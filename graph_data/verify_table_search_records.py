#!/usr/bin/env python3
"""Verify canonical table rows across canonical JSON, graph nodes and search docs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPORT_FIELDS = ("severity", "code", "row_id", "source_file", "message")


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split()).casefold()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            records.append(record)
    return records


def canonical_files(directories: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        files.extend(sorted(directory.glob("*section_can.json")))
        files.extend(sorted(directory.glob("schedule*_can.json")))
    return sorted(set(files))


def load_canonical_units(directories: list[Path]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    units: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    for path in canonical_files(directories):
        data = json.loads(path.read_text(encoding="utf-8"))
        for unit in data.get("legal_units", []) or []:
            if not isinstance(unit, dict):
                continue
            copied = dict(unit)
            copied["_canonical_file"] = str(path)
            units.append(copied)
            if unit.get("id"):
                sources.setdefault(str(unit["id"]), str(path))
    return units, sources


def add_issue(
    issues: list[dict[str, str]],
    severity: str,
    code: str,
    row_id: str,
    source_file: str,
    message: str,
) -> None:
    issues.append({
        "severity": severity,
        "code": code,
        "row_id": row_id,
        "source_file": source_file,
        "message": message,
    })


def require_in_retrieval(
    issues: list[dict[str, str]],
    row_id: str,
    source_file: str,
    retrieval_text: str,
    expected: Any,
    code: str,
    label: str,
) -> None:
    expected_text = normalize(expected)
    if expected_text and expected_text not in normalize(retrieval_text):
        add_issue(issues, "ERROR", code, row_id, source_file, f"retrieval_text is missing {label}: {expected!r}")


def verify(
    canonical_dirs: list[Path],
    graph_nodes_path: Path,
    search_documents_path: Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    issues: list[dict[str, str]] = []
    canonical_units, canonical_sources = load_canonical_units(canonical_dirs)
    graph_nodes = read_jsonl(graph_nodes_path)
    search_documents = read_jsonl(search_documents_path)

    canonical_counts = Counter(str(unit.get("id") or "") for unit in canonical_units)
    graph_counts = Counter(str(node.get("id") or "") for node in graph_nodes)
    search_counts = Counter(str(doc.get("id") or "") for doc in search_documents)

    canonical_by_id = {str(unit["id"]): unit for unit in canonical_units if unit.get("id")}
    graph_by_id = {str(node["id"]): node for node in graph_nodes if node.get("id")}
    search_by_id = {str(doc["id"]): doc for doc in search_documents if doc.get("id")}
    canonical_rows = {
        unit_id: unit
        for unit_id, unit in canonical_by_id.items()
        if unit.get("unit_type") == "table_row"
    }

    for duplicate_id, count in sorted(canonical_counts.items()):
        if duplicate_id and count > 1:
            add_issue(issues, "ERROR", "duplicate_canonical_id", duplicate_id,
                      canonical_sources.get(duplicate_id, ""), f"Canonical ID occurs {count} times.")
    for duplicate_id, count in sorted(graph_counts.items()):
        if duplicate_id and count > 1:
            add_issue(issues, "ERROR", "duplicate_graph_id", duplicate_id,
                      str(graph_nodes_path), f"Graph ID occurs {count} times.")
    for duplicate_id, count in sorted(search_counts.items()):
        if duplicate_id and count > 1:
            add_issue(issues, "ERROR", "duplicate_search_id", duplicate_id,
                      str(search_documents_path), f"Search-document ID occurs {count} times.")

    for row_id, row in sorted(canonical_rows.items()):
        source_file = str(row.get("_canonical_file") or "")
        table_id = str(row.get("parent_id") or "")
        table = canonical_by_id.get(table_id)
        if not table or table.get("unit_type") != "table":
            add_issue(issues, "ERROR", "invalid_canonical_table_parent", row_id, source_file,
                      f"parent_id {table_id!r} is not a canonical table.")
            continue

        graph_node = graph_by_id.get(row_id)
        if graph_counts[row_id] != 1 or not graph_node:
            add_issue(issues, "ERROR", "missing_or_duplicate_graph_row", row_id, source_file,
                      f"Expected exactly one graph node; found {graph_counts[row_id]}.")
        else:
            if graph_node.get("node_type") != "table_row":
                add_issue(issues, "ERROR", "wrong_graph_node_type", row_id, source_file,
                          f"Graph node type is {graph_node.get('node_type')!r}.")
            if graph_node.get("parent_id") != table_id:
                add_issue(issues, "ERROR", "graph_parent_mismatch", row_id, source_file,
                          f"Graph parent is {graph_node.get('parent_id')!r}; expected {table_id!r}.")
            if graph_node.get("text") != row.get("text"):
                add_issue(issues, "ERROR", "graph_text_stale", row_id, source_file,
                          "Graph row text differs from the canonical row text; rebuild graph nodes.")

        document = search_by_id.get(row_id)
        if search_counts[row_id] != 1 or not document:
            add_issue(issues, "ERROR", "missing_or_duplicate_search_row", row_id, source_file,
                      f"Expected exactly one search document; found {search_counts[row_id]}.")
            continue

        if document.get("unit_type") != "table_row":
            add_issue(issues, "ERROR", "wrong_search_unit_type", row_id, source_file,
                      f"Search unit type is {document.get('unit_type')!r}.")
        if document.get("parent_id") != table_id:
            add_issue(issues, "ERROR", "search_parent_mismatch", row_id, source_file,
                      f"Search parent is {document.get('parent_id')!r}; expected {table_id!r}.")
        if document.get("citation") != row.get("citation"):
            add_issue(issues, "ERROR", "search_citation_mismatch", row_id, source_file,
                      "Search citation differs from the canonical citation.")
        if document.get("text") != row.get("text"):
            add_issue(issues, "ERROR", "search_text_mismatch", row_id, source_file,
                      "Search text differs from the exact canonical row text.")

        retrieval_text = str(document.get("retrieval_text") or "")
        require_in_retrieval(issues, row_id, source_file, retrieval_text,
                             row.get("citation"), "missing_row_citation", "row citation")
        require_in_retrieval(issues, row_id, source_file, retrieval_text,
                             row.get("title"), "missing_row_title", "row title")
        require_in_retrieval(issues, row_id, source_file, retrieval_text,
                             row.get("text"), "missing_row_values", "exact row text/cell values")
        require_in_retrieval(issues, row_id, source_file, retrieval_text,
                             table.get("citation"), "missing_table_citation", "table citation")

        row_number = row.get("row_number")
        if row_number not in (None, ""):
            require_in_retrieval(issues, row_id, source_file, retrieval_text,
                                 str(row_number), "missing_row_number", "row number")
        for header in table.get("columns", []) or []:
            require_in_retrieval(issues, row_id, source_file, retrieval_text,
                                 header, "missing_column_header", "column header")

    canonical_row_ids = set(canonical_rows)
    graph_row_ids = {str(node["id"]) for node in graph_nodes if node.get("node_type") == "table_row" and node.get("id")}
    search_row_ids = {str(doc["id"]) for doc in search_documents if doc.get("unit_type") == "table_row" and doc.get("id")}
    for extra_id in sorted(graph_row_ids - canonical_row_ids):
        add_issue(issues, "ERROR", "stale_graph_table_row", extra_id, str(graph_nodes_path),
                  "Graph table row is absent from current canonical data.")
    for extra_id in sorted(search_row_ids - canonical_row_ids):
        add_issue(issues, "ERROR", "stale_search_table_row", extra_id, str(search_documents_path),
                  "Search table row is absent from current canonical data.")

    error_count = sum(issue["severity"] == "ERROR" for issue in issues)
    summary = {
        "canonical_table_rows": len(canonical_row_ids),
        "graph_table_rows": len(graph_row_ids),
        "search_table_rows": len(search_row_ids),
        "issues": len(issues),
        "errors": error_count,
        "passed": error_count == 0,
    }
    return issues, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-dir", type=Path, action="append", required=True,
                        help="Repeat for section and Schedule canonical directories.")
    parser.add_argument("--graph-nodes", type=Path, required=True)
    parser.add_argument("--search-documents", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    issues, summary = verify(args.canonical_dir, args.graph_nodes, args.search_documents)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(issues)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Report: {args.report}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
