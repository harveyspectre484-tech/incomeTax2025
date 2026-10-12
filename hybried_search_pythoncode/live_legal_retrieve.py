#!/usr/bin/env python3
"""Run hybrid retrieval, graph expansion, evidence selection, and grounded answering."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, ContextManager

try:
    from .discover_dependencies import DependencyDiscoverer
    from .expand_rerank import LegalGraph, expand_candidates, rerank
    from .select_final_evidence import exact_passage, gate_ranked_bundles, select_final_evidence
    from .grounded_answer import OpenAIDraftGenerator, answer_without_validation
except ImportError:
    from discover_dependencies import DependencyDiscoverer
    from expand_rerank import LegalGraph, expand_candidates, rerank
    from select_final_evidence import exact_passage, gate_ranked_bundles, select_final_evidence
    from grounded_answer import OpenAIDraftGenerator, answer_without_validation


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH_DIR = PROJECT_ROOT / "graph_data"
TraceFactory = Callable[[str, str, dict[str, Any]], ContextManager[Any]]
EVALUATION_CASES = [
    {"kind": "exact_citation", "question": "Explain Section 36(3)(a)(i)",
     "required_ids": ["36-3-a-i", "36-3-a-i-table"],
     "focus_ids": ["36", "36-3", "36-3-a", "36-3-a-i", "36-3-a-i-table",
                   "36-3-a-i-table-row-1", "36-3-a-i-table-row-2", "36-3-a-i-table-row-3",
                   "36-3-a-i-table-row-4", "36-3-a-i-table-row-5"]},
    {"kind": "bank_table", "question": "What deduction percentage is allowed for provision for bad debts of a scheduled bank?",
     "required_ids": ["31-1", "31-1-table", "31-1-table-row-1"],
     "focus_ids": ["31", "31-1", "31-1-table", "31-1-table-row-1"]},
    {"kind": "definition", "question": "What is a demerged company under Section 2(34)?",
     "required_ids": ["2-34"], "focus_ids": ["2", "2-34"]},
    {"kind": "cross_section_exception", "question": "Are marked to market losses deductible under Section 32(h), considering Section 36(9)?",
     "required_ids": ["32-h", "36-9"],
     "focus_ids": ["32", "32-h", "36", "36-9", "276", "276-2"]},
    {"kind": "schedule_row", "question": "When is armed forces disability pension excluded under Schedule III row 38A?",
     "required_ids": ["schedule-III-t38A", "schedule-III-table", "schedule-III-note12",
                      "schedule-III-note12-b", "schedule-III-footnote37"],
     "focus_ids": ["schedule-III", "schedule-III-table", "schedule-III-t38A",
                   "schedule-III-note12", "schedule-III-note12-b",
                   "schedule-III-footnote37", "schedule-III-footnote38"]},
]


def score_evaluation_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    actual_ids = {unit["id"] for unit in result["final_evidence"]["evidence_units"]}
    required = set(case["required_ids"])
    focus = set(case["focus_ids"])
    missing = sorted(required - actual_ids)
    return {
        "kind": case["kind"], "question": case["question"],
        "required_ids": case["required_ids"], "missing_ids": missing,
        "passed": not missing,
        "required_recall": len(required & actual_ids) / len(required),
        "focus_precision_proxy": len(focus & actual_ids) / len(actual_ids) if actual_ids else 0.0,
        "focus_ids": sorted(focus),
        "final_evidence_ids": sorted(actual_ids),
        "omitted_units": result["final_evidence"]["omitted_units"],
        "omitted_bundles": result["omitted_bundles"],
    }


def add_discovered_context(
    graph: LegalGraph, discoverer: DependencyDiscoverer,
    bundles: list[dict[str, Any]], question: str, max_bundle_chars: int,
) -> None:
    for bundle in bundles:
        found = discoverer.discover(bundle["anchor_id"], question)
        bundle["discovered_candidates"] = found
        included = set(bundle["source_ids"])
        for candidate in found:
            node_id = candidate["id"]
            candidate["evidence_included"] = node_id in included
            if node_id in included:
                continue
            passage = exact_passage(graph.nodes[node_id])
            if not passage or len(bundle["evidence_text"]) + len(passage) + 1 > max_bundle_chars:
                bundle["expansion_gaps"].append(f"{node_id}: discovered unit omitted by bundle budget")
                continue
            bundle["evidence_text"] += "\n" + passage
            bundle["source_ids"].append(node_id)
            included.add(node_id)
            candidate["evidence_included"] = True


def run_pipeline(
    question: str, retriever: Any, graph: LegalGraph,
    discoverer: DependencyDiscoverer, reranker: Any | None,
    *, filters: dict[str, str] | None = None,
    max_bundle_chars: int = 8000, max_context_chars: int = 16000,
    max_bundles: int = 10, max_score_drop: float | None = 1.0,
    trace_factory: TraceFactory | None = None,
) -> dict[str, Any]:
    def stage(name: str, run_type: str, inputs: dict[str, Any]) -> ContextManager[Any]:
        return trace_factory(name, run_type, inputs) if trace_factory else nullcontext(None)

    with stage("hybrid_retrieval", "retriever", {"question": question, "filters": filters or {}}) as run:
        hybrid = retriever.retrieve(question, filters=filters or {})
        if run is not None:
            run.end(outputs=hybrid)
    with stage("graph_expansion", "chain", {
        "question": question, "candidate_ids": [item["id"] for item in hybrid["candidates"]],
        "max_bundle_chars": max_bundle_chars,
    }) as run:
        bundles = expand_candidates(
            graph, hybrid["candidates"], question=question,
            max_evidence_chars=max_bundle_chars,
        )
        add_discovered_context(graph, discoverer, bundles, question, max_bundle_chars)
        if run is not None:
            run.end(outputs={"bundles": bundles})
    with stage("reranking", "chain", {
        "question": question, "bundle_anchor_ids": [item["anchor_id"] for item in bundles],
        "enabled": reranker is not None,
    }) as run:
        ranked = rerank(question, bundles, reranker) if reranker is not None else bundles
        if run is not None:
            run.end(outputs={"ranked_bundles": [
                {"anchor_id": item["anchor_id"], "rerank_score": item.get("rerank_score"),
                 "source_ids": item["source_ids"], "evidence_text": item["evidence_text"]}
                for item in ranked
            ]})
    with stage("evidence_selection", "chain", {
        "ranked_anchor_ids": [item["anchor_id"] for item in ranked],
        "max_bundles": max_bundles, "max_score_drop": max_score_drop,
        "max_context_chars": max_context_chars,
    }) as run:
        gated, omitted_bundles = gate_ranked_bundles(
            ranked, max_bundles=max_bundles,
            max_score_drop=max_score_drop if reranker is not None else None,
        )
        evidence = select_final_evidence(
            graph, gated, max_bundles=max_bundles, max_chars=max_context_chars,
            question=question,
        )
        if run is not None:
            run.end(outputs={"final_evidence": evidence, "omitted_bundles": omitted_bundles})
    return {
        "question": question,
        "filters": filters or {},
        "hybrid_candidate_ids": [item["id"] for item in hybrid["candidates"]],
        "ranked_bundles": [{
            "anchor_id": item["anchor_id"],
            "citation": item["citation"],
            "rerank_score": item.get("rerank_score"),
            "rrf_score": item["best_rrf_score"],
            "exact_citation_match": item["exact_citation_match"],
            "discovered_candidates": item["discovered_candidates"],
            "links": item["links"],
            "omitted_links": item["omitted_links"],
            "expansion_gaps": item["expansion_gaps"],
        } for item in gated],
        "omitted_bundles": omitted_bundles,
        "final_evidence": evidence,
    }


def run_question(
    question: str, retriever: Any, graph: LegalGraph,
    discoverer: DependencyDiscoverer, reranker: Any | None,
    *, answer_generator: Any | None = None,
    trace_factory: TraceFactory | None = None,
    filters: dict[str, str] | None = None,
    max_bundle_chars: int = 8000, max_context_chars: int = 16000,
    max_bundles: int = 10, max_score_drop: float | None = 1.0,
) -> dict[str, Any]:
    context = (
        trace_factory("legal_rag_question", "chain", {
            "question": question, "filters": filters or {},
            "answer_enabled": answer_generator is not None,
        }) if trace_factory else nullcontext(None)
    )
    with context as run:
        result = run_pipeline(
            question, retriever, graph, discoverer, reranker,
            filters=filters, max_bundle_chars=max_bundle_chars,
            max_context_chars=max_context_chars, max_bundles=max_bundles,
            max_score_drop=max_score_drop, trace_factory=trace_factory,
        )
        if answer_generator is not None:
            answer_context = (
                trace_factory("grounded_answer", "chain", {
                    "question": question,
                    "evidence": result["final_evidence"]["evidence_units"],
                }) if trace_factory else nullcontext(None)
            )
            with answer_context as answer_run:
                result["grounded_answer"] = answer_without_validation(
                    question, result["final_evidence"], answer_generator,
                )
                if answer_run is not None:
                    answer_run.end(outputs=result["grounded_answer"])
        if run is not None:
            run.end(outputs={
                "grounded_answer": result.get("grounded_answer"),
                "final_evidence_ids": [unit["id"] for unit in result["final_evidence"]["evidence_units"]],
                "omitted_units": result["final_evidence"]["omitted_units"],
            })
        return result


def memory_snapshot() -> dict[str, float | None]:
    snapshot: dict[str, float | None] = {"process_rss_mb": None, "mps_allocated_mb": None}
    try:
        import psutil
        snapshot["process_rss_mb"] = round(psutil.Process().memory_info().rss / 1024**2, 1)
    except ImportError:
        pass
    try:
        import torch
        if torch.backends.mps.is_available():
            snapshot["mps_allocated_mb"] = round(torch.mps.current_allocated_memory() / 1024**2, 1)
    except (ImportError, RuntimeError):
        pass
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--answer", action="store_true", help="Call the LLM on selected evidence")
    parser.add_argument("--answer-model", help="OpenAI model supporting structured output; required with --answer")
    parser.add_argument("--langsmith", action="store_true", help="Send question, evidence, and model traces to LangSmith")
    parser.add_argument("--langsmith-project", default=os.environ.get("LANGSMITH_PROJECT", "income-tax-2025-rag"))
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_GRAPH_DIR / "legal_act_bm25.sqlite")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default="income_tax_2025_units_v3")
    parser.add_argument("--reranker-model", default="Qwen/Qwen3-Reranker-0.6B")
    parser.add_argument("--reranker-device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--skip-rerank", action="store_true", help="Diagnostic mode")
    parser.add_argument("--max-bundle-chars", type=int, default=8000)
    parser.add_argument("--max-context-chars", type=int, default=16000)
    parser.add_argument("--max-bundles", type=int, default=10)
    parser.add_argument("--max-score-drop", type=float, default=1.0,
                        help="Keep reranked anchors within this score of the best anchor")
    parser.add_argument("--chapter")
    parser.add_argument("--section")
    parser.add_argument("--unit-type")
    parser.add_argument("--jurisdiction")
    parser.add_argument("--output", type=Path, help="Optional final result JSON")
    args = parser.parse_args()
    if args.answer and args.test:
        parser.error("--answer and --test cannot be combined; review answer quality separately")
    if args.answer and not args.answer_model:
        parser.error("--answer requires --answer-model")
    if args.langsmith and not os.environ.get("LANGSMITH_API_KEY"):
        parser.error("--langsmith requires LANGSMITH_API_KEY")
    if args.answer and not os.environ.get("OPENAI_API_KEY"):
        parser.error("--answer requires OPENAI_API_KEY")
    if min(args.max_bundle_chars, args.max_context_chars, args.max_bundles) < 1:
        parser.error("Evidence limits must be positive")
    if args.max_score_drop < 0:
        parser.error("--max-score-drop must be nonnegative")
    filters = {key: value for key, value in {
        "chapter": args.chapter, "section": args.section,
        "unit_type": args.unit_type, "jurisdiction": args.jurisdiction,
    }.items() if value is not None}

    from hybrid_retrieve import HybridRetriever

    graph = LegalGraph(args.graph_dir / "graph_nodes.jsonl", args.graph_dir / "graph_edges.jsonl")
    discoverer = DependencyDiscoverer(graph)
    print(f"Graph ready; memory={memory_snapshot()}", file=sys.stderr)
    retriever = HybridRetriever(
        db_path=args.db, qdrant_url=args.qdrant_url,
        collection=args.collection,
    )
    print(f"Embedder ready; memory={memory_snapshot()}", file=sys.stderr)
    reranker = None
    if not args.skip_rerank:
        import torch
        from sentence_transformers import CrossEncoder
        dtype = torch.float16 if args.reranker_device == "mps" else torch.float32
        reranker = CrossEncoder(
            args.reranker_model, device=args.reranker_device,
            model_kwargs={"torch_dtype": dtype},
        )
        print(f"Reranker ready; memory={memory_snapshot()}", file=sys.stderr)
    if args.langsmith:
        os.environ["LANGSMITH_TRACING"] = "true"
    answer_generator = OpenAIDraftGenerator(args.answer_model, trace_openai=args.langsmith) if args.answer else None
    trace_factory: TraceFactory | None = None
    trace_client = None
    if args.langsmith:
        from langsmith import Client, trace
        trace_client = Client()

        def trace_factory(name: str, run_type: str, inputs: dict[str, Any]) -> ContextManager[Any]:
            return trace(name, run_type=run_type, inputs=inputs,
                         project_name=args.langsmith_project, client=trace_client)

    cases = EVALUATION_CASES if args.test else None
    questions = [case["question"] for case in cases] if cases else [args.question] if args.question else None
    test_results: list[dict[str, Any]] = []
    try:
        while True:
            if questions is None:
                try:
                    question = input("> ").strip()
                except EOFError:
                    break
                if question.casefold() in {"quit", "exit"}:
                    break
                if not question:
                    continue
            else:
                if not questions:
                    break
                question = questions.pop(0)
            result = run_question(
                question, retriever, graph, discoverer, reranker,
                answer_generator=answer_generator, trace_factory=trace_factory,
                filters=filters, max_bundle_chars=args.max_bundle_chars,
                max_context_chars=args.max_context_chars,
                max_bundles=args.max_bundles,
                max_score_drop=args.max_score_drop,
            )
            if args.test:
                case = cases[len(test_results)]
                test_results.append(score_evaluation_case(case, result))
                print(json.dumps(test_results[-1], ensure_ascii=False))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"Memory after query={memory_snapshot()}", file=sys.stderr)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "cases": test_results,
                    "passed": all(case["passed"] for case in test_results),
                    "config": {"max_score_drop": args.max_score_drop,
                               "max_bundles": args.max_bundles,
                               "max_context_chars": args.max_context_chars},
                    "precision_note": "focus_precision_proxy is measured against a small engineering review set, not a legal gold standard",
                } if args.test else result
                args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        retriever.close()
        if trace_client is not None:
            trace_client.flush()


if __name__ == "__main__":
    main()
