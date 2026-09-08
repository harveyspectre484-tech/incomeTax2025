from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DIRECT_TEXT_KEYS = (
    "text",
    "text_before_subsections",
    "text_before_clauses",
    "text_after_clauses",
    "text_before_sub_clauses",
    "text_after_sub_clauses",
    "text_before_items",
    "text_after_items",
    "text_before_table",
    "text_after_table",
    "intro",
    "formula",
)

CHILD_LIST_KEYS = (
    "subsections",
    "clauses",
    "sub_clauses",
    "items",
    "children",
)

STRUCTURAL_KEYS = set(DIRECT_TEXT_KEYS + CHILD_LIST_KEYS + ("table",))


@dataclass
class Canonicalizer:
    act_name: str = "Income-tax Act, 2025"
    jurisdiction: str = "India"
    units: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)

    def convert(self, raw: dict[str, Any]) -> dict[str, Any]:
        for section in raw.get("sections", []):
            self.add_section(section)
        return {"legal_units": self.units}

    def add_section(self, section: dict[str, Any]) -> None:
        section_number = str(section["number"])
        section_id = str(section.get("id") or section_number)
        title = clean(section.get("title", ""))
        act = section.get("act") or self.act_name
        chapter = section.get("chapter", "")

        section_citation = f"Section {section_number}"
        section_text = render_node_body(section)
        section_context = join_text(section_citation, title, section_text)

        self.add_unit(
            source=section,
            unit_id=section_id,
            citation=section_citation,
            unit_type="section",
            parent_id=None,
            section_id=section_id,
            section_number=section_number,
            title=title,
            text=section_text,
            context_text=section_context,
            act=act,
            chapter=chapter,
        )

        if not has_explicit_children(section):
            self.add_inline_clause_units(
                source=section,
                text=section_text,
                parent_id=section_id,
                parent_context=join_text(section_citation, title),
                section_id=section_id,
                section_number=section_number,
                title=title,
                citation_parts=[section_number],
                act=act,
                chapter=chapter,
            )

        for subsection in section.get("subsections", []):
            self.add_provision_node(
                node=subsection,
                unit_type="subsection",
                parent_id=section_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                citation_parts=[section_number, subsection.get("number")],
                ancestor_context=join_text(section_citation, title),
                act=act,
                chapter=chapter,
            )
        for clause in section.get("clauses", []) or []:
            clause_number = clause.get("number") or clause.get("marker") or clause.get("letter") or clause.get("numeral")
            self.add_provision_node(
                node=clause,
                unit_type=clause.get("type") or "clause",
                parent_id=section_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                citation_parts=[section_number, clause_number],
                ancestor_context=join_text(section_citation, title, section.get("text_before_clauses", "")),
                act=act,
                chapter=chapter,
            )

    def add_provision_node(
        self,
        node: dict[str, Any],
        unit_type: str,
        parent_id: str,
        section_id: str,
        section_number: str,
        title: str,
        citation_parts: list[Any],
        ancestor_context: str,
        act: str,
        chapter: str,
    ) -> None:
        unit_id = str(node.get("id") or "-".join(str(part) for part in citation_parts if part))
        citation = citation_from_parts(citation_parts)
        text = render_node_body(node)
        context_text = join_text(ancestor_context, f"{citation}: {text}")

        self.add_unit(
            source=node,
            unit_id=unit_id,
            citation=citation,
            unit_type=unit_type,
            parent_id=parent_id,
            section_id=section_id,
            section_number=section_number,
            title=title,
            text=text,
            context_text=context_text,
            act=act,
            chapter=chapter,
        )

        if not has_explicit_children(node):
            self.add_inline_clause_units(
                source=node,
                text=text,
                parent_id=unit_id,
                parent_context=ancestor_context,
                section_id=section_id,
                section_number=section_number,
                title=title,
                citation_parts=citation_parts,
                act=act,
                chapter=chapter,
            )

        if isinstance(node.get("table"), dict):
            self.add_table(
                table=node["table"],
                parent_id=unit_id,
                parent_citation=citation,
                section_id=section_id,
                section_number=section_number,
                title=title,
                ancestor_context=context_text,
                act=act,
                chapter=chapter,
            )

        child_specs = [
            ("clauses", "clause"),
            ("sub_clauses", "sub_clause"),
            ("items", "item"),
            ("children", "item"),
        ]
        for child_key, child_type in child_specs:
            for child in node.get(child_key, []) or []:
                child_number = child.get("number") or child.get("marker") or child.get("letter") or child.get("numeral")
                self.add_provision_node(
                    node=child,
                    unit_type=child.get("type") or child_type,
                    parent_id=unit_id,
                    section_id=section_id,
                    section_number=section_number,
                    title=title,
                    citation_parts=citation_parts + [child_number],
                    ancestor_context=context_text,
                    act=act,
                    chapter=chapter,
                )

    def add_table(
        self,
        table: dict[str, Any],
        parent_id: str,
        parent_citation: str,
        section_id: str,
        section_number: str,
        title: str,
        ancestor_context: str,
        act: str,
        chapter: str,
    ) -> None:
        table_id = str(table.get("id") or f"{parent_id}-table")
        columns = table.get("columns", [])
        table_citation = f"{parent_citation} Table"
        table_text = "Table columns: " + "; ".join(columns)
        table_context = join_text(ancestor_context, table_text)

        self.add_unit(
            source=table,
            unit_id=table_id,
            citation=table_citation,
            unit_type="table",
            parent_id=parent_id,
            section_id=section_id,
            section_number=section_number,
            title=title,
            text=table_text,
            context_text=table_context,
            act=act,
            chapter=chapter,
            extra={"columns": columns},
        )

        for index, row in enumerate(table.get("rows", []) or [], start=1):
            row_number = str(row.get("sl_no") or index)
            row_id = f"{table_id}-row-{slug(row_number)}"
            row_citation = f"{table_citation}, Sl. No. {row_number}"
            column_values = self.render_row_columns(row)
            row_text = "; ".join(f"{humanize_key(key)}: {value}" for key, value in column_values.items() if value)
            row_context = join_text(table_context, row_citation, row_text)

            self.add_unit(
                source=row,
                unit_id=row_id,
                citation=row_citation,
                unit_type="table_row",
                parent_id=table_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                text=row_text,
                context_text=row_context,
                act=act,
                chapter=chapter,
                extra={"row_number": row_number, "columns": column_values},
            )

            for column_key, cell in row.items():
                if column_key == "sl_no":
                    continue
                self.add_complex_cell_items(
                    cell=cell,
                    row_id=row_id,
                    row_citation=row_citation,
                    column_key=column_key,
                    section_id=section_id,
                    section_number=section_number,
                    title=title,
                    ancestor_context=row_context,
                    act=act,
                    chapter=chapter,
                )

    def render_row_columns(self, row: dict[str, Any]) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, value in row.items():
            if key == "sl_no":
                continue
            values[key] = render_cell(value)
        return values

    def add_complex_cell_items(
        self,
        cell: Any,
        row_id: str,
        row_citation: str,
        column_key: str,
        section_id: str,
        section_number: str,
        title: str,
        ancestor_context: str,
        act: str,
        chapter: str,
    ) -> None:
        if not isinstance(cell, dict):
            return
        if not has_nested_legal_items(cell):
            return

        column_label = humanize_key(column_key)
        for seq, item in enumerate(iter_nested_items(cell), start=1):
            marker = item.get("marker") or item.get("letter") or item.get("number") or item.get("numeral") or seq
            item_id = f"{row_id}-{slug(column_key)}-{slug(str(marker))}-{seq}"
            item_id = self.unique_id(item_id)
            item_text = render_cell(item)
            item_citation = f"{row_citation}, Column {column_label}, item ({marker})"
            item_context = join_text(
                ancestor_context,
                f"Column {column_label}, item ({marker}): {item_text}",
            )
            self.add_unit(
                source=item,
                unit_id=item_id,
                citation=item_citation,
                unit_type="table_cell_item",
                parent_id=row_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                text=item_text,
                context_text=item_context,
                act=act,
                chapter=chapter,
                extra={
                    "column": column_key,
                    "column_label": column_label,
                    "source_id": item.get("id"),
                },
                already_unique=True,
            )

    def add_inline_clause_units(
        self,
        source: dict[str, Any],
        text: str,
        parent_id: str,
        parent_context: str,
        section_id: str,
        section_number: str,
        title: str,
        citation_parts: list[Any],
        act: str,
        chapter: str,
    ) -> None:
        split = split_inline_lettered_clauses(text)
        if not split:
            return

        intro, clauses = split
        clause_parent_context = join_text(parent_context, intro)
        for marker, clause_text in clauses:
            unit_id = "-".join(str(part) for part in citation_parts + [marker] if part)
            clause_citation = citation_from_parts(citation_parts + [marker])
            context_text = join_text(clause_parent_context, f"{clause_citation}: {clause_text}")
            self.add_unit(
                source=source,
                unit_id=unit_id,
                citation=clause_citation,
                unit_type="clause",
                parent_id=parent_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                text=clause_text,
                context_text=context_text,
                act=act,
                chapter=chapter,
                extra={
                    "generated_from_inline_text": True,
                    "inline_intro": intro,
                },
            )

    def add_unit(
        self,
        source: dict[str, Any],
        unit_id: str,
        citation: str,
        unit_type: str,
        parent_id: str | None,
        section_id: str,
        section_number: str,
        title: str,
        text: str,
        context_text: str,
        act: str,
        chapter: str,
        extra: dict[str, Any] | None = None,
        already_unique: bool = False,
    ) -> None:
        final_id = unit_id if already_unique else self.unique_id(unit_id)
        unit = {
            "id": final_id,
            "citation": citation,
            "unit_type": normalize_type(unit_type),
            "parent_id": parent_id,
            "section_id": section_id,
            "section_number": section_number,
            "title": title,
            "text": clean(text),
            "context_text": clean(context_text),
            "act": act,
            "chapter": chapter,
            "jurisdiction": self.jurisdiction,
            "page_start": source.get("page_start"),
            "page_end": source.get("page_end"),
            "line_start": source.get("line_start"),
            "x": source.get("x"),
            "y_start": source.get("y_start"),
        }
        if extra:
            unit.update(extra)
        self.units.append(unit)

    def unique_id(self, raw_id: str) -> str:
        base = slug(raw_id)
        candidate = base
        counter = 2
        while candidate in self.seen_ids:
            candidate = f"{base}-{counter}"
            counter += 1
        self.seen_ids.add(candidate)
        return candidate


