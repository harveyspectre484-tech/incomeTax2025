from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.2.0"
CORE_FIELDS = (
    "id",
    "node_type",
    "citation",
    "parent_id",
    "title",
    "text",
    "retrieval_text",
    "act",
    "jurisdiction",
    "is_placeholder",
    "source_file",
    "chapter_id",
    "chapter_number",
    "chapter_title",
    "division_id",
    "division_number",
    "division_title",
    "subdivision_id",
    "subdivision_number",
    "subdivision_title",
    "section_id",
    "section_number",
    "page_start",
    "page_end",
    "line_start",
    "x",
    "y_start",
    "source_properties",
)
REQUIRED_FIELDS = ("id", "node_type", "citation")
SOURCE_MAPPED_KEYS = {
    "id",
    "unit_type",
    "citation",
    "parent_id",
    "title",
    "text",
    "retrieval_text",
    "act",
    "jurisdiction",
    "chapter",
    "chapter_id",
    "chapter_title",
    "division_id",
    "division_number",
    "division_title",
    "subdivision_id",
    "subdivision_number",
    "subdivision_title",
    "section_id",
    "section_number",
    "page_start",
    "page_end",
    "line_start",
    "x",
    "y_start",
    "source_file",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect unmerged candidate graph nodes from canonical legal JSON.")
    parser.add_argument("--section-dir", required=True)
    parser.add_argument("--schedule-dir", required=True)
    parser.add_argument("--hierarchy-file")
    parser.add_argument("--project-root")
    parser.add_argument("--output", required=True, help="Candidate node JSONL output")
    parser.add_argument("--summary", required=True, help="Collection summary JSON")
    parser.add_argument("--report", required=True, help="Mapping and inventory CSV")
    args = parser.parse_args()

    section_dir = Path(args.section_dir)
    schedule_dir = Path(args.schedule_dir)
    hierarchy_file = Path(args.hierarchy_file) if args.hierarchy_file else section_dir / "chapter_hierarchy.json"
    project_root = Path(args.project_root) if args.project_root else common_project_root(section_dir, schedule_dir)
    output_path = Path(args.output)
    summary_path = Path(args.summary)
    report_path = Path(args.report)

    candidates: list[dict[str, Any]] = []
    report_rows: list[dict[str, str]] = []
    source_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    field_presence: defaultdict[str, Counter[str]] = defaultdict(Counter)
    deferred_external_occurrences = 0
    source_files_read = 0

    section_files = sorted(section_dir.glob("*section_can.json"), key=section_sort_key)
    for path in section_files:
        data = load_json(path)
        source_files_read += 1
        provenance = relative_source(path, project_root)
        for unit in data.get("legal_units", []) or []:
            candidate = map_node(unit, provenance)
            candidates.append(candidate)
            record_inventory(candidate, "section_units", source_counts, type_counts, field_presence)
            report_rows.extend(mapping_issues(candidate, provenance))
            report_rows.extend(hierarchy_source_issues(candidate, provenance, "section"))
        deferred_external_occurrences += len(data.get("external_nodes", []) or [])

    hierarchy = load_json(hierarchy_file)
    source_files_read += 1
    hierarchy_source = relative_source(hierarchy_file, project_root)
    hierarchy_nodes = hierarchy.get("hierarchy_nodes", []) or []
    hierarchy_by_id = {str(node.get("id")): node for node in hierarchy_nodes if node.get("id")}
    for node in hierarchy_nodes:
        candidate = map_hierarchy_node(node, hierarchy_by_id, hierarchy_source)
        candidates.append(candidate)
        record_inventory(candidate, "hierarchy_nodes", source_counts, type_counts, field_presence)
        report_rows.extend(mapping_issues(candidate, hierarchy_source))
        report_rows.extend(hierarchy_source_issues(candidate, hierarchy_source, "hierarchy"))

    schedule_files = sorted(schedule_dir.glob("schedule*_can.json"), key=schedule_sort_key)
    for path in schedule_files:
        data = load_json(path)
        source_files_read += 1
        provenance = relative_source(path, project_root)
        for unit in data.get("legal_units", []) or []:
            candidate = map_node(unit, provenance)
            candidates.append(candidate)
            record_inventory(candidate, "schedule_units", source_counts, type_counts, field_presence)
            report_rows.extend(mapping_issues(candidate, provenance))
            report_rows.extend(hierarchy_source_issues(candidate, provenance, "schedule"))

    id_counts = Counter(node["id"] for node in candidates if node.get("id"))
    duplicate_ids = {node_id: count for node_id, count in id_counts.items() if count > 1}
    for node_id, count in sorted(duplicate_ids.items(), key=lambda item: natural_key(item[0])):
        sources = sorted({node["source_file"] for node in candidates if node.get("id") == node_id})
        report_rows.append(
            report_row(
                "INFO",
                "duplicate_id_pending_identity_check",
                node_id,
                " | ".join(sources),
                f"ID occurs {count} times. Records remain unmerged for 4A.3.",
            )
        )

    for node_type, counts in sorted(field_presence.items()):
        total = type_counts[node_type]
        for field_name in CORE_FIELDS:
            present = counts[field_name]
            report_rows.append(
                report_row(
                    "INFO",
                    "field_coverage",
                    "",
                    node_type,
                    f"{field_name}: {present}/{total} non-null values",
                )
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate, ensure_ascii=False, sort_keys=False) + "\n")

    summary = {
        "schema_version": SCHEMA_VERSION,
        "stage": "4A.2_collect_graph_nodes",
        "records_merged": False,
        "external_nodes_included": False,
        "candidate_node_occurrences": len(candidates),
        "unique_candidate_ids": len(id_counts),
        "duplicate_ids_pending_4A_3": duplicate_ids,
        "deferred_external_node_occurrences": deferred_external_occurrences,
        "source_files_read": source_files_read,
        "section_files_read": len(section_files),
        "schedule_files_read": len(schedule_files),
        "hierarchy_files_read": 1,
        "source_counts": dict(sorted(source_counts.items())),
        "node_type_counts": dict(sorted(type_counts.items())),
        "required_mapping_issue_count": sum(row["severity"] == "ERROR" for row in report_rows),
        "hierarchy_source_issue_count": sum(row["code"].startswith(("hierarchy_", "source_")) for row in report_rows),
        "candidate_file": str(output_path),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["severity", "code", "node_id", "source", "message"])
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"Collected {len(candidates)} unmerged candidate node records")
    print(f"Unique IDs: {len(id_counts)}")
    print(f"Duplicate IDs pending 4A.3: {len(duplicate_ids)}")
    print(f"Deferred external-node occurrences: {deferred_external_occurrences}")
    print(f"Required-field mapping errors: {summary['required_mapping_issue_count']}")
    print(f"Candidates: {output_path}")
    print(f"Summary: {summary_path}")
    print(f"Report: {report_path}")


