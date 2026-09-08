from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CHILD_KEYS = ("subsections", "clauses", "sub_clauses", "items", "children")


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

    for raw_path in sorted(raw_dir.glob("*section.json"), key=section_sort_key):
        section_no = section_number_from_name(raw_path.name)
        can_path = canonical_dir / f"{section_no}section_can.json"
        issues = validate_pair(raw_path, can_path)
        errors = [issue for issue in issues if issue["severity"] == "ERROR"]
        warnings = [issue for issue in issues if issue["severity"] == "WARN"]
        total_errors += len(errors)
        total_warnings += len(warnings)
        if issues:
            rows.extend(issues)
        else:
            rows.append(
                {
                    "section": section_no,
                    "severity": "OK",
                    "code": "ok",
                    "message": "No structural issues found.",
                    "file": str(can_path),
                }
            )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["section", "severity", "code", "message", "file"])
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
    print(f"Report: {report_path}")


def validate_pair(raw_path: Path, can_path: Path) -> list[dict[str, str]]:
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

    for required_id in sorted(expected_ids - actual_id_set, key=natural_key):
        issues.append(issue(section_no, "ERROR", "missing_expected_unit", f"Missing expected unit id: {required_id}", can_path))

    unexpected = sorted(actual_id_set - expected_ids, key=natural_key)
    for unit_id in unexpected:
        unit = next((item for item in units if str(item.get("id")) == unit_id), {})
        if unit.get("generated_from_inline_text"):
            continue
        if unit.get("unit_type") in {"table", "table_row", "table_cell_item"}:
            continue
        issues.append(issue(section_no, "WARN", "unexpected_unit", f"Unit was not predicted from raw hierarchy: {unit_id}", can_path))

    valid_parent_ids = actual_id_set | {None}
    for unit in units:
        unit_id = str(unit.get("id", ""))
        parent_id = unit.get("parent_id")
        if parent_id not in valid_parent_ids:
            issues.append(issue(section_no, "ERROR", "bad_parent_id", f"{unit_id} has missing parent_id: {parent_id}", can_path))
        if clean(unit.get("text", "")) == "":
            issues.append(issue(section_no, "ERROR", "empty_text", f"{unit_id} has empty text.", can_path))
        if clean(unit.get("context_text", "")) == "":
            issues.append(issue(section_no, "ERROR", "empty_context_text", f"{unit_id} has empty context_text.", can_path))
        citation = clean(unit.get("citation", ""))
        if citation and section_no not in citation:
            citation_section = citation_section_number(citation)
            if citation_section and citation_section != str(unit.get("section_number")):
                issues.append(issue(section_no, "WARN", "citation_mismatch", f"{unit_id} citation may not match section: {citation}", can_path))

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
    for section in raw.get("sections", []) or []:
        section_id = str(section.get("id") or section.get("number"))
        expected.append({"id": section_id, "unit_type": "section"})
        walk_children(section, expected)
    return expected


def walk_children(node: dict[str, Any], expected: list[dict[str, str]]) -> None:
    for key in CHILD_KEYS:
        for child in node.get(key, []) or []:
            if not isinstance(child, dict):
                continue
            child_id = child.get("id")
            if child_id:
                expected.append({"id": str(child_id), "unit_type": str(child.get("type", ""))})
            walk_children(child, expected)
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


def issue(section: str, severity: str, code: str, message: str, file_path: Path) -> dict[str, str]:
    return {
        "section": section,
        "severity": severity,
        "code": code,
        "message": message,
        "file": str(file_path),
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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