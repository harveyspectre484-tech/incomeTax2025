from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CHILD_KEYS = (
    "subsections",
    "clauses",
    "sub_clauses",
    "items",
    "sub_items",
    "conditions",
    "children",
)
BLOCK_KEYS = ("where_block", "exclusion_block", "explanation_block")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate canonical legal units against raw structured section JSON.")
    parser.add_argument("--raw-dir", required=True, help="Directory containing raw files like 10section.json")
    parser.add_argument("--canonical-dir", required=True, help="Directory containing files like 10section_can.json")
    parser.add_argument("--report", default="canonical_validation_report.csv", help="CSV report path")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    canonical_dir = Path(args.canonical_dir)
    report_path = Path(args.report)

    rows: list[dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    total_infos = 0
    global_unit_ids = load_global_unit_ids(canonical_dir)
    external_identity_conflicts = load_external_identity_conflicts(canonical_dir)

    for raw_path in sorted(raw_dir.glob("*section.json"), key=section_sort_key):
        section_no = section_number_from_name(raw_path.name)
        can_path = canonical_dir / f"{section_no}section_can.json"
        issues = validate_pair(raw_path, can_path, global_unit_ids, external_identity_conflicts)
        errors = [issue for issue in issues if issue["severity"] == "ERROR"]
        warnings = [issue for issue in issues if issue["severity"] == "WARN"]
        infos = [issue for issue in issues if issue["severity"] == "INFO"]
        total_errors += len(errors)
        total_warnings += len(warnings)
        total_infos += len(infos)
        if issues:
            rows.extend(issues)
        else:
            rows.append(
                {
                    "section": section_no,
                    "severity": "OK",
                    "code": "ok",
                    "unit_id": "",
                    "message": "No structural issues found.",
                    "file": str(can_path),
                }
            )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["section", "severity", "code", "unit_id", "message", "file"])
        writer.writeheader()
        writer.writerows(rows)

    section_count = len(list(raw_dir.glob("*section.json")))
    ok_count = len({row["section"] for row in rows if row["severity"] == "OK"})
    issue_count = section_count - ok_count
    print(f"Checked {section_count} raw section files")
    print(f"OK sections: {ok_count}")
    print(f"Sections with issues: {issue_count}")
    print(f"Errors: {total_errors}")
    print(f"Warnings: {total_warnings}")
    print(f"Informational: {total_infos}")
    print(f"Report: {report_path}")


