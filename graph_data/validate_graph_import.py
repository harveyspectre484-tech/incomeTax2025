from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

if __package__:
    from .validate_graph_parents import validate_parents
else:
    from validate_graph_parents import validate_parents


NODE_TYPES = {
    "section", "subsection", "clause", "sub_clause", "sub_sub_clause", "sub_sub_sub_clause",
    "sub_clauses", "item", "sub_item", "condition", "operative_clause", "where_block",
    "exclusion_block", "explanation_block", "exception_block", "longline", "longline_clause", "table",
    "table_row", "table_cell_item", "schedule", "part", "paragraph", "subparagraph",
    "note", "footnote", "formula", "variable", "substitution", "chapter",
    "chapter_division", "chapter_subdivision",
}
EDGE_TYPES = {"parent", "contains", "references", "references_schedule", "annotates"}
EXTERNAL_EDGE_TYPES = {"references_external", "references_external_act"}
HIERARCHY_FIELDS = (
    "chapter_id", "chapter_number", "chapter_title", "division_id", "division_number",
    "division_title", "subdivision_id", "subdivision_number", "subdivision_title",
    "section_id", "section_number",
)
NODE_REQUIRED = ("id", "node_type", "citation")
EDGE_REQUIRED = ("from_id", "to_id", "edge_type")
NODE_EXPOSED = HIERARCHY_FIELDS + ("source_file", "page_start", "page_end", "line_start", "x", "y_start")
EDGE_EXPOSED = ("reference_text", "source_file", "resolution_status", "resolution_basis")
REPORT_FIELDS = ("severity", "code", "record_id", "source_file", "message")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def count_raw_tables(value: Any) -> int:
    if isinstance(value, list):
        return sum(count_raw_tables(item) for item in value)
    if isinstance(value, dict):
        return int(value.get("type") == "table") + sum(count_raw_tables(item) for item in value.values())
    return 0


def source_table_gaps(raw_dir: Path, canonical_dir: Path) -> list[dict[str, str]]:
    gaps = []
    for raw_path in sorted(raw_dir.glob("*section.json")):
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_count = count_raw_tables(raw)
        if not raw_count:
            continue
        canonical_path = canonical_dir / raw_path.name.replace("section.json", "section_can.json")
        if not canonical_path.exists():
            gaps.append({"severity": "ERROR", "code": "missing_canonical_section_file",
                         "record_id": raw_path.stem, "source_file": str(raw_path),
                         "message": f"Raw section has {raw_count} table(s), but canonical file is absent."})
            continue
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical_count = sum(unit.get("unit_type") == "table" for unit in canonical.get("legal_units", []))
        if canonical_count < raw_count:
            gaps.append({"severity": "ERROR", "code": "missing_canonical_table",
                         "record_id": raw_path.stem, "source_file": str(canonical_path),
                         "message": f"Raw section has {raw_count} table(s); canonical section has {canonical_count}."})
    return gaps


def canonical_schedule_ids(schedule_dir: Path) -> set[str]:
    return {unit["id"] for path in schedule_dir.glob("schedule*_can.json")
            for unit in json.loads(path.read_text(encoding="utf-8")).get("legal_units", [])
            if isinstance(unit.get("id"), str)}


