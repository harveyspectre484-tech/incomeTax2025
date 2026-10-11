from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate canonical Schedule units against structured Schedule JSON.")
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--canonical-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--section-canonical-dir")
    parser.add_argument("--expected-count", type=int, default=16)
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    canonical_dir = Path(args.canonical_dir)
    report_path = Path(args.report)
    raw_files = sorted(raw_dir.glob("schedule*.json"), key=schedule_sort_key)
    section_ids = load_section_ids(Path(args.section_canonical_dir)) if args.section_canonical_dir else set()
    schedule_ids = load_schedule_ids(canonical_dir)
    rows: list[dict[str, str]] = []

    if len(raw_files) != args.expected_count:
        rows.append(issue("", "ERROR", "schedule_file_count", "", f"Expected {args.expected_count} Schedule files, found {len(raw_files)}.", canonical_dir))

    for raw_path in raw_files:
        number = schedule_number_from_path(raw_path)
        canonical_path = canonical_dir / f"schedule{number}_can.json"
        rows.extend(validate_pair(raw_path, canonical_path, schedule_ids, section_ids))

    if not rows:
        rows.append(issue("", "OK", "ok", "", "All canonical Schedule files passed validation.", canonical_dir))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["schedule", "severity", "code", "unit_id", "message", "file"])
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["severity"] for row in rows)
    print(f"Checked {len(raw_files)} Schedule files")
    print(f"Errors: {counts['ERROR']}")
    print(f"Warnings: {counts['WARN']}")
    print(f"Informational: {counts['INFO']}")
    print(f"Report: {report_path}")


def validate_pair(
    raw_path: Path,
    canonical_path: Path,
    schedule_ids: set[str],
    section_ids: set[str],
) -> list[dict[str, str]]:
    number = schedule_number_from_path(raw_path)
    if not canonical_path.exists():
        return [issue(number, "ERROR", "missing_canonical_schedule", "", "Canonical Schedule file is missing.", canonical_path)]
    try:
        raw = load_json(raw_path)
        canonical = load_json(canonical_path)
    except Exception as exc:
        return [issue(number, "ERROR", "invalid_json", "", str(exc), canonical_path)]

    issues: list[dict[str, str]] = []
    units = canonical.get("legal_units", [])
    edges = canonical.get("graph_edges", [])
    ids = [str(unit.get("id", "")) for unit in units]
    id_set = set(ids)
    units_by_id = {str(unit.get("id")): unit for unit in units if unit.get("id")}

    for unit_id, count in Counter(ids).items():
        if count > 1:
            issues.append(issue(number, "ERROR", "duplicate_unit_id", unit_id, f"Unit ID occurs {count} times.", canonical_path))

    raw_ids = collect_raw_ids(raw)
    for missing_id in sorted(raw_ids - id_set):
        issues.append(issue(number, "ERROR", "missing_raw_unit", missing_id, "Raw Schedule ID is absent from canonical units.", canonical_path))

    roots = [unit for unit in units if unit.get("unit_type") == "schedule"]
    if len(roots) != 1:
        issues.append(issue(number, "ERROR", "bad_schedule_root_count", "", f"Expected one Schedule root, found {len(roots)}.", canonical_path))

    children = Counter(str(unit.get("parent_id")) for unit in units if unit.get("parent_id"))
    for unit in units:
        unit_id = str(unit.get("id", ""))
        parent_id = unit.get("parent_id")
        if parent_id is not None and str(parent_id) not in id_set:
            issues.append(issue(number, "ERROR", "missing_parent", unit_id, f"Parent '{parent_id}' does not exist.", canonical_path))
        if not clean(unit.get("text")) and not children[unit_id]:
            issues.append(issue(number, "ERROR", "empty_leaf", unit_id, "Leaf unit has no text.", canonical_path))
        if not clean(unit.get("retrieval_text")):
            issues.append(issue(number, "ERROR", "empty_retrieval_text", unit_id, "Unit has no retrieval_text.", canonical_path))

    cycle = find_parent_cycle(units_by_id)
    if cycle:
        issues.append(issue(number, "ERROR", "parent_cycle", cycle[0], " -> ".join(cycle), canonical_path))

    edge_counts = Counter(
        (
            str(edge.get("from_id", "")),
            str(edge.get("to_id", "")),
            str(edge.get("edge_type", "")),
            clean(edge.get("reference_text", "")).lower(),
        )
        for edge in edges
    )
    for key, count in edge_counts.items():
        if count > 1:
            issues.append(issue(number, "ERROR", "duplicate_edge", key[0], f"Exact edge occurs {count} times: {key}", canonical_path))

    edge_set = {(str(edge.get("from_id")), str(edge.get("to_id")), str(edge.get("edge_type"))) for edge in edges}
    for unit in units:
        unit_id = str(unit.get("id"))
        parent_id = unit.get("parent_id")
        if parent_id is None:
            continue
        parent_id = str(parent_id)
        if (unit_id, parent_id, "parent") not in edge_set:
            issues.append(issue(number, "ERROR", "missing_parent_edge", unit_id, f"Missing parent edge to '{parent_id}'.", canonical_path))
        if (parent_id, unit_id, "contains") not in edge_set:
            issues.append(issue(number, "ERROR", "missing_contains_edge", unit_id, f"Missing contains edge from '{parent_id}'.", canonical_path))

    allowed_edge_types = {"parent", "contains", "references", "references_schedule", "annotates"}
    for edge in edges:
        source = str(edge.get("from_id", ""))
        target = str(edge.get("to_id", ""))
        edge_type = str(edge.get("edge_type", ""))
        if edge_type not in allowed_edge_types:
            issues.append(issue(number, "ERROR", "disallowed_edge_type", source, edge_type, canonical_path))
            continue
        if source not in id_set:
            issues.append(issue(number, "ERROR", "missing_edge_source", source, f"Edge source '{source}' does not exist.", canonical_path))
        if edge_type in {"parent", "contains", "annotates"} and target not in id_set:
            issues.append(issue(number, "ERROR", "missing_edge_target", source, f"Edge target '{target}' does not exist.", canonical_path))
        if edge_type == "references_schedule" and target not in schedule_ids:
            issues.append(issue(number, "ERROR", "missing_schedule_target", source, f"Schedule target '{target}' does not exist.", canonical_path))
        if edge_type == "references" and target not in schedule_ids and target not in section_ids:
            issues.append(issue(number, "INFO", "unresolved_internal_reference", source, f"Reference target '{target}' is not in the current corpus.", canonical_path))

    raw_tables = collect_tables(raw)
    canonical_tables = {unit["id"]: unit for unit in units if unit.get("unit_type") == "table"}
    canonical_rows = Counter(str(unit.get("parent_id")) for unit in units if unit.get("unit_type") == "table_row")
    if len(canonical_tables) != len(raw_tables):
        issues.append(issue(number, "ERROR", "table_count_mismatch", "", f"Raw tables: {len(raw_tables)}; canonical tables: {len(canonical_tables)}.", canonical_path))
    for table in raw_tables:
        table_id = table.get("id")
        if table_id and table_id in canonical_tables:
            expected_rows = len(table.get("rows", []) or [])
            if canonical_rows[str(table_id)] != expected_rows:
                issues.append(issue(number, "ERROR", "table_row_count_mismatch", str(table_id), f"Expected {expected_rows} rows, found {canonical_rows[str(table_id)]}.", canonical_path))

    return issues


