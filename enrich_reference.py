#!/usr/bin/env python3
"""
Rule-based legal reference enricher.

Reads a parsed Act JSON, detects internal references such as:
  - sub-section (2)
  - sub-section (2)(b)
  - section 6(13)
  - section 2(49)(o)

Then writes:
  - enriched JSON with `references` arrays attached to source units
  - unresolved reference report for manual review
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


TEXT_FIELDS = (
    "title",
    "text",
    "text_before_subsections",
    "text_after_subsections",
    "text_before_clauses",
    "text_after_clauses",
)

SUBSECTION_RE = re.compile(
    r"\bsub-?sections?\s*\(\s*(?P<subsection>\d+[A-Za-z]?)\s*\)"
    r"(?:\s*\(\s*(?:\d*\[)?(?P<clause>[a-z0-9]+)\]?\s*\))?"
    r"(?:\s*(?:to|and|or|-)\s*(?:\d*\[)?(?:\(\s*)?(?P<end_clause>[a-z0-9]+)\)?\]?)?",
    re.IGNORECASE,
)

SECTION_RE = re.compile(
    r"\bsections?\s+"
    r"(?P<section>\d+[A-Za-z]?)"
    r"(?:\s*\(\s*(?P<subsection>\d+[A-Za-z]?)\s*\))?"
    r"(?:\s*\(\s*(?:\d*\[)?(?P<clause>[a-z0-9]+)\]?\s*\))?"
    r"(?:\s*\(\s*(?P<subclause>[ivx]+)\s*\))?"
    r"(?:\s*(?:to|and|or|-)\s*(?:\d*\[)?(?:\(\s*)?(?P<end_clause>[a-z0-9]+)\)?\]?)?",
    re.IGNORECASE,
)

CLAUSE_RE = re.compile(
    r"\b(?:sub-)?clauses?\s*\(\s*(?:\d*\[)?(?P<clause>[a-z0-9]+)\]?\s*\)"
    r"(?:\s*\(\s*(?P<subclause>[ivx]+)\s*\))?"
    r"(?:\s*(?:to|and|or|-)\s*(?:\d*\[)?(?:\(\s*)?(?P<end_clause>[a-z0-9]+)\)?\]?)?",
    re.IGNORECASE,
)

PARAGRAPH_SCHEDULE_RE = re.compile(
    r"\bparagraph\s+(?P<para>\d+)"
    r"(?:\s*\(\s*(?P<subpara1>\d+)\s*\)"
    r"(?:\s*(?:and|or|to|,)\s*\(\s*(?P<subpara2>\d+)\s*\))?)?"
    r"(?:\s+of\s+Part\s+(?P<part>[A-Z]))?"
    r"(?:\s+of\s+Schedules?\s+(?P<schedule>[IVXLCDM\d]+)\b)?",
    re.IGNORECASE,
)

SCHEDULE_RE = re.compile(
    r"(?:\bPart\s+(?P<part>[A-Z])\s+of\s+)?\bSchedules?\s+(?P<schedule>[IVXLCDM\d]+)\b",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add rule-based internal legal references to parsed Act JSON."
    )
    parser.add_argument(
        "input_json",
        nargs="?",
        default=None,
        help="Path to source parsed JSON (optional if --file / -i is specified)",
    )
    parser.add_argument(
        "output_json",
        nargs="?",
        default=None,
        help="Path to write enriched JSON (optional, defaults to <input_name>_enriched.json)",
    )
    parser.add_argument(
        "-i", "--input", "--file", "--input-json",
        dest="input_flag",
        default=None,
        help="Path to source parsed JSON file",
    )
    parser.add_argument(
        "-o", "--output", "--output-json",
        dest="output_flag",
        default=None,
        help="Path to write enriched JSON file",
    )
    parser.add_argument(
        "--unresolved",
        default=None,
        help="Path to write unresolved reference report. Defaults beside output JSON.",
    )
    parser.add_argument(
        "--include-unresolved",
        action="store_true",
        help="Also add unresolved references into source units with confidence=low.",
    )
    args = parser.parse_args()

    input_file = args.input_flag or args.input_json
    output_file = args.output_flag or args.output_json

    if not input_file:
        try:
            input_file = input("Enter path to source parsed JSON file: ").strip().strip('"\'')
        except (EOFError, KeyboardInterrupt):
            print("\nOperation cancelled.")
            return

    if not input_file:
        print("Error: No input file path provided.")
        return

    input_path = Path(input_file).resolve()
    if not input_path.exists():
        print(f"Error: Input file does not exist: {input_path}")
        return

    if output_file:
        output_path = Path(output_file).resolve()
    else:
        output_path = input_path.with_name(f"{input_path.stem}_enriched.json")

    unresolved_path = (
        Path(args.unresolved).resolve()
        if args.unresolved
        else output_path.with_name(output_path.stem + ".unresolved.json")
    )

    data = json.loads(input_path.read_text(encoding="utf-8"))
    enriched = copy.deepcopy(data)

    unit_index: dict[str, dict[str, Any]] = {}
    walk_units(enriched, unit_index=unit_index)

    unresolved: list[dict[str, Any]] = []
    enrich_units(enriched, unit_index=unit_index, unresolved=unresolved, include_unresolved=args.include_unresolved)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    unresolved_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    unresolved_path.write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"units indexed: {len(unit_index)}")
    print(f"unresolved references: {len(unresolved)}")
    print(f"wrote enriched JSON: {output_path}")
    print(f"wrote unresolved report: {unresolved_path}")


def walk_units(
    obj: Any,
    unit_index: dict[str, dict[str, Any]],
    current_section_id: str | None = None,
    current_subsection_id: str | None = None,
) -> None:
    if isinstance(obj, dict):
        unit_id = obj.get("id")
        unit_type = obj.get("type")

        if unit_id and unit_type in {"section", "subsection", "clause"}:
            if unit_type == "section":
                current_section_id = unit_id
                current_subsection_id = None
            elif unit_type == "subsection":
                current_subsection_id = unit_id

            unit_index[unit_id] = {
                "id": unit_id,
                "type": unit_type,
                "number": obj.get("number"),
                "section_id": current_section_id,
                "subsection_id": current_subsection_id,
            }

        for value in obj.values():
            walk_units(value, unit_index, current_section_id, current_subsection_id)

    elif isinstance(obj, list):
        for item in obj:
            walk_units(item, unit_index, current_section_id, current_subsection_id)


def enrich_units(
    obj: Any,
    unit_index: dict[str, dict[str, Any]],
    unresolved: list[dict[str, Any]],
    include_unresolved: bool,
    current_section_id: str | None = None,
    current_subsection_id: str | None = None,
) -> None:
    if isinstance(obj, dict):
        unit_id = obj.get("id")
        unit_type = obj.get("type")

        if unit_type == "section" and unit_id:
            current_section_id = unit_id
            current_subsection_id = None
        elif unit_type == "subsection" and unit_id:
            current_subsection_id = unit_id

        if unit_id and unit_type in {"section", "subsection", "clause"}:
            references = extract_references_for_unit(
                obj,
                source_id=unit_id,
                source_type=unit_type,
                current_section_id=current_section_id,
                current_subsection_id=current_subsection_id,
                unit_index=unit_index,
                unresolved=unresolved,
                include_unresolved=include_unresolved,
            )
            if references:
                existing = obj.get("references") or []
                obj["references"] = merge_references(existing, references)

        for value in obj.values():
            enrich_units(
                value,
                unit_index,
                unresolved,
                include_unresolved,
                current_section_id,
                current_subsection_id,
            )

    elif isinstance(obj, list):
        for item in obj:
            enrich_units(
                item,
                unit_index,
                unresolved,
                include_unresolved,
                current_section_id,
                current_subsection_id,
            )


def extract_references_for_unit(
    unit: dict[str, Any],
    source_id: str,
    source_type: str,
    current_section_id: str | None,
    current_subsection_id: str | None,
    unit_index: dict[str, dict[str, Any]],
    unresolved: list[dict[str, Any]],
    include_unresolved: bool,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen_spans: set[tuple[str, int, int]] = set()

    for field in TEXT_FIELDS:
        text = unit.get(field)
        if not isinstance(text, str) or not text.strip():
            continue

        for regex, kind in (
            (PARAGRAPH_SCHEDULE_RE, "para_schedule"),
            (SCHEDULE_RE, "schedule"),
            (SUBSECTION_RE, "subsection"),
            (SECTION_RE, "section"),
            (CLAUSE_RE, "clause"),
        ):
            for match in regex.finditer(text):
                span_key = (field, match.start(), match.end())
                if span_key in seen_spans:
                    continue
                if any(start <= match.start() and end >= match.end() for (_, start, end) in seen_spans):
                    continue
                seen_spans.add(span_key)

                anchor_text = match.group(0)
                target_id, target_type = resolve_target(
                    kind,
                    match,
                    current_section_id=current_section_id,
                    current_subsection_id=current_subsection_id,
                    unit_index=unit_index,
                )

                if not target_id:
                    continue

                relationship = classify_relationship(text, match.start(), match.end())
                in_index = target_id in unit_index
                confidence = "high" if in_index else "medium"

                refs.append(
                    {
                        "reference_id": f"ref-{source_id}-{len(refs) + 1:03d}",
                        "target_id": target_id,
                        "target_type": target_type or "unknown",
                        "anchor_text": anchor_text,
                        "relationship": relationship,
                        "confidence": confidence,
                    }
                )

                if not in_index:
                    item = {
                        "source_id": source_id,
                        "source_type": source_type,
                        "field": field,
                        "anchor_text": anchor_text,
                        "guessed_target_id": target_id,
                        "guessed_target_type": target_type,
                        "relationship": relationship,
                        "reason": "target_id not found in local unit index",
                    }
                    unresolved.append(item)

    return refs


def resolve_target(
    kind: str,
    match: re.Match[str],
    current_section_id: str | None,
    current_subsection_id: str | None,
    unit_index: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    if kind == "para_schedule":
        groups = match.groupdict()
        para = groups.get("para")
        part = groups.get("part")
        schedule = groups.get("schedule")
        
        target_id_parts = []
        if schedule:
            target_id_parts.append(f"schedule-{schedule.upper()}")
        if part:
            target_id_parts.append(f"part-{part.upper()}")
        if para:
            target_id_parts.append(f"para-{para}")
            subpara1 = groups.get("subpara1")
            if subpara1:
                target_id_parts.append(subpara1)
                
        target_id = "-".join(target_id_parts) if target_id_parts else None
        target_type = "paragraph" if para else ("part" if part else "schedule")
        return target_id, unit_index.get(target_id, {}).get("type") or target_type

    if kind == "schedule":
        groups = match.groupdict()
        part = groups.get("part")
        schedule = groups.get("schedule")
        target_id_parts = []
        if schedule:
            target_id_parts.append(f"schedule-{schedule.upper()}")
        if part:
            target_id_parts.append(f"part-{part.upper()}")
        target_id = "-".join(target_id_parts) if target_id_parts else None
        target_type = "part" if part else "schedule"
        return target_id, unit_index.get(target_id, {}).get("type") or target_type

    if kind == "subsection":
        if not current_section_id:
            return None, None
        subsection = match.group("subsection")
        clause = match.groupdict().get("clause")
        target_id = f"{current_section_id}-{subsection}"
        if clause:
            target_id = f"{target_id}-{clause.lower()}"
        return target_id, unit_index.get(target_id, {}).get("type") or ("clause" if clause else "subsection")

    if kind == "section":
        section = match.group("section")
        subsection = match.groupdict().get("subsection")
        clause = match.groupdict().get("clause")
        subclause = match.groupdict().get("subclause")
        target_id = section
        target_type = "section"
        if subsection:
            target_id = f"{target_id}-{subsection}"
            target_type = "subsection"
        if clause:
            target_id = f"{target_id}-{clause.lower()}"
            target_type = "clause"
        if subclause:
            target_id = f"{target_id}-{subclause.lower()}"
            target_type = "subclause"
        return target_id, unit_index.get(target_id, {}).get("type") or target_type

    if kind == "clause":
        if not current_subsection_id:
            return None, None
        clause = match.group("clause")
        target_id = f"{current_subsection_id}-{clause.lower()}"
        return target_id, unit_index.get(target_id, {}).get("type") or "clause"

    return None, None


def classify_relationship(text: str, start: int, end: int) -> str:
    window_start = max(0, start - 90)
    window_end = min(len(text), end + 90)
    window = text[window_start:window_end].lower()
    before = text[window_start:start].lower()

    if "subject to" in before[-40:]:
        return "subject_to"
    if "shall not apply" in window or "not apply" in window:
        return "exception_to"
    if "subject to" in window:
        return "subject_to"
    if "notwithstanding" in window:
        return "overrides"
    if "within the meaning of" in window or "as defined in" in window or "defined in" in window or "means" in window:
        return "definition_reference"
    if "in accordance with" in window or "to the extent provided in" in window or "provided in" in window:
        return "procedure_reference"
    if "under" in before[-30:] or "as per" in before[-30:] or "mentioned in" in window:
        return "depends_on"
    if "for the purposes of" in window:
        return "definition_or_scope"
    return "related_to"


def merge_references(existing: list[Any], new_refs: list[dict[str, Any]]) -> list[Any]:
    merged = list(existing)
    seen = {
        (
            ref.get("target_id"),
            ref.get("anchor_text"),
            ref.get("relationship"),
        )
        for ref in existing
        if isinstance(ref, dict)
    }

    for ref in new_refs:
        key = (ref.get("target_id"), ref.get("anchor_text"), ref.get("relationship"))
        if key not in seen:
            merged.append(ref)
            seen.add(key)

    for index, ref in enumerate(merged, start=1):
        if isinstance(ref, dict) and not ref.get("reference_id"):
            ref["reference_id"] = f"ref-auto-{index:03d}"

    return merged


if __name__ == "__main__":
    main()