def validate_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], pending: list[dict[str, Any]],
    hierarchy: dict[str, Any], conflicts: list[dict[str, Any]], schedule_source_ids: set[str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    issues: list[dict[str, str]] = []

    def add(severity: str, code: str, record_id: Any, source_file: Any, message: str) -> None:
        issues.append({"severity": severity, "code": code, "record_id": str(record_id or ""),
                       "source_file": str(source_file or ""), "message": message})

    counts = Counter(node.get("id") for node in nodes if node.get("id"))
    by_id = {node["id"]: node for node in nodes if node.get("id")}
    catalog = {node["id"]: node for node in hierarchy.get("hierarchy_nodes", []) if node.get("id")}
    for conflict in conflicts:
        add("ERROR", "conflicting_node_id", conflict.get("id"), conflict.get("source_file"),
            "Step 6 left this ID unresolved; strict import is blocked.")
    for node_id, count in counts.items():
        if count > 1:
            add("ERROR", "duplicate_node_id", node_id, "", f"Active ID occurs {count} times.")

    for node in nodes:
        node_id = node.get("id")
        source = node.get("source_file")
        for field in NODE_REQUIRED:
            if not isinstance(node.get(field), str) or not node[field].strip():
                add("ERROR", "missing_node_field", node_id, source, f"Required field {field!r} is empty.")
        if node.get("node_type") not in NODE_TYPES:
            add("ERROR", "unknown_node_type", node_id, source, f"Unknown type {node.get('node_type')!r}.")
        if node.get("is_placeholder") is True or str(node_id).startswith("ext-"):
            add("ERROR", "external_placeholder_in_active_graph", node_id, source,
                "External placeholders are deferred; they are not ingested statutory text.")
        if node.get("is_placeholder") is not False:
            add("ERROR", "invalid_placeholder_flag", node_id, source, "Active nodes require is_placeholder=false.")
        missing = [field for field in NODE_EXPOSED if field not in node]
        if missing:
            add("ERROR", "missing_exposed_node_fields", node_id, source, f"Fields absent: {', '.join(missing)}.")
        for prefix in ("division", "subdivision"):
            fields = (f"{prefix}_id", f"{prefix}_number", f"{prefix}_title")
            if node.get(fields[0]) is None and any(node.get(field) is not None for field in fields[1:]):
                add("ERROR", "partial_nullable_hierarchy", node_id, source, f"Absent {prefix} must have all null fields.")
            if node.get(fields[0]) is not None and any(node.get(field) is None for field in fields[1:]):
                add("ERROR", "incomplete_hierarchy_group", node_id, source, f"Present {prefix} lacks number or title.")
        if node.get("node_type", "").startswith("chapter") or node.get("node_type") == "schedule":
            if node.get("section_id") is not None or node.get("section_number") is not None:
                add("ERROR", "root_or_hierarchy_has_section", node_id, source, "Hierarchy/Schedule node must have null section fields.")
        if node.get("node_type") == "sub_clauses":
            add("WARN", "plural_sub_clauses_needs_review", node_id, source,
                "Preserved source type; review before normalizing to sub_clause.")
        if node.get("node_type") in {"chapter", "chapter_division", "chapter_subdivision"}:
            original = catalog.get(node_id)
            if original is None:
                add("ERROR", "hierarchy_node_not_in_catalog", node_id, source, "Hierarchy node absent from Chapter catalog.")
            elif (node.get("parent_id"), node.get("citation"), node.get("title")) != (
                original.get("parent_id"), original.get("citation"), original.get("title")
            ):
                add("ERROR", "hierarchy_catalog_mismatch", node_id, source, "Hierarchy identity differs from catalog.")

    for catalog_id in catalog.keys() - by_id.keys():
        add("ERROR", "missing_hierarchy_catalog_node", catalog_id, "", "Chapter catalog node absent from active graph.")

    parent_issues, _ = validate_parents(nodes, edges)
    for issue in parent_issues:
        add(issue["severity"], issue["code"], issue["node_id"], "", issue["message"])

    schedule_ids = {node_id for node_id, node in by_id.items() if node.get("node_type") == "schedule" or str(node_id).startswith("schedule-")}
    for index, edge in enumerate(edges):
        source = edge.get("source_file")
        edge_id = f"{edge.get('from_id', '')}->{edge.get('to_id', '')}"
        for field in EDGE_REQUIRED:
            if not isinstance(edge.get(field), str) or not edge[field].strip():
                add("ERROR", "missing_edge_field", edge_id, source, f"Active edge {index} has empty {field!r}.")
        if any(field not in edge for field in EDGE_EXPOSED):
            add("ERROR", "missing_exposed_edge_fields", edge_id, source, "Active edge lacks nullable contract fields.")
        kind = edge.get("edge_type")
        if kind not in EDGE_TYPES:
            add("ERROR", "unknown_active_edge_type", edge_id, source, f"Type {kind!r} is not active.")
        if edge.get("resolution_status") != "resolved":
            add("ERROR", "active_edge_not_resolved", edge_id, source, "Active edge must be resolved.")
        for field, code in (("from_id", "missing_edge_source"), ("to_id", "missing_resolved_target")):
            if edge.get(field) not in by_id:
                add("ERROR", code, edge_id, source, f"{field} is not an active node.")
        if kind == "references_schedule" and edge.get("to_id") not in schedule_ids:
            add("ERROR", "invalid_schedule_target", edge_id, source, "Resolved Schedule target is absent from canonical Schedule nodes.")
        if kind == "references_schedule" and schedule_source_ids is not None and edge.get("to_id") not in schedule_source_ids:
            add("ERROR", "schedule_target_absent_from_canonical_source", edge_id, source,
                "Resolved Schedule target is absent from Schedule canonical JSON.")
        if kind == "annotates" and edge.get("from_id") in by_id:
            if by_id[edge["from_id"]].get("node_type") not in {"note", "footnote"}:
                add("ERROR", "invalid_annotation_source", edge_id, source, "Annotation source must be a note or footnote.")

    for edge in pending:
        edge_id = f"{edge.get('from_id', '')}->{edge.get('to_id', '')}"
        source = edge.get("source_file")
        for field in EDGE_REQUIRED:
            if not isinstance(edge.get(field), str) or not edge[field].strip():
                add("ERROR", "missing_pending_edge_field", edge_id, source, f"Pending edge lacks {field!r}.")
        if edge.get("from_id") not in by_id:
            add("ERROR", "missing_pending_edge_source", edge_id, source, "Pending edge source is not an active node.")
        if edge.get("resolution_status") == "resolved":
            add("ERROR", "resolved_edge_in_pending", edge_id, source, "Resolved edge must not remain in pending collection.")
        if edge.get("edge_type") not in EDGE_TYPES | EXTERNAL_EDGE_TYPES:
            add("ERROR", "unknown_pending_edge_type", edge_id, source, "Unknown pending edge type.")
        if edge.get("edge_type") in EXTERNAL_EDGE_TYPES:
            if not str(edge.get("to_id", "")).startswith("ext-"):
                add("ERROR", "external_target_not_namespaced", edge_id, source, "External target must retain its ext- ID.")
            if edge.get("to_id") in by_id:
                add("ERROR", "external_placeholder_marked_ingested", edge_id, source, "External placeholder was imported as active text.")

    # Catalog ranges are the independent source for each section's hierarchy assignment.
    for node in nodes:
        if node.get("node_type") != "section":
            continue
        try:
            number = int(node.get("section_number"))
        except (ValueError, TypeError):
            add("ERROR", "invalid_section_number", node.get("id"), node.get("source_file"), "Section number is not numeric.")
            continue
        for field in ("chapter_id", "division_id", "subdivision_id"):
            ancestor_id = node.get(field)
            if ancestor_id is None:
                continue
            entry = catalog.get(ancestor_id)
            if entry is None:
                add("ERROR", "section_ancestor_not_in_catalog", node.get("id"), node.get("source_file"), f"{field}={ancestor_id!r} absent from catalog.")
            elif not any(item.get("start", 10**9) <= number <= item.get("end", -1) for item in entry.get("section_ranges", [])):
                add("ERROR", "section_outside_catalog_range", node.get("id"), node.get("source_file"), f"Section {number} is outside {ancestor_id} ranges.")
        for catalog_type, field in (("chapter", "chapter_id"), ("chapter_division", "division_id"),
                                    ("chapter_subdivision", "subdivision_id")):
            matches = [entry_id for entry_id, entry in catalog.items()
                       if entry.get("unit_type") == catalog_type and any(
                           item.get("start", 10**9) <= number <= item.get("end", -1)
                           for item in entry.get("section_ranges", []))]
            if len(matches) == 1 and node.get(field) != matches[0]:
                add("ERROR", "section_catalog_assignment_mismatch", node.get("id"), node.get("source_file"),
                    f"Catalog requires {field}={matches[0]!r}; node has {node.get(field)!r}.")
            elif len(matches) > 1:
                add("ERROR", "overlapping_catalog_ranges", node.get("id"), node.get("source_file"),
                    f"Section {number} matches multiple {catalog_type} entries: {', '.join(matches)}.")

    summary = {
        "stage": "4A_pre_import_validation", "schema_version": "1.2.0",
        "node_count": len(nodes), "active_edge_count": len(edges), "pending_edge_count": len(pending),
        "conflicting_node_occurrences": len(conflicts),
        "error_count": sum(issue["severity"] == "ERROR" for issue in issues),
        "warning_count": sum(issue["severity"] == "WARN" for issue in issues),
        "issue_counts": dict(sorted(Counter(issue["code"] for issue in issues).items())),
        "graph_integrity_ready": not any(issue["severity"] == "ERROR" for issue in issues),
        "strict_graph_import_ready": None,
        "source_table_completeness_checked": False,
        "external_act_nodes_deferred": True,
    }
    return issues, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the active legal graph before strict import.")
    parser.add_argument("--graph-data-dir", required=True)
    parser.add_argument("--hierarchy-file", required=True)
    parser.add_argument("--raw-dir", help="Raw section JSON; together with --canonical-dir checks missing canonical tables")
    parser.add_argument("--canonical-dir", help="Canonical section JSON; together with --raw-dir checks missing canonical tables")
    parser.add_argument("--schedule-dir", help="Canonical Schedule JSON; verifies resolved Schedule targets")
    parser.add_argument("--report")
    parser.add_argument("--summary")
    args = parser.parse_args()
    directory = Path(args.graph_data_dir)
    report = Path(args.report) if args.report else directory / "graph_import_validation_report.csv"
    summary_path = Path(args.summary) if args.summary else directory / "graph_import_validation_summary.json"
    issues, summary = validate_graph(
        read_jsonl(directory / "graph_nodes.jsonl"), read_jsonl(directory / "graph_edges.jsonl"),
        read_jsonl(directory / "graph_pending_edges.jsonl"),
        json.loads(Path(args.hierarchy_file).read_text(encoding="utf-8")),
        read_jsonl(directory / "graph_node_conflicts.jsonl"),
        canonical_schedule_ids(Path(args.schedule_dir)) if args.schedule_dir else None,
    )
    if bool(args.raw_dir) != bool(args.canonical_dir):
        parser.error("--raw-dir and --canonical-dir must be provided together")
    if args.raw_dir:
        issues.extend(source_table_gaps(Path(args.raw_dir), Path(args.canonical_dir)))
        summary["source_table_completeness_checked"] = True
        summary["strict_graph_import_ready"] = not any(issue["severity"] == "ERROR" for issue in issues)
    summary["error_count"] = sum(issue["severity"] == "ERROR" for issue in issues)
    summary["warning_count"] = sum(issue["severity"] == "WARN" for issue in issues)
    summary["issue_counts"] = dict(sorted(Counter(issue["code"] for issue in issues).items()))
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(issues)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {summary['node_count']} nodes and {summary['active_edge_count']} active edges: "
          f"{summary['error_count']} errors, {summary['warning_count']} warnings")
    if summary["error_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
