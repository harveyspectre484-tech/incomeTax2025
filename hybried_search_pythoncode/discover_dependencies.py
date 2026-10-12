#!/usr/bin/env python3
"""Evaluate conservative, scoped legal-dependency discovery on reviewed examples."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

try:
    from .expand_rerank import LegalGraph
except ImportError:
    from expand_rerank import LegalGraph


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH_DIR = PROJECT_ROOT / "graph_data"
DEFAULT_CASES = Path(__file__).with_name("discovery_cases_v1.json")
if not DEFAULT_CASES.exists():
    DEFAULT_CASES = PROJECT_ROOT / "data" / "discovery_cases_v1.json"
TERM_PATTERN = re.compile(r'["“]([^"”]{3,100})["”]\s+(?:shall\s+)?(?:mean|means|include|includes)\b', re.I)
SCOPE_PATTERN = re.compile(r"\b(in this paragraph|in this section|in this schedule|in this chapter)\b", re.I)
TOKEN = re.compile(r"[a-z0-9]{2,}", re.I)
STOPWORDS = {"the", "and", "for", "from", "with", "this", "that", "shall", "section", "schedule", "paragraph", "policy", "under", "what", "which"}


def tokens(text: str) -> set[str]:
    return {word for word in TOKEN.findall(text.casefold()) if word not in STOPWORDS}


def scope_id(graph: LegalGraph, node_id: str, scope: str) -> str | None:
    node = graph.nodes[node_id]
    if scope == "chapter":
        return node.get("chapter_id")
    wanted = {"paragraph": "paragraph", "section": "section", "schedule": "schedule"}[scope]
    for ancestor in reversed(graph.ancestry(node_id)):
        if ancestor.get("node_type") == wanted:
            return ancestor["id"]
    return None


class DependencyDiscoverer:
    def __init__(self, graph: LegalGraph) -> None:
        self.graph = graph
        self.definitions: list[tuple[str, str, str, str]] = []
        for node in graph.nodes.values():
            text = str(node.get("text") or "")
            scope_match = SCOPE_PATTERN.search(text)
            if not scope_match:
                continue
            scope = scope_match.group(1).split()[-1].casefold()
            owner = scope_id(graph, node["id"], scope)
            if not owner:
                continue
            for match in TERM_PATTERN.finditer(text):
                self.definitions.append((node["id"], match.group(1).casefold(), scope, owner))

    def discover(self, anchor_id: str, question: str, max_new: int = 6) -> list[dict[str, Any]]:
        graph = self.graph
        if anchor_id not in graph.nodes:
            raise ValueError(f"Unknown anchor: {anchor_id}")
        result: list[dict[str, Any]] = []
        seen = {node["id"] for node in graph.ancestry(anchor_id)}

        def propose(node_id: str, reason: str, support_id: str, scope: str | None = None) -> None:
            if node_id not in seen and len(result) < max_new and graph.nodes[node_id].get("text"):
                seen.add(node_id)
                result.append({"id": node_id, "reason": reason, "support_id": support_id, "scope": scope})

        # A condition list under a common "where" owner can govern the retrieved child.
        parent_id = graph.parents.get(anchor_id)
        parent = graph.nodes.get(parent_id or "", {})
        if "where" in str(parent.get("text") or "").casefold() and str(parent.get("text") or "").rstrip().endswith(("—", "--")):
            siblings = sorted(graph.children.get(parent_id, []))
            if len(siblings) <= 5:
                for sibling_id in siblings:
                    propose(sibling_id, "same_condition_list_candidate", parent_id, parent_id)

        # A trailing dash can introduce alternatives. Select just one when the
        # question uniquely matches that child's operative text.
        anchor_text = str(graph.nodes[anchor_id].get("text") or "").rstrip()
        if anchor_text.endswith(("—", "--")):
            children = [graph.nodes[child_id] for child_id in graph.children.get(anchor_id, [])]
            if 1 <= len(children) <= 5:
                query_terms = tokens(question)
                scores = sorted(
                    ((len(query_terms & tokens(str(child.get("text") or ""))), child["id"]) for child in children),
                    reverse=True,
                )
                if scores[0][0] > 0 and (len(scores) == 1 or scores[0][0] > scores[1][0]):
                    propose(scores[0][1], "matching_operative_child_candidate", anchor_id, anchor_id)

        # A cited empty paragraph is a pointer to its operative children, not evidence itself.
        source_ids = [node["id"] for node in graph.ancestry(anchor_id)]
        for source_id in source_ids:
            for edge in graph.outgoing.get(source_id, []):
                target_id = edge["to_id"]
                target = graph.nodes[target_id]
                if target.get("text") or target.get("node_type") not in {"paragraph", "subparagraph"}:
                    continue
                children = [graph.nodes[child_id] for child_id in graph.children.get(target_id, [])]
                children = [child for child in children if str(child.get("text") or "").strip()]
                if len(children) > 5:
                    continue
                query_terms = tokens(question)
                children.sort(key=lambda child: (-len(query_terms & tokens(str(child.get("text")))), child["id"]))
                for child in children[:2]:
                    propose(child["id"], "referenced_container_child_candidate", source_id, target_id)
                    grandchildren = [graph.nodes[child_id] for child_id in graph.children.get(child["id"], [])]
                    grandchildren = [item for item in grandchildren if str(item.get("text") or "").strip()]
                    if grandchildren and not TERM_PATTERN.search(str(child.get("text") or "")):
                        grandchildren.sort(key=lambda item: (-len(query_terms & tokens(str(item.get("text")))), item["id"]))
                        best = grandchildren[0]
                        if query_terms & tokens(str(best.get("text"))):
                            propose(best["id"], "referenced_condition_candidate", child["id"], target_id)

        # Use definitions only where an explicit local scope includes the term's usage.
        usage_ids = [anchor_id] + [item["id"] for item in result]
        usage_text = " ".join(str(graph.nodes[node_id].get("text") or "") for node_id in usage_ids)
        usage_text += " " + question
        for definition_id, term, scope, owner in self.definitions:
            if not re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", usage_text, re.I):
                continue
            if not any(scope_id(graph, node_id, scope) == owner for node_id in usage_ids):
                continue
            propose(definition_id, "scoped_definition_candidate", anchor_id, owner)
            definition = graph.nodes[definition_id]
            if str(definition.get("text") or "").rstrip().endswith(("--", "—")):
                children = sorted(graph.children.get(definition_id, []))
                if len(children) <= 3:
                    for child_id in children:
                        propose(child_id, "definition_continuation_candidate", definition_id, owner)
        return result


def evaluate(graph: LegalGraph, cases: list[dict[str, Any]]) -> dict[str, Any]:
    discoverer = DependencyDiscoverer(graph)
    rows: list[dict[str, Any]] = []
    for case in cases:
        anchor_id = case["anchor_id"]
        baseline_ids = set(graph.expand(anchor_id, question=case["question"])["source_ids"])
        proposed = discoverer.discover(anchor_id, case["question"])
        discovered_ids = {item["id"] for item in proposed}
        expected = set(case["expected_ids"])
        rows.append({
            "case_id": case["id"],
            "question": case["question"],
            "anchor_id": anchor_id,
            "baseline_ids": sorted(baseline_ids),
            "discovered": proposed,
            "baseline_missing_ids": sorted(expected - baseline_ids),
            "missing_ids": sorted(expected - baseline_ids - discovered_ids),
            "false_additions": sorted(discovered_ids - expected - baseline_ids),
            "expected_ids": sorted(expected),
            "review_note": case["review_note"],
        })
    return {
        "cases": rows,
        "summary": {
            "case_count": len(rows),
            "baseline_misses": sum(len(row["baseline_missing_ids"]) for row in rows),
            "remaining_misses": sum(len(row["missing_ids"]) for row in rows),
            "false_additions": sum(len(row["false_additions"]) for row in rows),
            "cases_fully_covered": sum(not row["missing_ids"] for row in rows),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH_DIR)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--csv-report", type=Path, required=True)
    args = parser.parse_args()
    graph = LegalGraph(args.graph_dir / "graph_nodes.jsonl", args.graph_dir / "graph_edges.jsonl")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Cases must be a list with unique IDs")
    report = evaluate(graph, cases)
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.csv_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with args.csv_report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("case_id", "anchor_id", "baseline_missing_ids", "missing_ids", "false_additions", "discovered_ids"))
        writer.writeheader()
        for row in report["cases"]:
            writer.writerow({
                "case_id": row["case_id"],
                "anchor_id": row["anchor_id"],
                "baseline_missing_ids": ";".join(row["baseline_missing_ids"]),
                "missing_ids": ";".join(row["missing_ids"]),
                "false_additions": ";".join(row["false_additions"]),
                "discovered_ids": ";".join(item["id"] for item in row["discovered"]),
            })
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
