from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from tools.canonicalize_legal_json import (
        discover_external_act_registry,
        extract_references,
        normalize_type,
    )
except ModuleNotFoundError:
    from canonicalize_legal_json import (
        discover_external_act_registry,
        extract_references,
        normalize_type,
    )


CHILD_SPECS = (
    ("parts", "part"),
    ("paragraphs", "paragraph"),
    ("subparagraphs", "subparagraph"),
    ("clauses", "clause"),
    ("sub_clauses", "sub_clause"),
    ("sub_sub_clauses", "sub_sub_clause"),
    ("sub_sub_sub_clauses", "sub_sub_sub_clause"),
    ("items", "item"),
    ("children", "item"),
    ("notes", "note"),
    ("variables", "variable"),
    ("amendments", "amendment"),
)

TEXT_KEYS = (
    "text",
    "intro",
    "term",
    "heading_text",
    "text_before_parts",
    "text_after_parts",
    "text_before_paragraphs",
    "text_after_paragraphs",
    "text_before_subparagraphs",
    "text_after_subparagraphs",
    "text_before_clauses",
    "text_after_clauses",
    "text_before_sub_clauses",
    "text_after_sub_clauses",
    "text_before_items",
    "text_after_items",
    "text_before_formula",
    "text_after_formula",
    "text_before_table",
    "text_after_table",
    "prior_text",
)

SKIP_RENDER_KEYS = {
    "id",
    "type",
    "number",
    "marker",
    "letter",
    "numeral",
    "x",
    "y_start",
    "page_start",
    "page_end",
    "line_start",
    "footnote_refs",
    "footnote_ref",
    "related_node_ids",
    "applies_to",
    "effective_from",
    "source_number_text",
}


