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
    "text_before_children",
    "text_after_children",
    "text_before_sub_items",
    "text_after_sub_items",
    "text_before_conditions",
    "text_after_conditions",
    "text_before_table",
    "text_after_table",
    "intro",
    "formula",
)

CHILD_LIST_KEYS = (
    "subsections",
    "subsection",
    "clauses",
    "clause",
    "sub_clauses",
    "sub_clause",
    "items",
    "item",
    "sub_items",
    "conditions",
    "children",
)

BLOCK_KEYS = (
    "where_block",
    "exclusion_block",
    "explanation_block",
)

BLOCK_LABELS = {
    "where_block": "Where block",
    "exclusion_block": "Exclusion block",
    "explanation_block": "Explanation block",
}

STRUCTURAL_KEYS = set(DIRECT_TEXT_KEYS + CHILD_LIST_KEYS + BLOCK_KEYS + ("table",))


@dataclass
class Canonicalizer:
    act_name: str = "Income-tax Act, 2025"
    jurisdiction: str = "India"
    units: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    edges: list[dict[str, Any]] = field(default_factory=list)
    seen_edges: set[tuple[str, str, str, str]] = field(default_factory=set)
    external_nodes: list[dict[str, Any]] = field(default_factory=list)
    external_nodes_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_act_registry: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    reference_aliases: dict[str, str] = field(default_factory=dict)
    reference_overrides: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    reference_scan_text_by_unit_id: dict[str, str] = field(default_factory=dict)

    def convert(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not self.external_act_registry:
            self.external_act_registry = discover_external_act_registry([raw])
        if not self.reference_aliases:
            self.reference_aliases = discover_reference_aliases([raw])
        section_numbers: list[str] = []
        for section in iter_source_sections(raw):
            section_numbers.append(str(section.get("number", "")))
            self.add_section(section)
        self.add_reference_edges()
        return {
            "section_number": section_numbers[0] if len(section_numbers) == 1 else "",
            "legal_units": self.units,
            "external_nodes": self.external_nodes,
            "graph_edges": self.edges,
        }

    def add_section(self, section: dict[str, Any]) -> None:
        section_number = str(section["number"])
        section_id = str(section.get("id") or section_number)
        title = clean(section.get("title", ""))
        act = section.get("act") or self.act_name
        chapter = section.get("chapter", "")

        section_citation = f"Section {section_number}"
        source_text = render_node_own_text(section)
        inline_split = split_inline_lettered_clauses(source_text) if not has_explicit_children(section) else None
        section_text = inline_split[0] if inline_split else source_text
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

        self.add_attached_blocks(
            source=section,
            parent_id=section_id,
            parent_citation=section_citation,
            section_id=section_id,
            section_number=section_number,
            title=title,
            ancestor_context=section_context,
            act=act,
            chapter=chapter,
        )

        if inline_split:
            self.add_inline_clause_units(
                source=section,
                split=inline_split,
                parent_id=section_id,
                parent_context=section_context,
                section_id=section_id,
                section_number=section_number,
                title=title,
                citation_parts=[section_number],
                act=act,
                chapter=chapter,
            )

        for subsection_key in ("subsections", "subsection"):
            for subsection in section.get(subsection_key, []) or []:
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

        for clause_key in ("clauses", "clause"):
            for clause in section.get(clause_key, []) or []:
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
        longline = detect_misnested_longline(node) if normalize_type(unit_type) == "subsection" else None
        excluded_text_keys = {"text_after_clauses"} if longline else set()
        source_text = render_node_own_text(node, exclude_keys=excluded_text_keys)
        inline_split = split_inline_lettered_clauses(source_text) if not has_explicit_children(node) else None
        text = inline_split[0] if inline_split else source_text
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

        self.add_attached_blocks(
            source=node,
            parent_id=unit_id,
            parent_citation=citation,
            section_id=section_id,
            section_number=section_number,
            title=title,
            ancestor_context=context_text,
            act=act,
            chapter=chapter,
        )

        if inline_split:
            self.add_inline_clause_units(
                source=node,
                split=inline_split,
                parent_id=unit_id,
                parent_context=context_text,
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
            ("clause", "clause"),
            ("sub_clauses", "sub_clause"),
            ("sub_clause", "sub_clause"),
            ("items", "item"),
            ("item", "item"),
            ("sub_items", "sub_item"),
            ("conditions", "condition"),
            ("children", "item"),
        ]
        for child_key, child_type in child_specs:
            for child in node.get(child_key, []) or []:
                child_node = child
                if longline and child_key == "clauses" and str(child.get("id", "")) == longline["owner_id"]:
                    child_node = dict(child)
                    child_node["sub_clauses"] = []
                child_number = child.get("number") or child.get("marker") or child.get("letter") or child.get("numeral")
                self.add_provision_node(
                    node=child_node,
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

        if longline:
            self.add_longline(
                spec=longline,
                source=node,
                parent_id=unit_id,
                parent_citation=citation,
                section_id=section_id,
                section_number=section_number,
                title=title,
                citation_parts=citation_parts,
                ancestor_context=context_text,
                act=act,
                chapter=chapter,
            )

    def add_attached_blocks(
        self,
        source: dict[str, Any],
        parent_id: str,
        parent_citation: str,
        section_id: str,
        section_number: str,
        title: str,
        ancestor_context: str,
        act: str,
        chapter: str,
    ) -> None:
        for block_key in BLOCK_KEYS:
            block = source.get(block_key)
            if not isinstance(block, dict):
                continue
            block_type = normalize_type(block.get("type") or block_key)
            block_label = BLOCK_LABELS.get(block_type, humanize_key(block_type))
            block_id = str(block.get("id") or f"{parent_id}-{slug(block_type)}")
            block_citation = f"{parent_citation}, {block_label}"
            block_text = render_node_own_text(block)
            block_context = join_text(ancestor_context, f"{block_citation}: {block_text}")

            self.add_unit(
                source=block,
                unit_id=block_id,
                citation=block_citation,
                unit_type=block_type,
                parent_id=parent_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                text=block_text,
                context_text=block_context,
                act=act,
                chapter=chapter,
            )

            for child_key, child_type in (
                ("clauses", "clause"),
                ("clause", "clause"),
                ("sub_clauses", "sub_clause"),
                ("sub_clause", "sub_clause"),
                ("items", "item"),
                ("item", "item"),
                ("sub_items", "sub_item"),
                ("conditions", "condition"),
                ("children", "item"),
            ):
                for child in block.get(child_key, []) or []:
                    if isinstance(child, dict):
                        self.add_block_child(
                            node=child,
                            fallback_type=child_type,
                            parent_id=block_id,
                            parent_citation=block_citation,
                            section_id=section_id,
                            section_number=section_number,
                            title=title,
                            ancestor_context=block_context,
                            act=act,
                            chapter=chapter,
                        )

    def add_block_child(
        self,
        node: dict[str, Any],
        fallback_type: str,
        parent_id: str,
        parent_citation: str,
        section_id: str,
        section_number: str,
        title: str,
        ancestor_context: str,
        act: str,
        chapter: str,
    ) -> None:
        unit_type = normalize_type(node.get("type") or fallback_type)
        marker = node.get("number") or node.get("marker") or node.get("letter") or node.get("numeral")
        marker_text = str(marker) if marker is not None else "item"
        source_id = str(node.get("id") or "")
        derived_id = f"{parent_id}-{slug(marker_text)}"
        unit_id = source_id if source_id.startswith(f"{section_number}-") else derived_id
        label = {
            "clause": "Clause",
            "sub_clause": "Sub-clause",
            "item": "Item",
        }.get(unit_type, humanize_key(unit_type))
        citation = f"{parent_citation}, {label} ({marker_text})"
        text = render_node_own_text(node)
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
            extra={"source_id": source_id} if source_id and source_id != unit_id else None,
        )

        self.add_attached_blocks(
            source=node,
            parent_id=unit_id,
            parent_citation=citation,
            section_id=section_id,
            section_number=section_number,
            title=title,
            ancestor_context=context_text,
            act=act,
            chapter=chapter,
        )

        for child_key, child_type in (
            ("clauses", "clause"),
            ("clause", "clause"),
            ("sub_clauses", "sub_clause"),
            ("sub_clause", "sub_clause"),
            ("items", "item"),
            ("item", "item"),
            ("sub_items", "sub_item"),
            ("conditions", "condition"),
            ("children", "item"),
        ):
            for child in node.get(child_key, []) or []:
                if isinstance(child, dict):
                    self.add_block_child(
                        node=child,
                        fallback_type=child_type,
                        parent_id=unit_id,
                        parent_citation=citation,
                        section_id=section_id,
                        section_number=section_number,
                        title=title,
                        ancestor_context=context_text,
                        act=act,
                        chapter=chapter,
                    )

    def add_longline(
        self,
        spec: dict[str, Any],
        source: dict[str, Any],
        parent_id: str,
        parent_citation: str,
        section_id: str,
        section_number: str,
        title: str,
        citation_parts: list[Any],
        ancestor_context: str,
        act: str,
        chapter: str,
    ) -> None:
        longline_id = f"{parent_id}-longline"
        longline_citation = f"Longline of {parent_citation}"
        before_items = spec["text_before_items"]
        after_items = spec["text_after_items"]
        longline_text = join_text(before_items, after_items)
        longline_context = join_text(ancestor_context, longline_citation, longline_text)

        self.add_unit(
            source=source,
            unit_id=longline_id,
            citation=longline_citation,
            unit_type="longline",
            parent_id=parent_id,
            section_id=section_id,
            section_number=section_number,
            title=title,
            text=longline_text,
            context_text=longline_context,
            act=act,
            chapter=chapter,
            extra={
                "text_before_items": before_items,
                "text_after_items": after_items,
                "generated_from_misnested_longline": True,
            },
        )

        for item in spec["items"]:
            marker = item.get("number") or item.get("marker") or item.get("letter") or item.get("numeral")
            item_id = "-".join(str(part) for part in citation_parts + [marker] if part)
            item_text = render_node_own_text(item)
            item_citation = f"Clause ({marker}) of the longline in {parent_citation}"
            item_context = join_text(
                ancestor_context,
                before_items,
                f"{item_citation}: {item_text}",
                after_items,
            )
            self.add_unit(
                source=item,
                unit_id=item_id,
                citation=item_citation,
                unit_type="longline_clause",
                parent_id=longline_id,
                section_id=section_id,
                section_number=section_number,
                title=title,
                text=item_text,
                context_text=item_context,
                act=act,
                chapter=chapter,
                extra={
                    "source_id": item.get("id"),
                    "generated_from_misnested_longline": True,
                },
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
                    # A cell item is indexed independently. Its row can be
                    # recovered through parent_id during graph expansion.
                    ancestor_context=table_context,
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
        if isinstance(cell, list):
            cell = {"items": cell}
        if not isinstance(cell, dict):
            return
        if not has_nested_legal_items(cell):
            return

        column_label = humanize_key(column_key)
        self.add_attached_blocks(
            source=cell,
            parent_id=row_id,
            parent_citation=f"{row_citation}, Column {column_label}",
            section_id=section_id,
            section_number=section_number,
            title=title,
            ancestor_context=ancestor_context,
            act=act,
            chapter=chapter,
        )
        for seq, item in enumerate(iter_nested_items(cell), start=1):
            marker = item.get("marker") or item.get("letter") or item.get("number") or item.get("numeral") or seq
            item_id = f"{row_id}-{slug(column_key)}-{slug(str(marker))}-{seq}"
            item_id = self.unique_id(item_id)
            item_text = render_node_own_text(item)
            item_citation = f"{row_citation}, Column {column_label}, item ({marker})"
            item_context = join_text(
                ancestor_context,
                row_citation,
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
            self.add_attached_blocks(
                source=item,
                parent_id=item_id,
                parent_citation=item_citation,
                section_id=section_id,
                section_number=section_number,
                title=title,
                ancestor_context=item_context,
                act=act,
                chapter=chapter,
            )

    def add_inline_clause_units(
        self,
        source: dict[str, Any],
        split: tuple[str, list[tuple[str, str]]],
        parent_id: str,
        parent_context: str,
        section_id: str,
        section_number: str,
        title: str,
        citation_parts: list[Any],
        act: str,
        chapter: str,
    ) -> None:
        intro, clauses = split
        clause_parent_context = parent_context
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
            "retrieval_text": clean(context_text),
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
        self.reference_scan_text_by_unit_id[final_id] = render_reference_scan_text(source, text)
        if parent_id is not None:
            self.add_edge(final_id, str(parent_id), "parent")
            self.add_edge(str(parent_id), final_id, "contains")

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

    def add_external_node(self, node: dict[str, Any]) -> None:
        node_id = str(node["id"])
        existing = self.external_nodes_by_id.get(node_id)
        if existing:
            mentions = existing.setdefault("source_mentions", [])
            for mention in node.get("source_mentions", []):
                if mention not in mentions:
                    mentions.append(mention)
            return
        self.external_nodes_by_id[node_id] = node
        self.external_nodes.append(node)

    def add_reference_edges(self) -> None:
        units_by_id = {str(unit["id"]): unit for unit in self.units}
        for unit in self.units:
            source_id = str(unit["id"])
            text = clean(self.reference_scan_text_by_unit_id.get(source_id, unit.get("text", "")))
            if not text:
                continue
            references, external_nodes = extract_references(
                unit,
                text,
                units_by_id,
                self.external_act_registry,
                self.reference_aliases,
            )
            references, override_nodes = apply_reference_overrides(
                source_id,
                references,
                self.reference_overrides,
            )
            external_nodes.extend(override_nodes)
            for external_node in external_nodes:
                self.add_external_node(external_node)
            for reference in references:
                self.add_edge(
                    source_id,
                    reference["to_id"],
                    reference["edge_type"],
                    reference["reference_text"],
                    extra={key: value for key, value in reference.items() if key not in {"to_id", "edge_type", "reference_text"}},
                )

    def unique_id(self, raw_id: str) -> str:
        base = slug(raw_id)
        candidate = base
        counter = 2
        while candidate in self.seen_ids:
            candidate = f"{base}-{counter}"
            counter += 1
        self.seen_ids.add(candidate)
        return candidate


def iter_source_sections(raw: dict[str, Any]):
    seen: set[str] = set()
    if normalize_type(str(raw.get("type", ""))) == "section" and raw.get("number") is not None:
        identity = str(raw.get("id") or raw.get("number"))
        seen.add(identity)
        yield raw

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
            if section.get("chapter"):
                yield section
            else:
                section_with_chapter = dict(section)
                section_with_chapter["chapter"] = chapter.get("number") or chapter.get("title", "")
                yield section_with_chapter


def detect_misnested_longline(node: dict[str, Any]) -> dict[str, Any] | None:
    """Detect Roman-numbered longline items attached to the final lettered clause."""
    clauses = [*(node.get("clauses", []) or []), *(node.get("clause", []) or [])]
    tail_text = clean(node.get("text_after_clauses", ""))
    if not clauses or not tail_text:
        return None

    owner = clauses[-1]
    items = [*(owner.get("sub_clauses", []) or []), *(owner.get("sub_clause", []) or [])]
    if len(items) < 2:
        return None

    markers = [
        str(item.get("number") or item.get("marker") or item.get("letter") or item.get("numeral") or "").lower()
        for item in items
    ]
    if not all(re.fullmatch(r"[ivxlcdm]+", marker) for marker in markers):
        return None

    owner_x = numeric(owner.get("x"))
    item_x_values = [numeric(item.get("x")) for item in items]
    positioned_items = [value for value in item_x_values if value is not None]
    if owner_x is not None and positioned_items and any(value > owner_x + 5 for value in positioned_items):
        return None

    split = re.match(r"^(.*?[—–])\s*(.+)$", tail_text)
    if not split:
        return None
    before_items = clean(split.group(1))
    after_items = clean(split.group(2))

    referenced_markers = {
        match.group(1).lower()
        for match in re.finditer(r"\bclause\s+\(([ivxlcdm]+)\)", after_items, flags=re.IGNORECASE)
    }
    if not set(markers).issubset(referenced_markers):
        return None

    return {
        "owner_id": str(owner.get("id", "")),
        "items": items,
        "text_before_items": before_items,
        "text_after_items": after_items,
    }


def render_node_own_text(node: dict[str, Any], exclude_keys: set[str] | None = None) -> str:
    """Render only text owned by this node, never text from descendants."""
    excluded = exclude_keys or set()
    parts: list[str] = []
    for key in DIRECT_TEXT_KEYS:
        if key not in excluded and key in node and node[key]:
            parts.append(render_cell(node[key]))
    return join_text(*parts)


def render_reference_scan_text(source: dict[str, Any], fallback_text: str) -> str:
    """Repair an Act title split into a false word-labelled item by source parsing."""
    before = clean(source.get("text_before_clauses", ""))
    if not before:
        return clean(fallback_text)
    continuation = next(
        (
            clean(item.get("text", ""))
            for item in source.get("items", []) or []
            if isinstance(item, dict)
            and clean(item.get("text", "")).lower().startswith("act,")
            and len(clean(item.get("number") or item.get("marker") or "")) > 1
        ),
        "",
    )
    if not continuation:
        return clean(fallback_text)
    return join_text(before, continuation, source.get("text_after_clauses", ""))


ACT_TITLE_TOKEN = r"(?:[A-Z][A-Za-z0-9&'’.-]*|of|and|the|for|to|with|in|on|or|tax|\([^)]{1,120}\))"
EXTERNAL_ACT_PATTERN = re.compile(
    rf"(?P<title>(?!Act\b)[A-Z][A-Za-z0-9&'’.-]*(?:\s+{ACT_TITLE_TOKEN})*\s+Act),?\s*"
    r"(?P<title_year>\d{4})"
    r"(?:\s*\((?P<act_number>\d+)\s+of\s+(?P<act_year>\d{4})\))?"
)
EXTERNAL_LOCATOR_SUFFIX = re.compile(
    r"(?P<full>(?:clause\s+\((?P<leading_clause>[a-z0-9]+)\)\s+of\s+)?"
    r"section\s+(?P<section>\d+[A-Za-z-]*)(?P<nested>(?:\([a-z0-9]+\)){0,4})"
    r"\s+of\s+(?:the\s+)?)$",
    flags=re.IGNORECASE,
)


def extract_references(
    source_unit: dict[str, Any],
    text: str,
    units_by_id: dict[str, dict[str, Any]],
    external_act_registry: dict[tuple[str, str], dict[str, str]],
    reference_aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    references: list[dict[str, Any]] = []
    external_nodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    consumed_spans: list[tuple[int, int]] = []
    external_act_mentions: list[dict[str, Any]] = []

    def add(target_id: str, reference_text: str, edge_type: str = "references", **metadata: Any) -> None:
        key = (target_id, clean(reference_text).lower(), edge_type)
        if key in seen:
            return
        seen.add(key)
        references.append(
            {
                "to_id": target_id,
                "edge_type": edge_type,
                "reference_text": clean(reference_text),
                **metadata,
            }
        )

    def add_external_provision(
        act: dict[str, Any],
        section: str,
        nested: list[str],
        reference_text: str,
    ) -> None:
        locator_parts = [section, *nested]
        provision_id = f"{act['act_id']}-section-" + "-".join(slug(part).lower() for part in locator_parts)
        locator_text = f"section {section}" + "".join(f"({part})" for part in nested)
        external_nodes.append(
            {
                "id": provision_id,
                "node_type": "external_provision_placeholder",
                "parent_id": act["act_id"],
                "jurisdiction": "India",
                "act_title": act["act_title"],
                "title_year": act["title_year"],
                "act_number": act["act_number"],
                "act_year": act["act_year"],
                "official_citation": act["official_citation"],
                "identity_basis": act["identity_basis"],
                "locator_text": locator_text,
                "section": section,
                "nested_locators": nested,
                "resolution_status": act["resolution_status"],
                "title_year_differs_from_enactment_year": act["year_difference"],
                "source_mentions": [clean(reference_text)],
            }
        )
        add(
            provision_id,
            reference_text,
            "references_external",
            target_act_id=act["act_id"],
            target_act_title=act["act_title"],
            target_section=section,
            target_nested_locators=nested,
            resolution_status=act["resolution_status"],
            target_act_number=act["act_number"],
            target_act_year=act["act_year"],
            target_act_title_year=act["title_year"],
            target_official_citation=act["official_citation"],
            target_identity_basis=act["identity_basis"],
        )

    for act_match in EXTERNAL_ACT_PATTERN.finditer(text):
        raw_act_title = clean(act_match.group("title")).replace("Income- tax", "Income-tax")
        act_title, removed_title_prefix = normalize_external_act_title(raw_act_title)
        title_year = act_match.group("title_year")
        act_number = act_match.group("act_number")
        act_year = act_match.group("act_year")
        if is_current_act_reference(act_title, title_year):
            continue

        identity_basis = "official_citation"
        if not act_number or not act_year:
            registered = external_act_registry.get((normalize_act_identity_title(act_title), title_year))
            if registered:
                act_number = registered["act_number"]
                act_year = registered["act_year"]
                identity_basis = "corpus_registry"
            else:
                identity_basis = "title_and_year"

        if act_number and act_year:
            act_id = f"ext-india-act-{act_year}-{act_number}"
            official_citation = f"{act_number} of {act_year}"
        else:
            act_id = f"ext-india-act-{title_year}-{slug(act_title).lower()}"
            official_citation = None

        year_difference = bool(act_year and title_year != act_year)
        resolution_status = "external_act_not_ingested"
        act_reference_text = clean(act_match.group(0))
        act = {
            "act_id": act_id,
            "act_title": act_title,
            "title_year": title_year,
            "act_number": act_number,
            "act_year": act_year,
            "official_citation": official_citation,
            "identity_basis": identity_basis,
            "resolution_status": resolution_status,
            "year_difference": year_difference,
            "match_start": act_match.start(),
            "match_end": act_match.end(),
        }
        external_act_mentions.append(act)
        external_nodes.append(
            {
                "id": act_id,
                "node_type": "external_act_placeholder",
                "jurisdiction": "India",
                "act_title": act_title,
                "title_year": title_year,
                "act_number": act_number,
                "act_year": act_year,
                "official_citation": official_citation,
                "identity_basis": identity_basis,
                "resolution_status": resolution_status,
                "title_year_differs_from_enactment_year": year_difference,
                "source_mentions": [act_reference_text],
            }
        )

        effective_title_start = act_match.start("title") + len(removed_title_prefix)
        prefix_start = max(0, effective_title_start - 260)
        prefix = text[prefix_start:effective_title_start]
        locator_match = EXTERNAL_LOCATOR_SUFFIX.search(prefix)
        if locator_match:
            nested = re.findall(r"\(([a-z0-9]+)\)", locator_match.group("nested"), flags=re.IGNORECASE)
            leading_clause = locator_match.group("leading_clause")
            if leading_clause:
                nested.append(leading_clause)
            section = locator_match.group("section")
            reference_start = prefix_start + locator_match.start("full")
            reference_text = text[reference_start : act_match.end()]
            consumed_spans.append((reference_start, act_match.end()))
            add_external_provision(act, section, nested, reference_text)
        else:
            compound = find_compound_external_locators(prefix)
            if compound:
                compound_start, locators = compound
                reference_start = prefix_start + compound_start
                reference_text = text[reference_start : act_match.end()]
                consumed_spans.append((reference_start, act_match.end()))
                for section, nested in locators:
                    add_external_provision(act, section, nested, reference_text)
            else:
                consumed_spans.append(act_match.span())
                add(
                    act_id,
                    act_reference_text,
                    "references_external_act",
                    target_act_id=act_id,
                    target_act_title=act_title,
                    resolution_status=resolution_status,
                    target_act_number=act_number,
                    target_act_year=act_year,
                    target_act_title_year=title_year,
                    target_official_citation=official_citation,
                    target_identity_basis=identity_basis,
                )

    for match in re.finditer(
        r"\bsection\s+(\d+[A-Z-]*)(?P<nested>(?:\([a-z0-9]+\)){0,4})\s+of\s+"
        r"(?:the\s+)?(?:said|that)\s+Act\b",
        text,
        flags=re.IGNORECASE,
    ):
        if overlaps_any(match.span(), consumed_spans):
            continue
        preceding_acts = [act for act in external_act_mentions if act["match_start"] < match.start()]
        if not preceding_acts:
            continue
        act = max(preceding_acts, key=lambda item: item["match_start"])
        nested = re.findall(r"\(([a-z0-9]+)\)", match.group("nested"), flags=re.IGNORECASE)
        add_external_provision(act, match.group(1), nested, match.group(0))
        consumed_spans.append(match.span())

    for match in re.finditer(
        r"\bsection\s+(\d+[A-Z-]*)(?:\(([a-z0-9]+)\))?(?:\(([a-z0-9]+)\))?",
        text,
        flags=re.IGNORECASE,
    ):
        if overlaps_any(match.span(), consumed_spans):
            continue
        parts = [match.group(1), match.group(2), match.group(3)]
        target_id = "-".join(part for part in parts if part)
        add(reference_aliases.get(target_id, target_id), match.group(0))

    section_number = str(source_unit.get("section_number", ""))
    for match in re.finditer(
        r"\bsub-?section\s+\((\d+[a-z]*)\)(?:\(([a-z0-9]+)\))?",
        text,
        flags=re.IGNORECASE,
    ):
        if overlaps_any(match.span(), consumed_spans):
            continue
        target_id = f"{section_number}-{match.group(1)}"
        if match.group(2):
            target_id += f"-{match.group(2)}"
        add(target_id, match.group(0))

    for match in re.finditer(r"\bclause\s+\(([a-z0-9]+)\)", text, flags=re.IGNORECASE):
        if overlaps_any(match.span(), consumed_spans):
            continue
        marker = match.group(1).lower()
        target_id = resolve_contextual_clause(source_unit, marker, text, match, units_by_id)
        if target_id:
            add(target_id, match.group(0))

    for match in re.finditer(r"\bSchedule\s+([IVXLCDM]+|\d+)\b", text, flags=re.IGNORECASE):
        if overlaps_any(match.span(), consumed_spans):
            continue
        raw_number = match.group(1)
        schedule_number = str(roman_to_int(raw_number) or raw_number)
        add(f"schedule-{schedule_number}", match.group(0))

    return references, external_nodes


def apply_reference_overrides(
    source_unit_id: str,
    references: list[dict[str, Any]],
    overrides: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not overrides:
        return references, []

    resolved: list[dict[str, Any]] = []
    external_nodes: list[dict[str, Any]] = []
    applied: set[tuple[str, str]] = set()
    for reference in references:
        key = (source_unit_id, normalize_reference_key(reference.get("reference_text", "")))
        override = overrides.get(key)
        if not override:
            resolved.append(reference)
            continue
        if key in applied:
            continue
        applied.add(key)
        for target in override.get("targets", []):
            materialized, nodes = materialize_override_target(override, target)
            resolved.append(materialized)
            external_nodes.extend(nodes)
    return resolved, external_nodes


def materialize_override_target(
    override: dict[str, Any],
    target: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reference_text = clean(target.get("reference_text") or override.get("reference_text", ""))
    common = {
        "resolution_basis": "human_override",
        "override_source": override.get("override_source", "reference_section.rtf"),
        "override_note": override.get("note", ""),
    }
    kind = target.get("kind", "internal")
    if kind in {"internal", "schedule"}:
        return (
            {
                "to_id": str(target["target_id"]),
                "edge_type": "references",
                "reference_text": reference_text,
                **common,
            },
            [],
        )

    instrument = target["instrument"]
    instrument_id = str(instrument["id"])
    section = str(target.get("section", ""))
    nested = [str(value) for value in target.get("nested", [])]
    qualifier = [str(value) for value in target.get("qualifier", [])]
    locator_parts = [section, *nested, *qualifier]
    target_id = str(
        target.get("target_id")
        or f"{instrument_id}-section-" + "-".join(slug(value).lower() for value in locator_parts if value)
    )
    identity_basis = instrument.get("identity_basis", "human_override")
    resolution_status = "external_act_not_ingested"
    act_node = {
        "id": instrument_id,
        "node_type": instrument.get("node_type", "external_act_placeholder"),
        "jurisdiction": instrument.get("jurisdiction", "India"),
        "act_title": instrument["title"],
        "title_year": instrument.get("title_year"),
        "act_number": instrument.get("act_number"),
        "act_year": instrument.get("act_year"),
        "official_citation": instrument.get("official_citation"),
        "identity_basis": identity_basis,
        "resolution_status": resolution_status,
        "title_year_differs_from_enactment_year": False,
        "source_mentions": [reference_text],
    }
    provision_node = {
        "id": target_id,
        "node_type": "external_provision_placeholder",
        "parent_id": instrument_id,
        "jurisdiction": instrument.get("jurisdiction", "India"),
        "act_title": instrument["title"],
        "title_year": instrument.get("title_year"),
        "act_number": instrument.get("act_number"),
        "act_year": instrument.get("act_year"),
        "official_citation": instrument.get("official_citation"),
        "identity_basis": identity_basis,
        "locator_text": target.get("locator_text") or reference_text,
        "section": section or None,
        "nested_locators": [*nested, *qualifier],
        "resolution_status": resolution_status,
        "title_year_differs_from_enactment_year": False,
        "source_mentions": [reference_text],
    }
    edge = {
        "to_id": target_id,
        "edge_type": "references_external",
        "reference_text": reference_text,
        "target_act_id": instrument_id,
        "target_act_title": instrument["title"],
        "target_section": section or None,
        "target_nested_locators": [*nested, *qualifier],
        "resolution_status": resolution_status,
        "target_act_number": instrument.get("act_number"),
        "target_act_year": instrument.get("act_year"),
        "target_act_title_year": instrument.get("title_year"),
        "target_official_citation": instrument.get("official_citation"),
        "target_identity_basis": identity_basis,
        **common,
    }
    return edge, [act_node, provision_node]


def normalize_reference_key(value: Any) -> str:
    return clean(value).lower()


def load_reference_overrides(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    instruments = data.get("external_instruments", {})
    loaded: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_override in data.get("overrides", []) or []:
        source_unit_id = str(raw_override["source_unit_id"])
        reference_text = clean(raw_override["reference_text"])
        targets: list[dict[str, Any]] = []
        for raw_target in raw_override.get("targets", []) or []:
            target = dict(raw_target)
            instrument_key = target.pop("instrument_key", None)
            if instrument_key:
                if instrument_key not in instruments:
                    raise ValueError(f"Unknown external instrument in reference overrides: {instrument_key}")
                target["instrument"] = instruments[instrument_key]
                target.setdefault("kind", "external")
            targets.append(target)
        key = (source_unit_id, normalize_reference_key(reference_text))
        if key in loaded:
            raise ValueError(f"Duplicate reference override for {source_unit_id}: {reference_text}")
        loaded[key] = {
            **raw_override,
            "reference_text": reference_text,
            "targets": targets,
            "override_source": data.get("source", path.name),
        }
    return loaded


def find_compound_external_locators(prefix: str) -> tuple[int, list[tuple[str, list[str]]]] | None:
    """Parse locators that share one trailing external Act name."""
    candidates = list(
        re.finditer(
            r"(?<!-)\bsection\s+(?P<body>[^.;:]{1,180}?)\s+of\s+(?:the\s+)?$",
            prefix,
            flags=re.IGNORECASE,
        )
    )
    if not candidates:
        return None

    candidate = candidates[-1]
    body = candidate.group("body")
    token_pattern = re.compile(
        r"\d+[A-Z-]*(?:\([a-z0-9]+\)){0,4}|\([a-z0-9]+\)",
        flags=re.IGNORECASE,
    )
    tokens = list(token_pattern.finditer(body))
    if len(tokens) < 2 or tokens[0].start() != 0:
        return None

    cursor = 0
    for token in tokens:
        gap = body[cursor : token.start()]
        if cursor and not re.fullmatch(r"\s*(?:,|and|or|and/or)?\s*", gap, flags=re.IGNORECASE):
            return None
        cursor = token.end()
    if body[cursor:].strip():
        return None

    locators: list[tuple[str, list[str]]] = []
    current_section = ""
    current_nested: list[str] = []
    for token_match in tokens:
        token = token_match.group(0)
        if token.startswith("("):
            if not current_section:
                return None
            marker = token[1:-1]
            current_nested = [*current_nested[:-1], marker]
        else:
            locator = re.fullmatch(
                r"(?P<section>\d+[A-Z-]*)(?P<nested>(?:\([a-z0-9]+\)){0,4})",
                token,
                flags=re.IGNORECASE,
            )
            if not locator:
                return None
            current_section = locator.group("section")
            current_nested = re.findall(r"\(([a-z0-9]+)\)", locator.group("nested"), flags=re.IGNORECASE)
        locators.append((current_section, list(current_nested)))

    return candidate.start(), locators


def overlaps_any(span: tuple[int, int], consumed_spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < consumed_end and consumed_start < span[1] for consumed_start, consumed_end in consumed_spans)


def is_current_act_reference(act_title: str, title_year: str) -> bool:
    normalized = normalize_act_identity_title(act_title)
    return normalized == "income tax act" and title_year == "2025"


def normalize_act_identity_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def discover_external_act_registry(raw_documents: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, str]]:
    identities: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for raw in raw_documents:
        for text in iter_string_values(raw):
            for match in EXTERNAL_ACT_PATTERN.finditer(text):
                act_number = match.group("act_number")
                act_year = match.group("act_year")
                if not act_number or not act_year:
                    continue
                raw_title = clean(match.group("title")).replace("Income- tax", "Income-tax")
                act_title, _ = normalize_external_act_title(raw_title)
                title_year = match.group("title_year")
                if is_current_act_reference(act_title, title_year):
                    continue
                key = (normalize_act_identity_title(act_title), title_year)
                identities.setdefault(key, set()).add((act_number, act_year))

    registry: dict[tuple[str, str], dict[str, str]] = {}
    for key, official_ids in identities.items():
        if len(official_ids) != 1:
            continue
        act_number, act_year = next(iter(official_ids))
        registry[key] = {"act_number": act_number, "act_year": act_year}
    return registry


def discover_reference_aliases(raw_documents: list[dict[str, Any]]) -> dict[str, str]:
    """Map statutory shorthand such as section 70(zd) to section 70(1)(zd)."""
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for raw in raw_documents:
        for section in iter_source_sections(raw):
            section_number = str(section.get("number", ""))
            subsection_one = next(
                (
                    subsection
                    for subsection in [
                        *(section.get("subsections", []) or []),
                        *(section.get("subsection", []) or []),
                    ]
                    if str(subsection.get("number", "")) == "1"
                ),
                None,
            )
            if not subsection_one:
                continue
            for child_key in ("clauses", "clause", "items", "item", "children"):
                for child in subsection_one.get(child_key, []) or []:
                    if not isinstance(child, dict):
                        continue
                    marker = child.get("number") or child.get("marker") or child.get("letter")
                    child_id = child.get("id")
                    if marker is None or not child_id:
                        continue
                    alias = f"{section_number}-{marker}"
                    target = str(child_id)
                    if alias in aliases and aliases[alias] != target:
                        ambiguous.add(alias)
                    else:
                        aliases[alias] = target
    for alias in ambiguous:
        aliases.pop(alias, None)
    return aliases


def iter_string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_string_values(child)


def normalize_external_act_title(raw_title: str) -> tuple[str, str]:
    patterns = (
        r"^(?P<prefix>(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|\d+(?:st|nd|rd|th)?)\s+Schedule\s+(?:of|to)\s+(?:the\s+)?)",
        r"^(?P<prefix>(?:Part|Chapter|Schedule)\s+[A-Za-z0-9-]+\s+(?:of|to)\s+(?:the\s+)?)",
        r"^(?P<prefix>[A-Z]{1,8}\s+of\s+(?:the\s+)?)",
    )
    title = raw_title
    removed = ""
    while True:
        for pattern in patterns:
            match = re.match(pattern, title)
            if match:
                prefix = match.group("prefix")
                removed += prefix
                title = clean(title[len(prefix) :])
                break
        else:
            return title, removed


def resolve_relative_clause(
    source_unit: dict[str, Any],
    marker: str,
    units_by_id: dict[str, dict[str, Any]],
) -> str:
    source_id = str(source_unit.get("id", ""))
    section_id = str(source_unit.get("section_id") or source_unit.get("section_number", ""))
    parent_by_id = {unit_id: unit.get("parent_id") for unit_id, unit in units_by_id.items()}

    if source_unit.get("unit_type") == "table_cell_item":
        source_sequence = table_item_sequence(source_id)
        sibling_candidates: list[tuple[int, str]] = []
        for unit_id, unit in units_by_id.items():
            if unit.get("unit_type") != "table_cell_item":
                continue
            if unit.get("parent_id") != source_unit.get("parent_id"):
                continue
            if unit.get("column") != source_unit.get("column"):
                continue
            match = re.search(rf"-{re.escape(marker)}-(\d+)$", unit_id, flags=re.IGNORECASE)
            if not match:
                continue
            sequence = int(match.group(1))
            if source_sequence is None or sequence < source_sequence:
                sibling_candidates.append((sequence, unit_id))
        if sibling_candidates:
            return max(sibling_candidates)[1]

    ancestor_ids: list[str] = []
    current_id: str | None = source_id
    while current_id and current_id not in ancestor_ids:
        ancestor_ids.append(current_id)
        current_id = parent_by_id.get(current_id)

    subsection_id = next(
        (unit_id for unit_id in ancestor_ids if units_by_id.get(unit_id, {}).get("unit_type") == "subsection"),
        None,
    )
    base_id = subsection_id or section_id
    direct_id = f"{base_id}-{marker}"
    if direct_id in units_by_id:
        return direct_id

    for ancestor_id in ancestor_ids:
        candidate = f"{ancestor_id}-{marker}"
        if candidate in units_by_id:
            return candidate

    candidates = []
    for unit_id, unit in units_by_id.items():
        if str(unit.get("section_number", "")) != str(source_unit.get("section_number", "")):
            continue
        citation = clean(unit.get("citation", ""))
        if not citation.lower().endswith(f"({marker})"):
            continue
        if subsection_id and not is_descendant_of(unit_id, subsection_id, parent_by_id):
            continue
        candidates.append(unit_id)
    if len(candidates) == 1:
        return candidates[0]
    return direct_id


def resolve_contextual_clause(
    source_unit: dict[str, Any],
    marker: str,
    text: str,
    match: re.Match[str],
    units_by_id: dict[str, dict[str, Any]],
) -> str | None:
    if source_unit.get("unit_type") == "table_row" and any(
        unit.get("unit_type") == "table_cell_item" and unit.get("parent_id") == source_unit.get("id")
        for unit in units_by_id.values()
    ):
        return None

    following = text[match.end() : match.end() + 60]
    explicit_subsection = re.match(
        r"\s+of\s+(?:sub-?section)\s+\((\d+[a-z]*)\)",
        following,
        flags=re.IGNORECASE,
    )
    section_number = str(source_unit.get("section_number", ""))
    if explicit_subsection:
        return f"{section_number}-{explicit_subsection.group(1)}-{marker}"

    if re.match(r"\s+thereof\b", following, flags=re.IGNORECASE):
        prior_subsections = list(
            re.finditer(r"\bsub-?section\s+\((\d+[a-z]*)\)", text[: match.start()], flags=re.IGNORECASE)
        )
        if prior_subsections:
            return f"{section_number}-{prior_subsections[-1].group(1)}-{marker}"

    return resolve_relative_clause(source_unit, marker, units_by_id)


def table_item_sequence(unit_id: str) -> int | None:
    match = re.search(r"-(\d+)$", unit_id)
    return int(match.group(1)) if match else None


def is_descendant_of(unit_id: str, ancestor_id: str, parent_by_id: dict[str, Any]) -> bool:
    current_id: str | None = unit_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        if current_id == ancestor_id:
            return True
        visited.add(current_id)
        parent = parent_by_id.get(current_id)
        current_id = str(parent) if parent is not None else None
    return False


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


def numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    return (
        any(isinstance(cell.get(key), list) and cell.get(key) for key in CHILD_LIST_KEYS)
        or any(isinstance(cell.get(key), dict) for key in BLOCK_KEYS)
        or bool(cell.get("formula"))
    )


def has_explicit_children(node: dict[str, Any]) -> bool:
    if isinstance(node.get("table"), dict):
        return True
    return (
        any(isinstance(node.get(key), list) and bool(node.get(key)) for key in CHILD_LIST_KEYS)
        or any(isinstance(node.get(key), dict) for key in BLOCK_KEYS)
    )


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
    parser.add_argument("input_json", help="Raw JSON file, or a directory containing *section.json files")
    parser.add_argument("output_json", help="Output JSON file, or output directory for directory input")
    parser.add_argument("--act-name", default="Income-tax Act, 2025")
    parser.add_argument("--jurisdiction", default="India")
    parser.add_argument(
        "--reference-overrides",
        help="Reviewed reference override JSON. Defaults to reference_overrides.json beside the input directory.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_json)
    override_path = Path(args.reference_overrides) if args.reference_overrides else default_override_path(input_path)
    reference_overrides = load_reference_overrides(override_path)

    if input_path.is_dir():
        output_path.mkdir(parents=True, exist_ok=True)
        input_files = sorted(input_path.glob("*section.json"), key=path_section_sort_key)
        raw_documents = [json.loads(raw_path.read_text(encoding="utf-8")) for raw_path in input_files]
        external_act_registry = discover_external_act_registry(raw_documents)
        reference_aliases = discover_reference_aliases(raw_documents)
        for raw_path, raw in zip(input_files, raw_documents):
            result = Canonicalizer(
                act_name=args.act_name,
                jurisdiction=args.jurisdiction,
                external_act_registry=external_act_registry,
                reference_aliases=reference_aliases,
                reference_overrides=reference_overrides,
            ).convert(raw)
            # Preserve one output per source file. A mismatched section number
            # must be exposed by validation, not overwrite another section.
            section_number = section_number_from_path(raw_path)
            destination = output_path / f"{section_number}section_can.json"
            destination.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote canonical JSON for {len(input_files)} section files to {output_path}")
        return

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    result = Canonicalizer(
        act_name=args.act_name,
        jurisdiction=args.jurisdiction,
        reference_overrides=reference_overrides,
    ).convert(raw)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(result['legal_units'])} canonical units to {output_path}")


def default_override_path(input_path: Path) -> Path | None:
    project_dir = input_path.parent if input_path.is_dir() else input_path.parent.parent
    candidate = project_dir / "reference_overrides.json"
    return candidate if candidate.exists() else None


def section_number_from_path(path: Path) -> str:
    match = re.match(r"(\d+)", path.name)
    return match.group(1) if match else path.stem


def path_section_sort_key(path: Path) -> tuple[int, str]:
    number = section_number_from_path(path)
    return (int(number), path.name) if number.isdigit() else (10**9, path.name)


if __name__ == "__main__":
    main()