def load_schedule_ids(canonical_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in canonical_dir.glob("schedule*_can.json"):
        try:
            ids.update(str(unit.get("id")) for unit in load_json(path).get("legal_units", []) if unit.get("id"))
        except Exception:
            pass
    return ids


def load_section_ids(canonical_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in canonical_dir.glob("*section_can.json"):
        try:
            ids.update(str(unit.get("id")) for unit in load_json(path).get("legal_units", []) if unit.get("id"))
        except Exception:
            pass
    return ids


def collect_raw_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        if value.get("id"):
            ids.add(str(value["id"]))
        for child in value.values():
            ids.update(collect_raw_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(collect_raw_ids(child))
    return ids


def collect_tables(value: Any) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if str(value.get("type", "")).lower() == "table":
            tables.append(value)
        for child in value.values():
            tables.extend(collect_tables(child))
    elif isinstance(value, list):
        for child in value:
            tables.extend(collect_tables(child))
    return tables


def find_parent_cycle(units_by_id: dict[str, dict[str, Any]]) -> list[str]:
    for start in units_by_id:
        seen: dict[str, int] = {}
        path: list[str] = []
        current: str | None = start
        while current and current in units_by_id:
            if current in seen:
                return path[seen[current] :] + [current]
            seen[current] = len(path)
            path.append(current)
            parent = units_by_id[current].get("parent_id")
            current = str(parent) if parent is not None else None
    return []


def issue(schedule: str, severity: str, code: str, unit_id: str, message: str, file: Path) -> dict[str, str]:
    return {
        "schedule": schedule,
        "severity": severity,
        "code": code,
        "unit_id": unit_id,
        "message": message,
        "file": str(file),
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def schedule_number_from_path(path: Path) -> str:
    match = re.search(r"schedule(\d+)", path.stem, flags=re.IGNORECASE)
    return match.group(1) if match else path.stem


def schedule_sort_key(path: Path) -> tuple[int, str]:
    number = schedule_number_from_path(path)
    return (int(number), path.name) if number.isdigit() else (10**9, path.name)


if __name__ == "__main__":
    main()
