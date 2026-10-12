#!/usr/bin/env python3
"""Expand hybrid hits through the legal graph, then rerank evidence bundles."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH_DIR = PROJECT_ROOT / "graph_data"
DEFAULT_MODEL = "Qwen/Qwen3-Reranker-0.6B"
REFERENCE_TYPES = {"references", "references_schedule"}
RESTRICTION_TERMS = re.compile(
    r"\b(unless|except|notwithstanding|subject to|shall not|does not apply|"
    r"not allowed|limited to|provided that|condition|restriction)\b", re.I,
)
WORD = re.compile(r"[a-z][a-z0-9]{2,}", re.I)
STOPWORDS = {"the", "and", "for", "from", "with", "that", "this", "section", "under", "shall", "which"}


def terms(value: str) -> set[str]:
    return {word for word in WORD.findall(value.lower()) if word not in STOPWORDS}


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"{path}:{line_number}: expected an object")
                yield record


def read_hybrid_results(path: Path) -> list[dict[str, Any]]:
    """Accept one result or several adjacent JSON objects from --test."""
    raw = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    results: list[dict[str, Any]] = []
    offset = 0
    while offset < len(raw):
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        if offset == len(raw):
            break
        result, offset = decoder.raw_decode(raw, offset)
        if not isinstance(result, dict) or not isinstance(result.get("candidates"), list):
            raise ValueError(f"{path}: expected hybrid retrieval result objects")
        results.append(result)
    if not results:
        raise ValueError(f"{path}: no hybrid results found")
    return results


class LegalGraph:
    def __init__(self, nodes_path: Path, edges_path: Path) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        for node in read_jsonl(nodes_path):
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id or node_id in self.nodes:
                raise ValueError(f"Invalid or duplicate graph node ID: {node_id!r}")
            self.nodes[node_id] = node

        self.parents: dict[str, str] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        self.outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.annotations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in read_jsonl(edges_path):
            if edge.get("resolution_status") != "resolved":
                continue
            source, target = edge.get("from_id"), edge.get("to_id")
            if source not in self.nodes or target not in self.nodes:
                raise ValueError(f"Resolved edge has missing endpoint: {source} -> {target}")
            if edge.get("edge_type") == "parent":
                if source in self.parents and self.parents[source] != target:
                    raise ValueError(f"Multiple parents for {source}")
                self.parents[source] = target
            elif edge.get("edge_type") == "contains" and target not in self.children[source]:
                self.children[source].append(target)
            elif edge.get("edge_type") in REFERENCE_TYPES:
                self.outgoing[source].append(edge)
                self.incoming[target].append(edge)
            elif edge.get("edge_type") == "annotates":
                self.annotations[target].append(edge)

        for child, parent in self.parents.items():
            if self.nodes[child].get("parent_id") != parent or child not in self.children[parent]:
                raise ValueError(f"Parent/contains edges disagree for {child}")

    def ancestry(self, node_id: str, max_depth: int = 16) -> list[dict[str, Any]]:
        if node_id not in self.nodes:
            raise ValueError(f"Candidate {node_id!r} has no graph node")
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        current = node_id
        while current and len(chain) < max_depth:
            if current in seen:
                raise ValueError(f"Structural cycle at {current}")
            seen.add(current)
            chain.append(self.nodes[current])
            if self.nodes[current].get("node_type") in {"section", "schedule"}:
                break
            current = self.parents.get(current)
        if current and chain[-1].get("node_type") not in {"section", "schedule"}:
            if self.parents.get(current):
                raise ValueError(f"Parent chain for {node_id} exceeds {max_depth} levels")
        return list(reversed(chain))

    def evidence_anchor(self, node_id: str) -> str:
        """A table-cell hit is scored with its complete row."""
        if node_id not in self.nodes:
            raise ValueError(f"Candidate {node_id!r} has no graph node")
        if self.nodes[node_id].get("node_type") != "table_cell_item":
            return node_id
        current = node_id
        while current in self.parents:
            current = self.parents[current]
            if self.nodes[current].get("node_type") == "table_row":
                return current
        return node_id

    def expand(
        self, anchor_id: str, max_table_rows: int = 8, question: str = "",
        max_outgoing: int = 3, max_incoming: int = 2,
        max_annotations: int = 2, max_evidence_chars: int = 8000,
        max_node_chars: int = 2500,
    ) -> dict[str, Any]:
        chain = self.ancestry(anchor_id)
        # Reserve the first part of a tight budget for the retrieved unit itself.
        evidence_nodes: list[tuple[dict[str, Any], str]] = [
            (chain[-1], "structural"),
            *((node, "structural") for node in chain[:-1]),
        ]
        anchor = self.nodes[anchor_id]
        omitted_table_rows: dict[str, int] = {}

        # Include a directly owned table only when all its rows fit the bound.
        # A retrieved row already carries its own values; its sibling rows are excluded.
        if anchor.get("node_type") not in {"table_row", "table_cell_item"}:
            for child_id in self.children.get(anchor_id, []):
                table = self.nodes[child_id]
                if table.get("node_type") != "table":
                    continue
                evidence_nodes.append((table, "table_context"))
                rows = [
                    self.nodes[row_id]
                    for row_id in self.children.get(child_id, [])
                    if self.nodes[row_id].get("node_type") == "table_row"
                ]
                if len(rows) <= max_table_rows:
                    evidence_nodes.extend((row, "table_context") for row in sorted(rows, key=self._row_sort_key))
                else:
                    omitted_table_rows[child_id] = len(rows)

        source_ids = [node["id"] for node in chain if str(node.get("text") or "").strip()]
        lookup_ids = [node["id"] for node in chain]
        query_terms = terms(question)
        links: list[dict[str, Any]] = []
        omitted_links: dict[str, int] = {}
        expansion_gaps: list[str] = []

        def add_link(edge: dict[str, Any], relation: str) -> None:
            target_id = edge["to_id"] if relation == "outgoing" else edge["from_id"]
            target = self.nodes[target_id]
            if target.get("is_placeholder"):
                return
            if relation == "outgoing" and target.get("node_type") == "schedule":
                expansion_gaps.append(f"{target_id}: Schedule root cited without a paragraph locator")
            linked_anchor = self.evidence_anchor(target_id)
            for node in self.ancestry(linked_anchor):
                evidence_nodes.append((node, relation))
            links.append({
                "relation": relation,
                "edge_type": edge["edge_type"],
                "from_id": edge["from_id"],
                "to_id": edge["to_id"],
                "reference_text": edge.get("reference_text"),
                "source_properties": edge.get("source_properties") or {},
            })

        for node_id in source_ids:
            notes = sorted(self.annotations.get(node_id, []), key=lambda edge: edge["from_id"])
            for edge in notes[:max_annotations]:
                add_link(edge, "annotation")
            omitted_links["annotation"] = omitted_links.get("annotation", 0) + max(0, len(notes) - max_annotations)

        outgoing = [edge for node_id in source_ids for edge in self.outgoing.get(node_id, [])]
        outgoing.sort(key=lambda edge: (
            -int(edge["from_id"] == anchor_id),
            -len(query_terms & terms(str(self.nodes[edge["to_id"]].get("text") or ""))),
            edge["to_id"],
        ))
        for edge in outgoing[:max_outgoing]:
            add_link(edge, "outgoing")
        omitted_links["outgoing"] = max(0, len(outgoing) - max_outgoing)

        incoming = [edge for node_id in lookup_ids for edge in self.incoming.get(node_id, [])]
        incoming = [edge for edge in incoming if edge["from_id"] not in lookup_ids]
        def incoming_score(edge: dict[str, Any]) -> tuple[int, int, int]:
            source = self.nodes[edge["from_id"]]
            text = str(source.get("text") or "")
            return (
                int(source.get("section_id") is not None and
                    source.get("section_id") == anchor.get("section_id")),
                int(bool(RESTRICTION_TERMS.search(text))),
                len(query_terms & terms(text)),
            )
        eligible_incoming = []
        for edge in incoming:
            props = edge.get("source_properties") or {}
            broad_range = props.get("reference_kind") == "range" and (
                props.get("range_member_count") or 0
            ) > 8
            broad_schedule_root = (
                edge["to_id"] != anchor_id
                and self.nodes[edge["to_id"]].get("node_type") == "schedule"
            )
            same_section, restrictive, overlap = incoming_score(edge)
            if not broad_range and not broad_schedule_root and (
                same_section or (restrictive and overlap >= 2)
            ):
                eligible_incoming.append(edge)
        omitted_links["incoming_unselected"] = len(incoming) - len(eligible_incoming)
        incoming = eligible_incoming
        incoming.sort(key=lambda edge: (
            tuple(-value for value in incoming_score(edge)), edge["from_id"],
        ))
        for edge in incoming[:max_incoming]:
            add_link(edge, "incoming_candidate")
        omitted_links["incoming_candidate"] = max(0, len(incoming) - max_incoming)

        parts: list[str] = []
        used_chars = 0
        included_ids: list[str] = []
        seen_ids: set[str] = set()
        truncated_ids: list[str] = []
        for node, relation in evidence_nodes:
            if node["id"] in seen_ids:
                continue
            seen_ids.add(node["id"])
            citation = str(node.get("citation") or node["id"])
            title = str(node.get("title") or "").strip()
            body = str(node.get("text") or "").strip()
            if node.get("node_type") == "table":
                columns = (node.get("source_properties") or {}).get("columns") or []
                if columns:
                    body = "Table columns: " + "; ".join(map(str, columns))
            if node.get("node_type") in {"section", "schedule"} and title:
                citation = f"{citation} — {title}"
            was_truncated = len(body) > max_node_chars
            if was_truncated:
                body = body[:max_node_chars].rsplit(" ", 1)[0] + " [truncated]"
            if body:
                passage = f"{citation}: {body}"
            elif node.get("node_type") in {"section", "schedule"}:
                passage = citation
            else:
                continue
            if used_chars + len(passage) + bool(parts) > max_evidence_chars:
                expansion_gaps.append("Evidence character budget reached; further nodes omitted")
                break
            parts.append(passage)
            used_chars += len(passage) + bool(parts[:-1])
            included_ids.append(node["id"])
            if was_truncated:
                truncated_ids.append(node["id"])

        for link in links:
            evidence_id = link["to_id"] if link["relation"] == "outgoing" else link["from_id"]
            link["evidence_included"] = self.evidence_anchor(evidence_id) in included_ids

        return {
            "anchor_id": anchor_id,
            "citation": anchor.get("citation"),
            "evidence_text": "\n".join(parts),
            "source_ids": included_ids,
            "omitted_table_rows": omitted_table_rows,
            "links": links,
            "omitted_links": omitted_links,
            "expansion_gaps": expansion_gaps,
            "truncated_source_ids": truncated_ids,
        }

    @staticmethod
    def _row_sort_key(row: dict[str, Any]) -> tuple[int, str]:
        number = str((row.get("source_properties") or {}).get("row_number") or "")
        return (int(number) if number.isdigit() else 10**9, row["id"])


def expand_candidates(
    graph: LegalGraph, candidates: list[dict[str, Any]], max_table_rows: int = 8,
    question: str = "", max_evidence_chars: int = 8000,
    max_outgoing: int = 3, max_incoming: int = 2, max_annotations: int = 2,
) -> list[dict[str, Any]]:
    bundles: dict[str, dict[str, Any]] = {}
    for position, candidate in enumerate(candidates, 1):
        hit_id = candidate["id"]
        anchor_id = graph.evidence_anchor(hit_id)
        if anchor_id not in bundles:
            bundles[anchor_id] = {
                **graph.expand(anchor_id, max_table_rows, question=question,
                               max_evidence_chars=max_evidence_chars,
                               max_outgoing=max_outgoing, max_incoming=max_incoming,
                               max_annotations=max_annotations),
                "retrieved_ids": [],
                "first_hybrid_rank": position,
                "best_rrf_score": float(candidate.get("rrf_score") or 0),
                "exact_citation_match": False,
            }
        bundle = bundles[anchor_id]
        bundle["retrieved_ids"].append(hit_id)
        bundle["best_rrf_score"] = max(
            bundle["best_rrf_score"], float(candidate.get("rrf_score") or 0)
        )
        citation_only_match = (
            question.strip().strip("\"' .?").casefold()
            == str(candidate.get("citation") or "").strip().casefold()
        )
        bundle["exact_citation_match"] |= bool(candidate.get("exact_citation_match")) or citation_only_match
    return list(bundles.values())


def rerank(
    question: str, bundles: list[dict[str, Any]], model: Any, batch_size: int = 1,
) -> list[dict[str, Any]]:
    if not bundles:
        return []
    pairs = [(question, bundle["evidence_text"]) for bundle in bundles]
    scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
    if len(scores) != len(bundles):
        raise ValueError("Reranker returned a different number of scores")
    scored = [{**bundle, "rerank_score": float(score)} for bundle, score in zip(bundles, scores)]
    return sorted(
        scored,
        key=lambda item: (
            -int(item["exact_citation_match"]),
            -item["rerank_score"],
            -item["best_rrf_score"],
            item["anchor_id"],
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Saved hybrid retrieval JSON")
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH_DIR)
    parser.add_argument("--output", type=Path, help="Optional full JSON result")
    parser.add_argument("--expand-only", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-table-rows", type=int, default=8)
    parser.add_argument("--max-evidence-chars", type=int, default=8000)
    parser.add_argument("--max-outgoing", type=int, default=3)
    parser.add_argument("--max-incoming", type=int, default=2)
    parser.add_argument("--max-annotations", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if min(args.batch_size, args.max_table_rows, args.max_evidence_chars, args.top_k) < 1:
        parser.error("batch size, maximum table rows, evidence size, and top-k must be positive")
    if min(args.max_outgoing, args.max_incoming, args.max_annotations) < 0:
        parser.error("edge limits cannot be negative")

    graph = LegalGraph(
        args.graph_dir / "graph_nodes.jsonl",
        args.graph_dir / "graph_edges.jsonl",
    )
    inputs = read_hybrid_results(args.input)
    model = None
    if not args.expand_only:
        import torch
        from sentence_transformers import CrossEncoder

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if device == "mps" else torch.float32
        model = CrossEncoder(args.model, device=device, model_kwargs={"torch_dtype": dtype})

    outputs: list[dict[str, Any]] = []
    for result in inputs:
        bundles = expand_candidates(graph, result["candidates"], args.max_table_rows,
                                    question=result["question"],
                                    max_evidence_chars=args.max_evidence_chars,
                                    max_outgoing=args.max_outgoing,
                                    max_incoming=args.max_incoming,
                                    max_annotations=args.max_annotations)
        ranked = rerank(result["question"], bundles, model, args.batch_size) if model else bundles
        outputs.append({"question": result["question"], "bundles": ranked})
        print(f"\n{result['question']} ({len(bundles)} evidence bundles)")
        for position, bundle in enumerate(ranked[:args.top_k], 1):
            score = f" rerank={bundle['rerank_score']:.4f}" if model else ""
            print(f"{position}. {bundle['citation']} [{bundle['anchor_id']}]{score}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(outputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Full evidence: {args.output}")


if __name__ == "__main__":
    main()
