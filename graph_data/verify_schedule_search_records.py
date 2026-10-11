#!/usr/bin/env python3
"""Verify canonical Schedule units across graph nodes and search documents."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


INDEXABLE_TYPES = {
    "paragraph", "subparagraph", "clause", "sub_clause", "sub_sub_clause",
    "sub_sub_sub_clause", "item", "sub_item", "table_row", "table_cell_item",
    "note", "footnote", "formula", "variable", "substitution",
}
NON_SUBSTANTIVE_CELL_VALUES = {"", "***", "-", "—", "–"}
IGNORED_UNIT_IDS = {"schedule-X-3-3-formula"}
REPORT_FIELDS = ("severity", "code", "unit_id", "source_file", "message")


def normalized(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            records.append(record)
    return records


def add_issue(
    issues: list[dict[str, str]], severity: str, code: str,
    unit_id: str, source_file: str, message: str,
) -> None:
    issues.append({
        "severity": severity,
        "code": code,
        "unit_id": unit_id,
        "source_file": source_file,
        "message": message,
    })


def schedule_metadata(record: dict[str, Any]) -> tuple[Any, Any, Any]:
    properties = record.get("source_properties") or {}
    return (
        record.get("schedule_id") or properties.get("schedule_id"),
        record.get("schedule_number") or properties.get("schedule_number"),
        record.get("schedule_title") or properties.get("schedule_title"),
    )


def should_index(unit: dict[str, Any]) -> bool:
    text = str(unit.get("text") or "").strip()
    unit_type = unit.get("unit_type")
    if not text or unit_type not in INDEXABLE_TYPES:
        return False
    return unit_type != "table_cell_item" or text not in NON_SUBSTANTIVE_CELL_VALUES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-dir", type=Path, required=True)
    parser.add_argument("--graph-nodes", type=Path, required=True)
    parser.add_argument("--search-documents", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    canonical: list[dict[str, Any]] = []
    source_by_id: dict[str, str] = {}
    for path in sorted(args.canonical_dir.glob("schedule*_can.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for unit in data.get("legal_units", []) or []:
            if isinstance(unit, dict) and unit.get("id"):
                canonical.append(unit)
                source_by_id.setdefault(str(unit["id"]), str(path))

    graph = [node for node in read_jsonl(args.graph_nodes)
             if str(node.get("id") or "").startswith("schedule-")]
    search = [doc for doc in read_jsonl(args.search_documents)
              if str(doc.get("id") or "").startswith("schedule-")]
    canonical_counts = Counter(str(unit["id"]) for unit in canonical)
    graph_counts = Counter(str(node["id"]) for node in graph)
    search_counts = Counter(str(doc["id"]) for doc in search)
    canonical_by_id = {str(unit["id"]): unit for unit in canonical}
    graph_by_id = {str(node["id"]): node for node in graph}
    search_by_id = {str(doc["id"]): doc for doc in search}
    issues: list[dict[str, str]] = []

    for unit_id, count in canonical_counts.items():
        if count != 1:
            add_issue(issues, "ERROR", "duplicate_canonical_id", unit_id,
                      source_by_id.get(unit_id, ""), f"Canonical ID occurs {count} times.")

    expected_search_ids: set[str] = set()
    for unit_id, unit in sorted(canonical_by_id.items()):
        source_file = source_by_id.get(unit_id, "")
        graph_node = graph_by_id.get(unit_id)
        if graph_counts[unit_id] != 1 or not graph_node:
            add_issue(issues, "ERROR", "missing_or_duplicate_graph_node", unit_id,
                      source_file, f"Expected one graph node; found {graph_counts[unit_id]}.")
            continue

        expected_metadata = (
            unit.get("schedule_id"), unit.get("schedule_number"), unit.get("schedule_title")
        )
        if schedule_metadata(graph_node) != expected_metadata:
            add_issue(issues, "ERROR", "graph_schedule_metadata_mismatch", unit_id,
                      source_file, "Graph Schedule metadata differs from canonical data.")
        if unit_id in IGNORED_UNIT_IDS:
            continue
        if normalized(graph_node.get("text")) != normalized(unit.get("text")):
            add_issue(issues, "ERROR", "graph_text_mismatch", unit_id,
                      source_file, "Graph text differs from canonical text.")

        if not should_index(unit):
            continue
        expected_search_ids.add(unit_id)
        document = search_by_id.get(unit_id)
        if search_counts[unit_id] != 1 or not document:
            add_issue(issues, "ERROR", "missing_or_duplicate_search_document", unit_id,
                      source_file, f"Expected one search document; found {search_counts[unit_id]}.")
            continue

        if document.get("parent_id") != unit.get("parent_id"):
            add_issue(issues, "ERROR", "search_parent_mismatch", unit_id,
                      source_file, "Search parent_id differs from canonical data.")
        if normalized(document.get("text")) != normalized(unit.get("text")):
            add_issue(issues, "ERROR", "search_text_mismatch", unit_id,
                      source_file, "Search text differs from canonical text.")
        if schedule_metadata(document) != expected_metadata:
            add_issue(issues, "ERROR", "search_schedule_metadata_mismatch", unit_id,
                      source_file, "Search Schedule metadata is missing or inconsistent.")

        retrieval = normalized(document.get("retrieval_text"))
        for field, label in (("citation", "citation"), ("schedule_title", "Schedule title"),
                             ("text", "exact unit text")):
            expected = normalized(unit.get(field))
            if expected and expected not in retrieval:
                add_issue(issues, "ERROR", f"retrieval_missing_{field}", unit_id,
                          source_file, f"retrieval_text is missing {label}.")

    canonical_ids = set(canonical_by_id)
    graph_ids = set(graph_by_id)
    search_ids = set(search_by_id)
    for unit_id in sorted(graph_ids - canonical_ids):
        add_issue(issues, "ERROR", "stale_graph_node", unit_id,
                  str(args.graph_nodes), "Schedule graph node has no canonical unit.")
    for unit_id in sorted(search_ids - expected_search_ids - IGNORED_UNIT_IDS):
        add_issue(issues, "ERROR", "unexpected_search_document", unit_id,
                  str(args.search_documents), "Schedule unit should not be indexed under current rules.")

    errors = sum(issue["severity"] == "ERROR" for issue in issues)
    summary = {
        "canonical_schedule_units": len(canonical_ids),
        "graph_schedule_units": len(graph_ids),
        "expected_search_documents": len(expected_search_ids),
        "actual_search_documents": len(search_ids),
        "ignored_unit_ids": sorted(IGNORED_UNIT_IDS),
        "issues": len(issues),
        "errors": errors,
        "passed": errors == 0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(issues)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Report: {args.report}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
