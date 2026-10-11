#!/usr/bin/env python3
"""Run the complete Step 6 validation contract for canonical Schedules."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


INDEXABLE_TYPES = {
    "paragraph", "subparagraph", "clause", "sub_clause", "sub_sub_clause",
    "sub_sub_sub_clause", "item", "sub_item", "table_row", "table_cell_item",
    "note", "footnote", "formula", "variable", "substitution",
}
NON_SUBSTANTIVE_CELL_VALUES = {"", "***", "-", "—", "–"}
IGNORED_FORMULA_IDS: set[str] = set()
REPORT_FIELDS = ("severity", "check", "code", "unit_id", "source_file", "message")


def normalize(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


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


def metadata(record: dict[str, Any]) -> tuple[str, str, str]:
    properties = record.get("source_properties") or {}
    return (
        str(record.get("schedule_id") or properties.get("schedule_id") or ""),
        str(record.get("schedule_number") or properties.get("schedule_number") or ""),
        str(record.get("schedule_title") or properties.get("schedule_title") or ""),
    )


def should_index(unit: dict[str, Any]) -> bool:
    unit_id = str(unit.get("id") or "")
    text = str(unit.get("text") or "").strip()
    unit_type = str(unit.get("unit_type") or "")
    if unit_id in IGNORED_FORMULA_IDS:
        return False
    if unit_type not in INDEXABLE_TYPES or not text:
        return False
    return unit_type != "table_cell_item" or text not in NON_SUBSTANTIVE_CELL_VALUES


def add_issue(
    issues: list[dict[str, str]], check: int, code: str, unit_id: str,
    source_file: str, message: str, severity: str = "ERROR",
) -> None:
    issues.append({
        "severity": severity,
        "check": str(check),
        "code": code,
        "unit_id": unit_id,
        "source_file": source_file,
        "message": message,
    })


def require_in_text(
    issues: list[dict[str, str]], check: int, code: str, unit_id: str,
    source_file: str, container: Any, expected: Any, label: str,
) -> None:
    expected_text = normalize(expected)
    if expected_text and expected_text not in normalize(container):
        add_issue(issues, check, code, unit_id, source_file, f"Missing {label}: {expected!r}")


def load_canonical(directory: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    units: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    for path in sorted(directory.glob("schedule*_can.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for unit in data.get("legal_units", []) or []:
            if isinstance(unit, dict) and unit.get("id"):
                units.append(unit)
                sources.setdefault(str(unit["id"]), str(path))
    return units, sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-dir", type=Path, required=True)
    parser.add_argument("--graph-nodes", type=Path, required=True)
    parser.add_argument("--graph-edges", type=Path, required=True)
    parser.add_argument("--pending-edges", type=Path, required=True)
    parser.add_argument("--search-documents", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    canonical, source_by_id = load_canonical(args.canonical_dir)
    graph_all = read_jsonl(args.graph_nodes)
    edges = read_jsonl(args.graph_edges)
    pending_edges = read_jsonl(args.pending_edges)
    search_all = read_jsonl(args.search_documents)

    canonical_counts = Counter(str(unit["id"]) for unit in canonical)
    graph_counts = Counter(str(node.get("id") or "") for node in graph_all)
    search_counts = Counter(str(doc.get("id") or "") for doc in search_all)
    canonical_by_id = {str(unit["id"]): unit for unit in canonical}
    graph_by_id = {str(node["id"]): node for node in graph_all if node.get("id")}
    search_by_id = {str(doc["id"]): doc for doc in search_all if doc.get("id")}
    canonical_ids = set(canonical_by_id)
    schedule_graph_ids = {node_id for node_id in graph_by_id if node_id.startswith("schedule-")}
    schedule_search_ids = {doc_id for doc_id in search_by_id if doc_id.startswith("schedule-")}
    issues: list[dict[str, str]] = []

    # Check 1: canonical IDs occur once and map to one graph node.
    for unit_id, count in sorted(canonical_counts.items()):
        source = source_by_id.get(unit_id, "")
        if count != 1:
            add_issue(issues, 1, "duplicate_canonical_id", unit_id, source,
                      f"Canonical ID occurs {count} times.")
        if graph_counts[unit_id] != 1:
            add_issue(issues, 1, "missing_or_duplicate_graph_node", unit_id, source,
                      f"Expected one graph node; found {graph_counts[unit_id]}.")
    for unit_id in sorted(schedule_graph_ids - canonical_ids):
        add_issue(issues, 1, "stale_schedule_graph_node", unit_id, str(args.graph_nodes),
                  "Schedule graph node has no canonical Schedule unit.")

    # Check 2: every substantive unit occurs once in the search index.
    expected_search_ids = {unit_id for unit_id, unit in canonical_by_id.items() if should_index(unit)}
    for unit_id in sorted(expected_search_ids):
        unit = canonical_by_id[unit_id]
        source = source_by_id.get(unit_id, "")
        if search_counts[unit_id] != 1:
            add_issue(issues, 2, "missing_or_duplicate_search_document", unit_id, source,
                      f"Expected one search document; found {search_counts[unit_id]}.")
            continue
        document = search_by_id[unit_id]
        if document.get("parent_id") != unit.get("parent_id"):
            add_issue(issues, 2, "search_parent_mismatch", unit_id, source,
                      "Search parent_id differs from canonical data.")
        if normalize(document.get("text")) != normalize(unit.get("text")):
            add_issue(issues, 2, "search_text_mismatch", unit_id, source,
                      "Search text differs from canonical text.")
        retrieval = document.get("retrieval_text")
        require_in_text(issues, 2, "retrieval_missing_citation", unit_id, source,
                        retrieval, unit.get("citation"), "citation")
        require_in_text(issues, 2, "retrieval_missing_schedule_title", unit_id, source,
                        retrieval, unit.get("schedule_title"), "Schedule title")
        require_in_text(issues, 2, "retrieval_missing_unit_text", unit_id, source,
                        retrieval, unit.get("text"), "exact unit text")
    for unit_id in sorted(schedule_search_ids - expected_search_ids - IGNORED_FORMULA_IDS):
        add_issue(issues, 2, "unexpected_schedule_search_document", unit_id,
                  str(args.search_documents), "Schedule unit is not indexable under current rules.")

    # Index structural edge occurrences once for parent/contains checks.
    parent_edges: Counter[tuple[str, str]] = Counter()
    contains_edges: Counter[tuple[str, str]] = Counter()
    for edge in edges:
        if edge.get("resolution_status") != "resolved":
            continue
        edge_type = edge.get("edge_type")
        pair = (str(edge.get("from_id") or ""), str(edge.get("to_id") or ""))
        if edge_type == "parent":
            parent_edges[pair] += 1
        elif edge_type == "contains":
            contains_edges[pair] += 1

    # Check 3: parent IDs exist, agree with graph links and terminate at the correct root.
    for unit_id, unit in sorted(canonical_by_id.items()):
        source = source_by_id.get(unit_id, "")
        schedule_id = str(unit.get("schedule_id") or "")
        parent_id = unit.get("parent_id")
        graph_node = graph_by_id.get(unit_id)
        if unit_id == schedule_id:
            if unit.get("unit_type") != "schedule" or parent_id not in (None, ""):
                add_issue(issues, 3, "invalid_schedule_root", unit_id, source,
                          "Schedule root must have unit_type='schedule' and parent_id=null.")
            if any(child == unit_id for child, _ in parent_edges):
                add_issue(issues, 3, "root_has_parent_edge", unit_id, source,
                          "Schedule root must not have a parent edge.")
            continue
        if not parent_id or parent_id not in canonical_by_id:
            add_issue(issues, 3, "missing_structural_parent", unit_id, source,
                      f"Parent {parent_id!r} is absent from canonical Schedule units.")
            continue
        if graph_node and graph_node.get("parent_id") != parent_id:
            add_issue(issues, 3, "graph_parent_mismatch", unit_id, source,
                      f"Graph parent {graph_node.get('parent_id')!r} differs from {parent_id!r}.")
        if parent_edges[(unit_id, str(parent_id))] != 1:
            add_issue(issues, 3, "invalid_parent_edge_count", unit_id, source,
                      f"Expected one parent edge to {parent_id!r}; found {parent_edges[(unit_id, str(parent_id))]}.")
        if contains_edges[(str(parent_id), unit_id)] != 1:
            add_issue(issues, 3, "invalid_contains_edge_count", unit_id, source,
                      f"Expected one inverse contains edge; found {contains_edges[(str(parent_id), unit_id)]}.")

        seen = {unit_id}
        cursor = unit
        while cursor.get("parent_id"):
            next_id = str(cursor["parent_id"])
            if next_id in seen:
                add_issue(issues, 3, "schedule_parent_cycle", unit_id, source,
                          f"Structural cycle reaches {next_id!r}.")
                break
            seen.add(next_id)
            parent = canonical_by_id.get(next_id)
            if not parent:
                break
            if str(parent.get("schedule_id") or "") != schedule_id:
                add_issue(issues, 3, "parent_crosses_schedule", unit_id, source,
                          f"Parent path crosses from {schedule_id!r} to {parent.get('schedule_id')!r}.")
                break
            cursor = parent
        else:
            if str(cursor.get("id") or "") != schedule_id:
                add_issue(issues, 3, "wrong_schedule_root", unit_id, source,
                          f"Parent path ends at {cursor.get('id')!r}, expected {schedule_id!r}.")

    # Check 4: Schedule identity metadata remains consistent in canonical, graph and search stores.
    for unit_id, unit in sorted(canonical_by_id.items()):
        source = source_by_id.get(unit_id, "")
        expected = metadata(unit)
        if not all(expected):
            add_issue(issues, 4, "incomplete_canonical_schedule_metadata", unit_id, source,
                      f"Expected nonempty schedule_id, schedule_number and schedule_title; found {expected!r}.")
        graph_node = graph_by_id.get(unit_id)
        if graph_node and metadata(graph_node) != expected:
            add_issue(issues, 4, "graph_schedule_metadata_mismatch", unit_id, source,
                      f"Graph metadata {metadata(graph_node)!r} differs from {expected!r}.")
        document = search_by_id.get(unit_id)
        if unit_id in expected_search_ids and document and metadata(document) != expected:
            add_issue(issues, 4, "search_schedule_metadata_mismatch", unit_id, source,
                      f"Search metadata {metadata(document)!r} differs from {expected!r}.")

    # Check 5: every row has a table, headers, row number and complete searchable row text.
    table_rows = [unit for unit in canonical if unit.get("unit_type") == "table_row"]
    for row in table_rows:
        row_id = str(row["id"])
        source = source_by_id.get(row_id, "")
        table_id = str(row.get("parent_id") or "")
        table = canonical_by_id.get(table_id)
        if not table or table.get("unit_type") != "table":
            add_issue(issues, 5, "invalid_table_parent", row_id, source,
                      f"Parent {table_id!r} is not a canonical table.")
            continue
        columns = table.get("columns") or []
        if not columns:
            add_issue(issues, 5, "table_has_no_columns", table_id,
                      source_by_id.get(table_id, source), "Canonical table has no column headers.")
        row_number = row.get("row_number")
        if row_number in (None, ""):
            add_issue(issues, 5, "row_number_missing", row_id, source, "Table row has no row_number.")
        if not normalize(row.get("text")):
            add_issue(issues, 5, "row_values_missing", row_id, source, "Table row has no linearized cell values.")
        document = search_by_id.get(row_id)
        if not document:
            continue
        retrieval = document.get("retrieval_text")
        for column in columns:
            require_in_text(issues, 5, "row_retrieval_missing_header", row_id, source,
                            retrieval, column, "column header")
        require_in_text(issues, 5, "row_retrieval_missing_number", row_id, source,
                        retrieval, row_number, "row number")
        require_in_text(issues, 5, "row_retrieval_missing_values", row_id, source,
                        retrieval, row.get("text"), "row cell values")

    # Check 6: formulas and variables retain exact text and valid ownership.
    formulas = [unit for unit in canonical if unit.get("unit_type") == "formula"]
    variables = [unit for unit in canonical if unit.get("unit_type") == "variable"]
    for formula in formulas:
        formula_id = str(formula["id"])
        source = source_by_id.get(formula_id, "")
        if formula_id in IGNORED_FORMULA_IDS:
            continue
        if not normalize(formula.get("text")):
            add_issue(issues, 6, "formula_expression_missing", formula_id, source,
                      "Canonical formula has no expression text.")
        graph_node = graph_by_id.get(formula_id)
        if graph_node and normalize(graph_node.get("text")) != normalize(formula.get("text")):
            add_issue(issues, 6, "graph_formula_mismatch", formula_id, source,
                      "Graph formula expression differs from canonical text.")
        document = search_by_id.get(formula_id)
        if document and normalize(document.get("text")) != normalize(formula.get("text")):
            add_issue(issues, 6, "search_formula_mismatch", formula_id, source,
                      "Search formula expression differs from canonical text.")
    for variable in variables:
        variable_id = str(variable["id"])
        source = source_by_id.get(variable_id, "")
        parent = canonical_by_id.get(str(variable.get("parent_id") or ""))
        if not parent or parent.get("unit_type") != "formula":
            add_issue(issues, 6, "variable_parent_not_formula", variable_id, source,
                      f"Variable parent {variable.get('parent_id')!r} is not a formula.")
        if not normalize(variable.get("text")):
            add_issue(issues, 6, "variable_definition_missing", variable_id, source,
                      "Variable has no definition text.")
        graph_node = graph_by_id.get(variable_id)
        if graph_node and normalize(graph_node.get("text")) != normalize(variable.get("text")):
            add_issue(issues, 6, "graph_variable_mismatch", variable_id, source,
                      "Graph variable definition differs from canonical text.")
        document = search_by_id.get(variable_id)
        if document and normalize(document.get("text")) != normalize(variable.get("text")):
            add_issue(issues, 6, "search_variable_mismatch", variable_id, source,
                      "Search variable definition differs from canonical text.")

    # Check 7: all footnotes annotate at least one existing target; every annotation is valid.
    annotate_by_source: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge.get("edge_type") != "annotates":
            continue
        source_id = str(edge.get("from_id") or "")
        target_id = str(edge.get("to_id") or "")
        if not source_id.startswith("schedule-"):
            continue
        annotate_by_source[source_id].append(edge)
        source_node = graph_by_id.get(source_id)
        target_node = graph_by_id.get(target_id)
        if not source_node or source_node.get("node_type") not in {"note", "footnote"}:
            add_issue(issues, 7, "invalid_annotation_source", source_id, str(args.graph_edges),
                      "Annotation source is absent or is not a note/footnote.")
        if not target_node:
            add_issue(issues, 7, "missing_annotation_target", source_id, str(args.graph_edges),
                      f"Annotation target {target_id!r} is absent.")
        elif metadata(source_node)[0] != metadata(target_node)[0]:
            add_issue(issues, 7, "annotation_crosses_schedule", source_id, str(args.graph_edges),
                      f"Annotation target {target_id!r} belongs to a different Schedule.")
        if edge.get("resolution_status") != "resolved":
            add_issue(issues, 7, "annotation_not_resolved", source_id, str(args.graph_edges),
                      f"Annotation to {target_id!r} is not resolved.")
    for footnote in (unit for unit in canonical if unit.get("unit_type") == "footnote"):
        footnote_id = str(footnote["id"])
        if not annotate_by_source[footnote_id]:
            add_issue(issues, 7, "footnote_has_no_annotation", footnote_id,
                      source_by_id.get(footnote_id, ""), "Footnote has no annotates edge.")
    for edge in pending_edges:
        if edge.get("edge_type") == "annotates" and str(edge.get("from_id") or "").startswith("schedule-"):
            add_issue(issues, 7, "pending_schedule_annotation", str(edge.get("from_id") or ""),
                      str(args.pending_edges), f"Pending annotation target: {edge.get('to_id')!r}.")

    # Check 8: every resolved references_schedule edge has existing verified endpoints.
    resolved_schedule_references = 0
    for edge in edges:
        if edge.get("edge_type") != "references_schedule":
            continue
        source_id = str(edge.get("from_id") or "")
        target_id = str(edge.get("to_id") or "")
        resolved_schedule_references += 1
        if edge.get("resolution_status") != "resolved":
            add_issue(issues, 8, "schedule_reference_not_resolved", source_id,
                      str(args.graph_edges), f"Reference to {target_id!r} is not resolved.")
        if source_id not in graph_by_id:
            add_issue(issues, 8, "missing_schedule_reference_source", source_id,
                      str(args.graph_edges), "Schedule-reference source node is absent.")
        target = graph_by_id.get(target_id)
        if not target or target_id not in canonical_ids:
            add_issue(issues, 8, "missing_schedule_reference_target", source_id,
                      str(args.graph_edges), f"Schedule target {target_id!r} is absent from canonical graph nodes.")
            continue
        expected_roman = str((edge.get("source_properties") or {}).get("schedule_number_roman") or "")
        if expected_roman and metadata(target)[1] != expected_roman:
            add_issue(issues, 8, "schedule_reference_number_mismatch", source_id,
                      str(args.graph_edges),
                      f"Reference says Schedule {expected_roman}, target belongs to Schedule {metadata(target)[1]}.")
    pending_schedule_references = [
        edge for edge in pending_edges if edge.get("edge_type") == "references_schedule"
    ]
    for edge in pending_schedule_references:
        add_issue(issues, 8, "pending_schedule_reference", str(edge.get("from_id") or ""),
                  str(args.pending_edges), f"Pending Schedule target: {edge.get('to_id')!r}.")

    check_errors = {
        str(check): sum(issue["severity"] == "ERROR" and issue["check"] == str(check) for issue in issues)
        for check in range(1, 9)
    }
    errors = sum(issue["severity"] == "ERROR" for issue in issues)
    summary = {
        "canonical_schedule_units": len(canonical_ids),
        "graph_schedule_units": len(schedule_graph_ids),
        "expected_search_documents": len(expected_search_ids),
        "actual_search_documents": len(schedule_search_ids),
        "schedule_table_rows": len(table_rows),
        "formulas": len(formulas),
        "variables": len(variables),
        "footnotes": sum(unit.get("unit_type") == "footnote" for unit in canonical),
        "resolved_annotation_edges": sum(len(value) for value in annotate_by_source.values()),
        "resolved_schedule_reference_edges": resolved_schedule_references,
        "pending_schedule_reference_edges": len(pending_schedule_references),
        "ignored_formula_ids": sorted(IGNORED_FORMULA_IDS),
        "errors_by_check": check_errors,
        "issues": len(issues),
        "errors": errors,
        "passed": errors == 0,
    }
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
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
