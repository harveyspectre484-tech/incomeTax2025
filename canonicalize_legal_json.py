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
    edges: list[dict[str, Any]] = field(default_factory=list)
    seen_edges: set[tuple[str, str, str, str]] = field(default_factory=set)
    external_nodes: list[dict[str, Any]] = field(default_factory=list)
    external_nodes_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_act_registry: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def convert(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not self.external_act_registry:
            self.external_act_registry = discover_external_act_registry([raw])
        section_numbers: list[str] = []
        for section in raw.get("sections", []):
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
            ("sub_clauses", "sub_clause"),
            ("items", "item"),
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
        if not isinstance(cell, dict):
            return
        if not has_nested_legal_items(cell):
            return

        column_label = humanize_key(column_key)
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
            text = clean(unit.get("text", ""))
            if not text:
                continue
            references, external_nodes = extract_references(
                unit,
                text,
                units_by_id,
                self.external_act_registry,
            )
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


def detect_misnested_longline(node: dict[str, Any]) -> dict[str, Any] | None:
    """Detect Roman-numbered longline items attached to the final lettered clause."""
    clauses = node.get("clauses", []) or []
    tail_text = clean(node.get("text_after_clauses", ""))
    if not clauses or not tail_text:
        return None

    owner = clauses[-1]
    items = owner.get("sub_clauses", []) or []
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    references: list[dict[str, Any]] = []
    external_nodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    consumed_spans: list[tuple[int, int]] = []

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
            locator_parts = [section, *nested]
            provision_id = f"{act_id}-section-" + "-".join(slug(part).lower() for part in locator_parts)
            reference_start = prefix_start + locator_match.start("full")
            reference_text = text[reference_start : act_match.end()]
            consumed_spans.append((reference_start, act_match.end()))
            locator_text = f"section {section}" + "".join(f"({part})" for part in nested)
            external_nodes.append(
                {
                    "id": provision_id,
                    "node_type": "external_provision_placeholder",
                    "parent_id": act_id,
                    "jurisdiction": "India",
                    "act_title": act_title,
                    "title_year": title_year,
                    "act_number": act_number,
                    "act_year": act_year,
                    "official_citation": official_citation,
                    "identity_basis": identity_basis,
                    "locator_text": locator_text,
                    "section": section,
                    "nested_locators": nested,
                    "resolution_status": resolution_status,
                    "title_year_differs_from_enactment_year": year_difference,
                    "source_mentions": [clean(reference_text)],
                }
            )
            add(
                provision_id,
                reference_text,
                "references_external",
                target_act_id=act_id,
                target_act_title=act_title,
                target_section=section,
                target_nested_locators=nested,
                resolution_status=resolution_status,
                target_act_number=act_number,
                target_act_year=act_year,
                target_act_title_year=title_year,
                target_official_citation=official_citation,
                target_identity_basis=identity_basis,
            )
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
        r"\bsection\s+(\d+[A-Z-]*)(?:\(([a-z0-9]+)\))?(?:\(([a-z0-9]+)\))?",
        text,
        flags=re.IGNORECASE,
    ):
        if overlaps_any(match.span(), consumed_spans):
            continue
        parts = [match.group(1), match.group(2), match.group(3)]
        add("-".join(part for part in parts if part), match.group(0))

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
        target_id = resolve_relative_clause(source_unit, marker, units_by_id)
        add(target_id, match.group(0))

    for match in re.finditer(r"\bSchedule\s+([IVXLCDM]+|\d+)\b", text, flags=re.IGNORECASE):
        if overlaps_any(match.span(), consumed_spans):
            continue
        raw_number = match.group(1)
        schedule_number = str(roman_to_int(raw_number) or raw_number)
        add(f"schedule-{schedule_number}", match.group(0))

    return references, external_nodes


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
    parser.add_argument("input_json", help="Raw JSON file, or a directory containing *section.json files")
    parser.add_argument("output_json", help="Output JSON file, or output directory for directory input")
    parser.add_argument("--act-name", default="Income-tax Act, 2025")
    parser.add_argument("--jurisdiction", default="India")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_json)

    if input_path.is_dir():
        output_path.mkdir(parents=True, exist_ok=True)
        input_files = sorted(input_path.glob("*section.json"), key=path_section_sort_key)
        raw_documents = [json.loads(raw_path.read_text(encoding="utf-8")) for raw_path in input_files]
        external_act_registry = discover_external_act_registry(raw_documents)
        for raw_path, raw in zip(input_files, raw_documents):
            result = Canonicalizer(
                act_name=args.act_name,
                jurisdiction=args.jurisdiction,
                external_act_registry=external_act_registry,
            ).convert(raw)
            # Preserve one output per source file. A mismatched section number
            # must be exposed by validation, not overwrite another section.
            section_number = section_number_from_path(raw_path)
            destination = output_path / f"{section_number}section_can.json"
            destination.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote canonical JSON for {len(input_files)} section files to {output_path}")
        return

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    result = Canonicalizer(act_name=args.act_name, jurisdiction=args.jurisdiction).convert(raw)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(result['legal_units'])} canonical units to {output_path}")


def section_number_from_path(path: Path) -> str:
    match = re.match(r"(\d+)", path.name)
    return match.group(1) if match else path.stem


def path_section_sort_key(path: Path) -> tuple[int, str]:
    number = section_number_from_path(path)
    return (int(number), path.name) if number.isdigit() else (10**9, path.name)


if __name__ == "__main__":
    main()