def map_node(source: dict[str, Any], source_file: str) -> dict[str, Any]:
    node_type = nullable_string(source.get("unit_type") or source.get("node_type"))
    chapter_number = nullable_string(source.get("chapter_number") or source.get("chapter"))
    candidate = {
        "id": nullable_string(source.get("id")),
        "node_type": node_type,
        "citation": nullable_string(source.get("citation")),
        "parent_id": nullable_string(source.get("parent_id")),
        "title": nullable_string(source.get("title")),
        "text": nullable_string(source.get("text")),
        "retrieval_text": nullable_string(source.get("retrieval_text")),
        "act": nullable_string(source.get("act")),
        "jurisdiction": nullable_string(source.get("jurisdiction")),
        "is_placeholder": False,
        "source_file": source_file,
        "chapter_id": nullable_string(source.get("chapter_id")),
        "chapter_number": chapter_number,
        "chapter_title": nullable_string(source.get("chapter_title")),
        "division_id": nullable_string(source.get("division_id")),
        "division_number": nullable_string(source.get("division_number")),
        "division_title": nullable_string(source.get("division_title")),
        "subdivision_id": nullable_string(source.get("subdivision_id")),
        "subdivision_number": nullable_string(source.get("subdivision_number")),
        "subdivision_title": nullable_string(source.get("subdivision_title")),
        "section_id": nullable_string(source.get("section_id")),
        "section_number": nullable_string(source.get("section_number")),
        "page_start": source.get("page_start"),
        "page_end": source.get("page_end"),
        "line_start": source.get("line_start"),
        "x": source.get("x"),
        "y_start": source.get("y_start"),
        "source_properties": {
            key: value
            for key, value in source.items()
            if key not in SOURCE_MAPPED_KEYS
        },
    }
    return candidate