def validate_pair(
    raw_path: Path,
    can_path: Path,
    global_unit_ids: set[str] | None = None,
    external_identity_conflicts: set[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    section_no = section_number_from_name(raw_path.name)
    issues: list[dict[str, str]] = []

    if not can_path.exists():
        return [issue(section_no, "ERROR", "missing_canonical_file", "Canonical file is missing.", can_path)]

    try:
        raw = load_json(raw_path)
    except Exception as exc:
        return [issue(section_no, "ERROR", "invalid_raw_json", f"Raw JSON cannot be parsed: {exc}", raw_path)]

    try:
        canonical = load_json(can_path)
    except Exception as exc:
        return [issue(section_no, "ERROR", "invalid_canonical_json", f"Canonical JSON cannot be parsed: {exc}", can_path)]

    units = canonical.get("legal_units")
    if not isinstance(units, list):
        return [issue(section_no, "ERROR", "missing_legal_units", "Canonical JSON must contain legal_units list.", can_path)]

    if not units:
        issues.append(issue(section_no, "ERROR", "empty_legal_units", "No canonical units found.", can_path))
        return issues

    expected = expected_units(raw)
    expected_ids = {item["id"] for item in expected if item["id"]}
    actual_ids = [str(unit.get("id", "")) for unit in units]
    actual_id_set = set(actual_ids)
    mapped_source_ids = {str(unit.get("source_id")) for unit in units if unit.get("source_id")}

    for unit_id, count in Counter(actual_ids).items():
        if count > 1:
            issues.append(issue(section_no, "ERROR", "duplicate_id", f"Duplicate canonical id: {unit_id}", can_path))

    section_units = [unit for unit in units if unit.get("unit_type") == "section" and str(unit.get("section_number")) == section_no]
    if len(section_units) != 1:
        issues.append(issue(section_no, "ERROR", "bad_section_unit_count", f"Expected 1 section unit, found {len(section_units)}.", can_path))

    unit_section_numbers = sorted({str(unit.get("section_number", "")) for unit in units if unit.get("section_number")}, key=natural_key)
    wrong_section_numbers = [number for number in unit_section_numbers if number != section_no]
    if wrong_section_numbers:
        issues.append(
            issue(
                section_no,
                "ERROR",
                "wrong_section_numbers_in_file",
                f"Canonical file contains units for other section_number values: {', '.join(wrong_section_numbers[:20])}",
                can_path,
            )
        )

    for required_id in sorted(expected_ids - actual_id_set - mapped_source_ids, key=natural_key):
        issues.append(issue(section_no, "ERROR", "missing_expected_unit", f"Missing expected unit id: {required_id}", can_path))

    unexpected = sorted(actual_id_set - expected_ids, key=natural_key)
    for unit_id in unexpected:
        unit = next((item for item in units if str(item.get("id")) == unit_id), {})
        if str(unit.get("source_id") or "") in expected_ids:
            continue
        if unit.get("generated_from_inline_text"):
            continue
        if unit.get("unit_type") in {"table", "table_row", "table_cell_item", "longline", "longline_clause"}:
            continue
        issues.append(issue(section_no, "WARN", "unexpected_unit", f"Unit was not predicted from raw hierarchy: {unit_id}", can_path))

    valid_parent_ids = actual_id_set | {None}
    units_with_children = {unit.get("parent_id") for unit in units if unit.get("parent_id") is not None}
    for unit in units:
        unit_id = str(unit.get("id", ""))
        parent_id = unit.get("parent_id")
        if parent_id not in valid_parent_ids:
            issues.append(issue(section_no, "ERROR", "bad_parent_id", f"{unit_id} has missing parent_id: {parent_id}", can_path))
        if clean(unit.get("text", "")) == "" and unit_id not in units_with_children:
            issues.append(issue(section_no, "ERROR", "empty_text", f"{unit_id} has empty text.", can_path))
        if clean(unit.get("retrieval_text", "")) == "":
            issues.append(issue(section_no, "ERROR", "empty_retrieval_text", f"{unit_id} has empty retrieval_text.", can_path))
        citation = clean(unit.get("citation", ""))
        citation_section = citation_section_number(citation)
        if citation_section and citation_section != str(unit.get("section_number")):
            issues.append(issue(section_no, "WARN", "citation_mismatch", f"{unit_id} citation may not match section: {citation}", can_path))
        source_id = str(unit.get("source_id") or "")
        source_prefix = re.match(r"^(\d+)(?:-|$)", source_id)
        if source_prefix and source_prefix.group(1) != section_no:
            issues.append(
                issue(
                    section_no,
                    "WARN",
                    "source_id_section_mismatch",
                    f"{unit_id} was derived from source_id '{source_id}', which belongs to another section prefix.",
                    can_path,
                    unit_id,
                )
            )

    catalog = global_unit_ids or actual_id_set
    for edge in canonical.get("graph_edges", []) or []:
        from_id = str(edge.get("from_id", ""))
        to_id = str(edge.get("to_id", ""))
        edge_type = str(edge.get("edge_type", ""))
        if from_id not in catalog:
            issues.append(issue(section_no, "ERROR", "bad_edge_source", f"Edge source does not exist: {from_id}", can_path, from_id))
            continue
        if edge_type in {"references_external", "references_external_act"}:
            identity = (normalize_title(edge.get("target_act_title", "")), str(edge.get("target_act_number", "")))
            if identity in (external_identity_conflicts or set()):
                issues.append(
                    issue(
                        section_no,
                        "WARN",
                        "external_act_identity_conflict",
                        f"External Act identity appears with conflicting enactment years: {edge.get('reference_text', '')}",
                        can_path,
                        from_id,
                    )
                )
            else:
                issues.append(
                    issue(
                        section_no,
                        "INFO",
                        "external_reference_pending",
                        f"External target is represented by placeholder '{to_id}'.",
                        can_path,
                        from_id,
                    )
                )
            continue
        if to_id in catalog or is_external_reference(to_id):
            continue
        if edge_type == "references":
            reference_text = clean(edge.get("reference_text", ""))
            issues.append(
                issue(
                    section_no,
                    "INFO",
                    "unresolved_reference",
                    f"Cross-reference text '{reference_text}' targets '{to_id}' (not in catalog).",
                    can_path,
                    from_id,
                )
            )
        else:
            issues.append(issue(section_no, "ERROR", "bad_edge_target", f"{edge_type} edge targets missing unit: {to_id}", can_path, from_id))

    raw_tables = find_tables(raw)
    actual_tables = [unit for unit in units if unit.get("unit_type") == "table"]
    actual_rows_by_parent = defaultdict(int)
    for unit in units:
        if unit.get("unit_type") == "table_row":
            actual_rows_by_parent[unit.get("parent_id")] += 1

    if len(actual_tables) < len(raw_tables):
        issues.append(issue(section_no, "ERROR", "missing_table_units", f"Expected at least {len(raw_tables)} table units, found {len(actual_tables)}.", can_path))

    for table in raw_tables:
        table_id = table.get("id")
        expected_rows = len(table.get("rows", []) or [])
        matching_table = next((unit for unit in actual_tables if unit.get("id") == table_id), None)
        if table_id and matching_table and actual_rows_by_parent[table_id] != expected_rows:
            issues.append(
                issue(
                    section_no,
                    "ERROR",
                    "table_row_count_mismatch",
                    f"{table_id} expected {expected_rows} row units, found {actual_rows_by_parent[table_id]}.",
                    can_path,
                )
            )

    return issues


def expected_units(raw: dict[str, Any]) -> list[dict[str, str]]:
    expected: list[dict[str, str]] = []
    for section in iter_source_sections(raw):
        section_id = str(section.get("id") or section.get("number"))
        expected.append({"id": section_id, "unit_type": "section"})
        walk_children(section, expected)
    return expected


def iter_source_sections(raw: dict[str, Any]):
    seen: set[str] = set()
    for section in raw.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        identity = str(section.get("id") or section.get("number") or id(section))
        seen.add(identity)
        yield section

    for chapter in raw.get("chapters", []) or []:
        if not isinstance(chapter, dict):
            continue
        for section in chapter.get("sections", []) or []:
            if not isinstance(section, dict):
                continue
            identity = str(section.get("id") or section.get("number") or id(section))
            if identity in seen:
                continue
            seen.add(identity)
            yield section


def walk_children(node: dict[str, Any], expected: list[dict[str, str]]) -> None:
    for key in CHILD_KEYS:
        for child in node.get(key, []) or []:
            if not isinstance(child, dict):
                continue
            child_id = child.get("id")
            if child_id:
                expected.append({"id": str(child_id), "unit_type": str(child.get("type", ""))})
            walk_children(child, expected)
    for block_key in BLOCK_KEYS:
        block = node.get(block_key)
        if not isinstance(block, dict):
            continue
        block_id = block.get("id")
        if block_id:
            expected.append({"id": str(block_id), "unit_type": str(block.get("type") or block_key)})
        walk_children(block, expected)
    table = node.get("table")
    if isinstance(table, dict):
        table_id = table.get("id")
        if table_id:
            expected.append({"id": str(table_id), "unit_type": "table"})
        for row in table.get("rows", []) or []:
            for value in row.values():
                collect_table_cell_ids(value, expected)


def collect_table_cell_ids(value: Any, expected: list[dict[str, str]]) -> None:
    if isinstance(value, list):
        for item in value:
            collect_table_cell_ids(item, expected)
    elif isinstance(value, dict):
        if value.get("id"):
            expected.append({"id": str(value["id"]), "unit_type": str(value.get("type", ""))})
        for key in CHILD_KEYS:
            for child in value.get(key, []) or []:
                collect_table_cell_ids(child, expected)
        for block_key in BLOCK_KEYS:
            block = value.get(block_key)
            if isinstance(block, dict):
                collect_table_cell_ids(block, expected)


def find_tables(value: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("table"), dict):
            tables.append(value["table"])
        for child_value in value.values():
            tables.extend(find_tables(child_value))
    elif isinstance(value, list):
        for item in value:
            tables.extend(find_tables(item))
    return tables


def issue(
    section: str,
    severity: str,
    code: str,
    message: str,
    file_path: Path,
    unit_id: str = "",
) -> dict[str, str]:
    return {
        "section": section,
        "severity": severity,
        "code": code,
        "unit_id": unit_id,
        "message": message,
        "file": str(file_path),
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_global_unit_ids(canonical_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in canonical_dir.glob("*section_can.json"):
        try:
            data = load_json(path)
        except Exception:
            continue
        ids.update(str(unit.get("id")) for unit in data.get("legal_units", []) if unit.get("id"))
        ids.update(str(node.get("id")) for node in data.get("external_nodes", []) if node.get("id"))
    return ids


def load_external_identity_conflicts(canonical_dir: Path) -> set[tuple[str, str]]:
    years_by_identity: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for path in canonical_dir.glob("*section_can.json"):
        try:
            data = load_json(path)
        except Exception:
            continue
        for node in data.get("external_nodes", []) or []:
            if node.get("node_type") != "external_act_placeholder":
                continue
            identity = (normalize_title(node.get("act_title", "")), str(node.get("act_number", "")))
            years_by_identity[identity].add(str(node.get("act_year", "")))
    return {identity for identity, years in years_by_identity.items() if len(years) > 1}


def normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def is_external_reference(target_id: str) -> bool:
    return target_id.startswith("schedule-") or "-act-" in target_id.lower() or target_id.lower().endswith("-act")


def section_number_from_name(name: str) -> str:
    match = re.match(r"(\d+)", name)
    if not match:
        raise ValueError(f"Cannot infer section number from {name}")
    return match.group(1)


def section_sort_key(path: Path) -> tuple[int, str]:
    number = section_number_from_name(path.name)
    return int(number), path.name


def citation_section_number(citation: str) -> str | None:
    match = re.search(r"\bSection\s+(\d+)\b", citation, flags=re.IGNORECASE)
    return match.group(1) if match else None


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


if __name__ == "__main__":
    main()
