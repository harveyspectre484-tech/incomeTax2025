from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


REPORT_FIELDS = (
    "section",
    "severity",
    "code",
    "source_unit_id",
    "reference_text",
    "target_id",
    "schedule_number",
    "locator_type",
    "message",
    "canonical_file",
    "schedule_file",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate schedule-reference graph edges against raw schedule JSON files.")
    parser.add_argument("--canonical-dir", required=True)
    parser.add_argument("--schedule-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-count", type=int, default=16)
    args = parser.parse_args()

    canonical_dir = Path(args.canonical_dir)
    schedule_dir = Path(args.schedule_dir)
    report_path = Path(args.report)
    catalog, schedule_files = load_schedule_catalog(schedule_dir)
    rows: list[dict[str, str]] = []

    for number in range(1, args.expected_count + 1):
        roman = int_to_roman(number)
        if roman in schedule_files:
            continue
        rows.append(
            report_row(
                section="",
                severity="ERROR",
                code="missing_schedule_file",
                source_unit_id="",
                reference_text="",
                target_id=f"schedule-{roman}",
                schedule_number=roman,
                locator_type="schedule",
                message=f"Expected schedule file schedule{number}.json is missing.",
                canonical_file="",
                schedule_file=str(schedule_dir / f"schedule{number}.json"),
            )
        )

    for canonical_path in sorted(canonical_dir.glob("*section_can.json"), key=section_sort_key):
        data = json.loads(canonical_path.read_text(encoding="utf-8"))
        section = str(data.get("section_number") or section_from_filename(canonical_path.name))
        for edge in data.get("graph_edges", []) or []:
            if edge.get("edge_type") != "references_schedule":
                continue
            target_id = str(edge.get("to_id", ""))
            roman = str(edge.get("schedule_number_roman") or schedule_roman_from_target(target_id))
            schedule_path = schedule_files.get(roman)
            if schedule_path is None:
                severity = "ERROR"
                code = "missing_schedule_file"
                message = f"Schedule {roman} is referenced but its schedule JSON file is missing."
            elif target_id not in catalog:
                severity = "ERROR"
                code = "missing_schedule_target"
                message = f"Schedule reference target '{target_id}' is not present in {schedule_path.name}."
            else:
                severity = "OK"
                code = "resolved_schedule_reference"
                message = f"Schedule reference resolves to '{target_id}'."
            rows.append(
                report_row(
                    section=section,
                    severity=severity,
                    code=code,
                    source_unit_id=str(edge.get("from_id", "")),
                    reference_text=str(edge.get("reference_text", "")),
                    target_id=target_id,
                    schedule_number=roman,
                    locator_type=str(edge.get("schedule_locator_type") or edge.get("reference_kind") or "schedule"),
                    message=message,
                    canonical_file=str(canonical_path),
                    schedule_file=str(schedule_path or schedule_dir / schedule_filename_from_roman(roman)),
                )
            )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    resolved = sum(row["severity"] == "OK" for row in rows)
    errors = sum(row["severity"] == "ERROR" for row in rows)
    print(f"Schedule files found: {len(schedule_files)} of {args.expected_count}")
    print(f"Resolved schedule-reference edges: {resolved}")
    print(f"Schedule validation errors: {errors}")
    print(f"Report: {report_path}")


def load_schedule_catalog(schedule_dir: Path) -> tuple[set[str], dict[str, Path]]:
    catalog: set[str] = set()
    files: dict[str, Path] = {}
    for path in sorted(schedule_dir.glob("schedule*.json")):
        match = re.fullmatch(r"schedule(\d+)\.json", path.name, flags=re.IGNORECASE)
        if not match:
            continue
        roman = int_to_roman(int(match.group(1)))
        files[roman] = path
        collect_ids(json.loads(path.read_text(encoding="utf-8")), catalog)
    return catalog, files


def collect_ids(value: Any, catalog: set[str]) -> None:
    if isinstance(value, dict):
        if value.get("id"):
            catalog.add(str(value["id"]))
        for child in value.values():
            collect_ids(child, catalog)
    elif isinstance(value, list):
        for child in value:
            collect_ids(child, catalog)


def report_row(**values: str) -> dict[str, str]:
    return {field: str(values.get(field, "")) for field in REPORT_FIELDS}


def schedule_roman_from_target(target_id: str) -> str:
    match = re.match(r"schedule-([IVXLCDM]+)", target_id, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def schedule_filename_from_roman(roman: str) -> str:
    number = roman_to_int(roman)
    return f"schedule{number}.json" if number else ""


def section_from_filename(name: str) -> str:
    match = re.match(r"(\d+)", name)
    return match.group(1) if match else ""


def section_sort_key(path: Path) -> tuple[int, str]:
    number = section_from_filename(path.name)
    return (int(number) if number else 10**9, path.name)


def roman_to_int(value: str) -> int | None:
    roman = value.upper()
    if not roman or not re.fullmatch(r"[IVXLCDM]+", roman):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for character in reversed(roman):
        current = values[character]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total


def int_to_roman(value: int) -> str:
    numerals = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    parts: list[str] = []
    remainder = value
    for number, numeral in numerals:
        count, remainder = divmod(remainder, number)
        parts.append(numeral * count)
    return "".join(parts)


if __name__ == "__main__":
    main()