def map_hierarchy_node(
    source: dict[str, Any],
    hierarchy_by_id: dict[str, dict[str, Any]],
    source_file: str,
) -> dict[str, Any]:
    candidate = map_node(source, source_file)
    node_type = candidate["node_type"]
    if node_type == "chapter":
        candidate["chapter_id"] = candidate["id"]
        candidate["chapter_number"] = nullable_string(source.get("number"))
        candidate["chapter_title"] = candidate["title"]
        return candidate

    if node_type == "chapter_division":
        chapter = hierarchy_by_id.get(str(source.get("parent_id")), {})
        candidate["chapter_id"] = nullable_string(chapter.get("id"))
        candidate["chapter_number"] = nullable_string(chapter.get("number"))
        candidate["chapter_title"] = nullable_string(chapter.get("title"))
        candidate["division_id"] = candidate["id"]
        candidate["division_number"] = nullable_string(source.get("number"))
        candidate["division_title"] = candidate["title"]
        return candidate

    if node_type == "chapter_subdivision":
        division = hierarchy_by_id.get(str(source.get("parent_id")), {})
        chapter = hierarchy_by_id.get(str(division.get("parent_id")), {})
        candidate["chapter_id"] = nullable_string(chapter.get("id"))
        candidate["chapter_number"] = nullable_string(chapter.get("number"))
        candidate["chapter_title"] = nullable_string(chapter.get("title"))
        candidate["division_id"] = nullable_string(division.get("id"))
        candidate["division_number"] = nullable_string(division.get("number"))
        candidate["division_title"] = nullable_string(division.get("title"))
        candidate["subdivision_id"] = candidate["id"]
        candidate["subdivision_number"] = nullable_string(source.get("number"))
        candidate["subdivision_title"] = candidate["title"]
    return candidate


def mapping_issues(node: dict[str, Any], source_file: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for field_name in REQUIRED_FIELDS:
        if node.get(field_name) is None:
            issues.append(
                report_row(
                    "ERROR",
                    "missing_required_mapping",
                    node.get("id") or "",
                    source_file,
                    f"Required graph field '{field_name}' could not be mapped.",
                )
            )
    return issues


def hierarchy_source_issues(node: dict[str, Any], source_file: str, group: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def add(code: str, message: str) -> None:
        issues.append(report_row("ERROR", code, node.get("id") or "", source_file, message))

    if not node.get("source_file"):
        add("source_missing_file", "Node has no source_file provenance.")

    for level in ("division", "subdivision"):
        values = [node.get(f"{level}_{part}") for part in ("id", "number", "title")]
        if any(value is None for value in values) and any(value is not None for value in values):
            add("hierarchy_partial_level", f"{level} fields must be all present or all null.")

    if node.get("subdivision_id") and not node.get("division_id"):
        add("hierarchy_missing_division", "Subdivision requires a division.")

    if group == "section":
        if not all(node.get(field) for field in ("chapter_id", "chapter_number", "chapter_title")):
            add("hierarchy_missing_chapter", "Section legal unit is missing its Chapter fields.")
        if not node.get("section_id") or not node.get("section_number"):
            add("hierarchy_missing_section", "Section legal unit needs section_id and section_number.")
    elif group in ("schedule", "hierarchy"):
        if node.get("section_id") is not None or node.get("section_number") is not None:
            add("hierarchy_unexpected_section", f"{group} node must have null section fields.")
        if group == "schedule" and any(node.get(f"{level}_id") for level in ("chapter", "division", "subdivision")):
            add("hierarchy_unexpected_chapter", "Schedule node must not carry a Chapter path.")

    for field in ("page_start", "page_end", "line_start", "x", "y_start"):
        value = node.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            add("source_invalid_coordinate", f"{field} must be numeric or null.")

    return issues


def record_inventory(
    node: dict[str, Any],
    source_group: str,
    source_counts: Counter[str],
    type_counts: Counter[str],
    field_presence: defaultdict[str, Counter[str]],
) -> None:
    source_counts[source_group] += 1
    node_type = str(node.get("node_type") or "<missing>")
    type_counts[node_type] += 1
    for field_name in CORE_FIELDS:
        if node.get(field_name) is not None:
            field_presence[node_type][field_name] += 1


def report_row(severity: str, code: str, node_id: str, source: str, message: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "node_id": node_id,
        "source": source,
        "message": message,
    }


def nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def relative_source(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def common_project_root(section_dir: Path, schedule_dir: Path) -> Path:
    section_parent = section_dir.resolve().parent
    if schedule_dir.resolve().parent == section_parent:
        return section_parent
    return Path.cwd().resolve()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def section_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"(\d+)", path.name)
    return (int(match.group(1)), path.name) if match else (10**9, path.name)


def schedule_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"schedule(\d+)", path.name, flags=re.IGNORECASE)
    return (int(match.group(1)), path.name) if match else (10**9, path.name)


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


if __name__ == "__main__":
    main()