def render_node_body(node: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in DIRECT_TEXT_KEYS:
        if key in node and node[key]:
            parts.append(render_cell(node[key]))

    if isinstance(node.get("table"), dict):
        parts.append(render_table_summary(node["table"]))

    for child_key in CHILD_LIST_KEYS:
        for child in node.get(child_key, []) or []:
            marker = child.get("number") or child.get("marker") or child.get("letter") or child.get("numeral")
            child_text = render_node_body(child)
            if child_text:
                parts.append(f"({marker}) {child_text}" if marker else child_text)

    return join_text(*parts)


def render_table_summary(table: dict[str, Any]) -> str:
    columns = table.get("columns", [])
    row_count = len(table.get("rows", []) or [])
    return f"Table with columns {', '.join(columns)} and {row_count} rows."


def render_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return join_text(*(render_cell(item) for item in value))
    if not isinstance(value, dict):
        return clean(str(value))

    parts: list[str] = []
    for key in DIRECT_TEXT_KEYS:
        if key in value and value[key]:
            if key == "formula":
                parts.append(f"Formula: {render_cell(value[key])}")
            else:
                parts.append(render_cell(value[key]))

    for child_key in CHILD_LIST_KEYS:
        for child in value.get(child_key, []) or []:
            marker = child.get("marker") or child.get("letter") or child.get("number") or child.get("numeral")
            child_text = render_cell(child)
            if child_text:
                parts.append(f"({marker}) {child_text}" if marker else child_text)

    return join_text(*parts)


def iter_nested_items(cell: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for child_key in CHILD_LIST_KEYS:
        for child in cell.get(child_key, []) or []:
            if isinstance(child, dict):
                found.append(child)
                found.extend(iter_nested_items(child))
    return found


def has_nested_legal_items(cell: dict[str, Any]) -> bool:
    return any(isinstance(cell.get(key), list) and cell.get(key) for key in CHILD_LIST_KEYS) or bool(cell.get("formula"))


def has_explicit_children(node: dict[str, Any]) -> bool:
    if isinstance(node.get("table"), dict):
        return True
    return any(isinstance(node.get(key), list) and bool(node.get(key)) for key in CHILD_LIST_KEYS)


def split_inline_lettered_clauses(text: str) -> tuple[str, list[tuple[str, str]]] | None:
    cleaned = clean(text)
    matches = [
        match
        for match in re.finditer(r"(?<![A-Za-z0-9])\(([a-z])\)\s+", cleaned)
        if is_probable_top_level_inline_clause(cleaned, match)
    ]
    if len(matches) < 2:
        return None

    markers = [match.group(1) for match in matches]
    if markers[0] != "a":
        return None

    expected = [chr(ord("a") + index) for index in range(len(markers))]
    if markers != expected:
        return None

    intro = clean(cleaned[: matches[0].start()])
    clauses: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        clause_text = clean(cleaned[start:end])
        if clause_text:
            clauses.append((match.group(1), clause_text))

    if len(clauses) < 2:
        return None
    return intro, clauses


def is_probable_top_level_inline_clause(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 30) : match.start()].lower()
    if re.search(r"\b(clause|section|sub-section|subsection|article|item)\s+$", before):
        return False

    prefix = text[: match.start()].rstrip()
    if not prefix:
        return True
    return prefix.endswith(("—", "-", ";", ":", ",")) or bool(
        re.search(r"(?:—|-|;|:|,)\s+(?:and|or)$", prefix, flags=re.IGNORECASE)
    )


def citation_from_parts(parts: list[Any]) -> str:
    section = str(parts[0])
    suffix = "".join(f"({part})" for part in parts[1:] if part is not None and str(part) != "")
    return f"Section {section}{suffix}"


def normalize_type(value: str) -> str:
    return clean(value).lower().replace("-", "_").replace(" ", "_")


def humanize_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


def join_text(*values: Any) -> str:
    return clean(" ".join(str(value) for value in values if value not in (None, "")))


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert structured statute JSON into canonical legal units.")
    parser.add_argument("input_json", help="Raw structured JSON file")
    parser.add_argument("output_json", help="Output canonical legal units JSON")
    parser.add_argument("--act-name", default="Income-tax Act, 2025")
    parser.add_argument("--jurisdiction", default="India")
    args = parser.parse_args()

    raw = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = Canonicalizer(act_name=args.act_name, jurisdiction=args.jurisdiction).convert(raw)
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(result['legal_units'])} canonical units to {args.output_json}")


if __name__ == "__main__":
    main()