@dataclass
class ScheduleCanonicalizer:
    act_name: str = "Income-tax Act, 2025"
    jurisdiction: str = "India"
    external_act_registry: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    units: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    seen_edges: set[tuple[str, str, str, str]] = field(default_factory=set)
    reference_text_by_id: dict[str, str] = field(default_factory=dict)
    generated_id_counts: dict[str, int] = field(default_factory=dict)
    schedule_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_file: str = ""

    def convert(self, raw: dict[str, Any], source_file: str = "") -> dict[str, Any]:
        self.source_file = source_file
        schedules = raw.get("schedules", []) or []
        if not schedules:
            raise ValueError("Schedule JSON must contain a non-empty 'schedules' list.")
        if not self.external_act_registry:
            self.external_act_registry = discover_external_act_registry([raw])

        schedule_numbers: list[str] = []
        for schedule in schedules:
            schedule_numbers.append(str(schedule.get("number", "")))
            self.add_schedule(schedule)
            self.add_footnotes(raw.get("footnotes", []) or [], schedule)

        self.add_reference_edges()
        return {
            "schedule_number": schedule_numbers[0] if len(schedule_numbers) == 1 else "",
            "source_file": source_file,
            "legal_units": self.units,
            "graph_edges": self.edges,
        }

    def add_schedule(self, schedule: dict[str, Any]) -> None:
        number = str(schedule.get("number", ""))
        schedule_id = str(schedule.get("id") or f"schedule-{number}")
        title = clean(schedule.get("title", ""))
        citation = f"Schedule {number}"
        own_text = render_own_text(schedule)
        context = join_text(citation, title, own_text)
        self.schedule_metadata[schedule_id] = {
            "act": schedule.get("act") or self.act_name,
            "reference_text": clean(schedule.get("reference_text", "")),
            "reference_section": clean(schedule.get("reference_section", "")),
            "source_url": clean(schedule.get("source_url", "")),
            "source_year": clean(schedule.get("source_year", "")),
        }
        self.add_unit(
            source=schedule,
            unit_id=schedule_id,
            unit_type="schedule",
            citation=citation,
            parent_id=None,
            schedule_id=schedule_id,
            schedule_number=number,
            schedule_title=title,
            text=own_text,
            retrieval_text=context,
        )
        self.process_children(schedule, schedule_id, citation, context, schedule_id, number, title)

    def process_children(
        self,
        source: dict[str, Any],
        parent_id: str,
        parent_citation: str,
        parent_context: str,
        schedule_id: str,
        schedule_number: str,
        schedule_title: str,
    ) -> None:
        table = source.get("table")
        if isinstance(table, dict):
            self.add_table(
                table,
                parent_id,
                parent_citation,
                parent_context,
                schedule_id,
                schedule_number,
                schedule_title,
            )

        formula = source.get("formula")
        if isinstance(formula, dict):
            self.add_structural_node(
                formula,
                "formula",
                parent_id,
                parent_citation,
                parent_context,
                schedule_id,
                schedule_number,
                schedule_title,
                sequence=1,
            )

        for key, fallback_type in CHILD_SPECS:
            for index, child in enumerate(source.get(key, []) or [], start=1):
                if not isinstance(child, dict):
                    continue
                self.add_structural_node(
                    child,
                    fallback_type,
                    parent_id,
                    parent_citation,
                    parent_context,
                    schedule_id,
                    schedule_number,
                    schedule_title,
                    sequence=index,
                )

    def add_structural_node(
        self,
        node: dict[str, Any],
        fallback_type: str,
        parent_id: str,
        parent_citation: str,
        parent_context: str,
        schedule_id: str,
        schedule_number: str,
        schedule_title: str,
        sequence: int,
        force_table_item: bool = False,
        column_name: str = "",
    ) -> str:
        source_type = normalize_type(str(node.get("type") or fallback_type))
        unit_type = "table_cell_item" if force_table_item else source_type
        marker = node.get("number") or node.get("marker") or node.get("letter") or node.get("numeral") or sequence
        base_id = f"{parent_id}-{slug(str(marker))}"
        unit_id = str(node.get("id") or self.generated_id(base_id))
        citation = child_citation(parent_citation, unit_type, marker, column_name)
        text = render_own_text(node)
        context = join_text(f"Schedule {schedule_number}", schedule_title, citation, text)
        extra: dict[str, Any] = {}
        if force_table_item:
            extra["source_unit_type"] = source_type
            extra["table_column"] = column_name
        if not node.get("id"):
            extra["generated_id"] = True
        self.add_unit(
            source=node,
            unit_id=unit_id,
            unit_type=unit_type,
            citation=citation,
            parent_id=parent_id,
            schedule_id=schedule_id,
            schedule_number=schedule_number,
            schedule_title=schedule_title,
            text=text,
            retrieval_text=context,
            extra=extra,
        )
        self.process_children(node, unit_id, citation, context, schedule_id, schedule_number, schedule_title)
        return unit_id

    def add_table(
        self,
        table: dict[str, Any],
        parent_id: str,
        parent_citation: str,
        parent_context: str,
        schedule_id: str,
        schedule_number: str,
        schedule_title: str,
    ) -> None:
        table_id = str(table.get("id") or self.generated_id(f"{parent_id}-table"))
        columns = [clean(value) for value in table.get("columns", []) or []]
        table_text = join_text(table.get("caption", "Table"), "Columns:", "; ".join(columns))
        citation = f"{parent_citation}, Table"
        context = join_text(f"Schedule {schedule_number}", schedule_title, citation, table_text)
        self.add_unit(
            source=table,
            unit_id=table_id,
            unit_type="table",
            citation=citation,
            parent_id=parent_id,
            schedule_id=schedule_id,
            schedule_number=schedule_number,
            schedule_title=schedule_title,
            text=table_text,
            retrieval_text=context,
            extra={"columns": columns, "column_labels": table.get("column_labels", []) or []},
        )

        for index, row in enumerate(table.get("rows", []) or [], start=1):
            if not isinstance(row, dict):
                continue
            row_number = row.get("sl_no") or row.get("number") or index
            row_id = str(row.get("id") or self.generated_id(f"{table_id}-row-{slug(str(row_number))}"))
            row_text = render_table_row(row)
            row_citation = f"{citation}, Sl. No. {row_number}"
            row_context = join_text(f"Schedule {schedule_number}", schedule_title, row_citation, row_text)
            self.add_unit(
                source=row,
                unit_id=row_id,
                unit_type="table_row",
                citation=row_citation,
                parent_id=table_id,
                schedule_id=schedule_id,
                schedule_number=schedule_number,
                schedule_title=schedule_title,
                text=row_text,
                retrieval_text=row_context,
                extra={"row_number": str(row_number)},
            )
            for column_key, cell in row.items():
                if column_key in {"id", "sl_no", "number", "type"}:
                    continue
                self.add_table_cell_nodes(
                    cell,
                    row_id,
                    row_citation,
                    row_context,
                    schedule_id,
                    schedule_number,
                    schedule_title,
                    humanize(column_key),
                )

    def add_table_cell_nodes(
        self,
        value: Any,
        parent_id: str,
        parent_citation: str,
        parent_context: str,
        schedule_id: str,
        schedule_number: str,
        schedule_title: str,
        column_name: str,
    ) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value, start=1):
                self.add_table_cell_nodes(
                    item,
                    parent_id,
                    parent_citation,
                    parent_context,
                    schedule_id,
                    schedule_number,
                    schedule_title,
                    column_name,
                )
            return
        if not isinstance(value, dict):
            return

        owner_id = parent_id
        owner_citation = parent_citation
        owner_context = parent_context
        if value.get("id"):
            owner_id = self.add_structural_node(
                value,
                str(value.get("type") or "item"),
                parent_id,
                parent_citation,
                parent_context,
                schedule_id,
                schedule_number,
                schedule_title,
                sequence=1,
                force_table_item=True,
                column_name=column_name,
            )
            return

        table = value.get("table")
        if isinstance(table, dict):
            self.add_table(
                table,
                owner_id,
                owner_citation,
                owner_context,
                schedule_id,
                schedule_number,
                schedule_title,
            )
        for key, fallback_type in CHILD_SPECS:
            for index, child in enumerate(value.get(key, []) or [], start=1):
                if not isinstance(child, dict):
                    continue
                self.add_structural_node(
                    child,
                    fallback_type,
                    owner_id,
                    owner_citation,
                    owner_context,
                    schedule_id,
                    schedule_number,
                    schedule_title,
                    sequence=index,
                    force_table_item=True,
                    column_name=column_name,
                )

    def add_footnotes(self, footnotes: list[Any], schedule: dict[str, Any]) -> None:
        schedule_id = str(schedule.get("id") or f"schedule-{schedule.get('number', '')}")
        schedule_number = str(schedule.get("number", ""))
        schedule_title = clean(schedule.get("title", ""))
        for index, footnote in enumerate(footnotes, start=1):
            if not isinstance(footnote, dict):
                continue
            number = footnote.get("number") or index
            footnote_id = str(footnote.get("id") or self.generated_id(f"{schedule_id}-footnote-{number}"))
            citation = f"Schedule {schedule_number}, Footnote {number}"
            text = render_own_text(footnote)
            context = join_text(f"Schedule {schedule_number}", schedule_title, citation, text)
            self.add_unit(
                source=footnote,
                unit_id=footnote_id,
                unit_type="footnote",
                citation=citation,
                parent_id=schedule_id,
                schedule_id=schedule_id,
                schedule_number=schedule_number,
                schedule_title=schedule_title,
                text=text,
                retrieval_text=context,
            )
            targets = footnote.get("related_node_ids") or footnote.get("applies_to") or []
            if isinstance(targets, str):
                targets = [targets]
            for target in targets:
                self.add_edge(footnote_id, str(target), "annotates")

    def add_unit(
        self,
        source: dict[str, Any],
        unit_id: str,
        unit_type: str,
        citation: str,
        parent_id: str | None,
        schedule_id: str,
        schedule_number: str,
        schedule_title: str,
        text: str,
        retrieval_text: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if unit_id in self.seen_ids:
            raise ValueError(f"Duplicate canonical Schedule unit id: {unit_id}")
        self.seen_ids.add(unit_id)
        schedule_metadata = self.schedule_metadata.get(schedule_id, {})
        unit = {
            "id": unit_id,
            "citation": citation,
            "unit_type": normalize_type(unit_type),
            "parent_id": parent_id,
            "schedule_id": schedule_id,
            "schedule_number": schedule_number,
            "schedule_title": schedule_title,
            "title": clean(source.get("title", "")),
            "text": clean(text),
            "retrieval_text": clean(retrieval_text),
            "act": source.get("act") or schedule_metadata.get("act") or self.act_name,
            "jurisdiction": self.jurisdiction,
            "reference_text": clean(source.get("reference_text", "")) or schedule_metadata.get("reference_text", ""),
            "reference_section": clean(source.get("reference_section", "")) or schedule_metadata.get("reference_section", ""),
            "source_url": clean(source.get("source_url", "")) or schedule_metadata.get("source_url", ""),
            "source_year": clean(source.get("source_year", "")) or schedule_metadata.get("source_year", ""),
            "source_file": self.source_file,
            "page_start": source.get("page_start"),
            "page_end": source.get("page_end"),
            "line_start": source.get("line_start"),
            "x": source.get("x"),
            "y_start": source.get("y_start"),
        }
        if extra:
            unit.update(extra)
        self.units.append(unit)
        self.reference_text_by_id[unit_id] = clean(text)
        if parent_id is not None:
            self.add_edge(unit_id, parent_id, "parent")
            self.add_edge(parent_id, unit_id, "contains")

    def add_reference_edges(self) -> None:
        units_by_id = {unit["id"]: unit for unit in self.units}
        for unit in self.units:
            text = self.reference_text_by_id.get(unit["id"], "")
            if not text:
                continue
            references, _ = extract_references(
                unit,
                text,
                units_by_id,
                self.external_act_registry,
                {},
            )
            for reference in references:
                if reference.get("edge_type") not in {"references", "references_schedule"}:
                    continue
                target_id = re.sub(r"-+$", "", str(reference.get("to_id", "")))
                reference_text = str(reference.get("reference_text", ""))
                if target_id.startswith("-") or is_external_anaphoric_reference(text, reference_text):
                    continue
                self.add_edge(
                    unit["id"],
                    target_id,
                    str(reference["edge_type"]),
                    reference_text,
                    {key: value for key, value in reference.items() if key not in {"to_id", "edge_type", "reference_text"}},
                )
            self.add_internal_schedule_references(unit, text, units_by_id)

    def add_internal_schedule_references(
        self,
        unit: dict[str, Any],
        text: str,
        units_by_id: dict[str, dict[str, Any]],
    ) -> None:
        schedule_id = str(unit.get("schedule_id", ""))
        for match in re.finditer(r"\bparagraph\s+(\d+)(?:\(([A-Za-z0-9]+)\))?", text, flags=re.IGNORECASE):
            target = f"{schedule_id}-{match.group(1)}"
            if match.group(2):
                target += f"-{match.group(2)}"
            if target in units_by_id and target != unit["id"]:
                self.add_edge(unit["id"], target, "references", match.group(0), {"reference_kind": "schedule_internal"})
        for match in re.finditer(r"\bPart\s+([A-Z])\b", text):
            target = f"{schedule_id}-{match.group(1)}"
            if target in units_by_id and target != unit["id"]:
                self.add_edge(unit["id"], target, "references", match.group(0), {"reference_kind": "schedule_internal"})
        for match in re.finditer(r"\bNote\s+(\d+[A-Za-z]?)\b", text, flags=re.IGNORECASE):
            candidates = [f"{schedule_id}-note{match.group(1)}", f"{schedule_id}-note-{match.group(1)}"]
            target = next((candidate for candidate in candidates if candidate in units_by_id), None)
            if target and target != unit["id"]:
                self.add_edge(unit["id"], target, "references", match.group(0), {"reference_kind": "schedule_internal"})

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        reference_text: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        key = (from_id, to_id, edge_type, clean(reference_text).lower())
        if key in self.seen_edges:
            return
        self.seen_edges.add(key)
        edge = {"from_id": from_id, "to_id": to_id, "edge_type": edge_type}
        if reference_text:
            edge["reference_text"] = clean(reference_text)
        if extra:
            edge.update(extra)
        self.edges.append(edge)

    def generated_id(self, base: str) -> str:
        count = self.generated_id_counts.get(base, 0) + 1
        self.generated_id_counts[base] = count
        return base if count == 1 and base not in self.seen_ids else f"{base}-{count}"


def render_own_text(node: dict[str, Any]) -> str:
    return join_text(*(node.get(key, "") for key in TEXT_KEYS))


def render_table_row(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in row.items():
        if key in {"id", "type"}:
            continue
        rendered = render_value(value)
        if rendered:
            parts.append(f"{humanize(key)}: {rendered}")
    return "; ".join(parts)


def render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return clean(value)
    if isinstance(value, list):
        return join_text(*(render_value(item) for item in value))
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    marker = value.get("marker") or value.get("number") or value.get("letter") or value.get("numeral")
    own = render_own_text(value)
    if own:
        parts.append(f"({marker}) {own}" if marker else own)
    for key, child in value.items():
        if key in SKIP_RENDER_KEYS or key in TEXT_KEYS or key in {"table", "rows"}:
            continue
        if isinstance(child, (dict, list)):
            rendered = render_value(child)
            if rendered:
                parts.append(rendered)
    if isinstance(value.get("table"), dict):
        table = value["table"]
        parts.append(join_text("Table columns:", "; ".join(table.get("columns", []) or [])))
        for row in table.get("rows", []) or []:
            parts.append(render_table_row(row))
    return join_text(*parts)


def child_citation(parent: str, unit_type: str, marker: Any, column_name: str = "") -> str:
    labels = {
        "part": "Part",
        "paragraph": "Paragraph",
        "subparagraph": "Subparagraph",
        "clause": "Clause",
        "sub_clause": "Sub-clause",
        "sub_sub_clause": "Item",
        "sub_sub_sub_clause": "Sub-item",
        "item": "Item",
        "note": "Note",
        "variable": "Variable",
        "formula": "Formula",
        "footnote": "Footnote",
        "amendment": "Amendment",
        "substitution": "Substitution",
        "table_cell_item": "Item",
    }
    label = labels.get(unit_type, humanize(unit_type))
    prefix = f"{parent}, {column_name}" if column_name else parent
    return f"{prefix}, {label} ({marker})"


def humanize(value: Any) -> str:
    return clean(str(value).replace("_", " ")).title()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def join_text(*values: Any) -> str:
    return clean(" ".join(str(value) for value in values if value not in (None, "")))


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def is_external_anaphoric_reference(text: str, reference_text: str) -> bool:
    if not reference_text:
        return False
    start = text.lower().find(reference_text.lower())
    if start < 0:
        return False
    suffix = text[start + len(reference_text) : start + len(reference_text) + 40]
    return bool(re.match(r"\s+of\s+(?:the\s+)?(?:said|that)\s+Act\b", suffix, flags=re.IGNORECASE))


def schedule_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"schedule(\d+)", path.stem, flags=re.IGNORECASE)
    return (int(match.group(1)), path.name) if match else (10**9, path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert structured Schedule JSON into canonical legal units.")
    parser.add_argument("input_json", help="Schedule JSON file or directory containing schedule*.json files")
    parser.add_argument("output_json", help="Output JSON file or output directory")
    parser.add_argument("--act-name", default="Income-tax Act, 2025")
    parser.add_argument("--jurisdiction", default="India")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_json)
    if input_path.is_dir():
        output_path.mkdir(parents=True, exist_ok=True)
        input_files = sorted(input_path.glob("schedule*.json"), key=schedule_sort_key)
        raw_documents = [json.loads(path.read_text(encoding="utf-8")) for path in input_files]
        registry = discover_external_act_registry(raw_documents)
        total_units = 0
        for path, raw in zip(input_files, raw_documents):
            result = ScheduleCanonicalizer(
                act_name=args.act_name,
                jurisdiction=args.jurisdiction,
                external_act_registry=registry,
            ).convert(raw, str(path))
            number_match = re.search(r"schedule(\d+)", path.stem, flags=re.IGNORECASE)
            filename = f"schedule{number_match.group(1)}_can.json" if number_match else f"{path.stem}_can.json"
            (output_path / filename).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            total_units += len(result["legal_units"])
        print(f"Wrote {len(input_files)} canonical Schedule files with {total_units} units to {output_path}")
        return

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    result = ScheduleCanonicalizer(act_name=args.act_name, jurisdiction=args.jurisdiction).convert(raw, str(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(result['legal_units'])} canonical Schedule units to {output_path}")


if __name__ == "__main__":
    main()
