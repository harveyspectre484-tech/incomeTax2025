
#!/usr/bin/env python3
"""
Convert position-aware extracted Income-tax Act text into structured JSON.

Expected input line shape:
  [x=  27.62] [y=  96.50] [font= 8.5] [bold=True] 206. (1)(a) ...

Usage:
  python income_tax_txt_to_json.py input.txt -o output.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LINE_RE = re.compile(
    r"^\[x=\s*(?P<x>[-\d.]+)\]\s*"
    r"\[y=\s*(?P<y>[-\d.]+)\]\s*"
    r"\[font=\s*(?P<font>[-\d.]+)\]\s*"
    r"\[bold=(?P<bold>True|False)\]\s*"
    r"(?P<text>.*)$"
)
PAGE_RE = re.compile(r"^\s*PAGE\s+(\d+)\s*$")
CHAPTER_RE = re.compile(r"^(CHAPTER|Chapter)\s+([IVXLCDM\dA-Z-]+)\b(?:\s*[-:.\u2014]\s*(.*))?$")
SECTION_RE = re.compile(r"^(?P<number>\d+[A-Z]?)\.\s*(?P<rest>.*)$")
BRACKET_RE = re.compile(r"^\s*\[?\((?P<token>[A-Za-z]+|\d+)\)\s*")
EXPLANATION_RE = re.compile(r"^(Explanation(?:\s+\d+)?\.?|Explanation(?:\s+[A-Z])?)\s*[-:.\u2014]?\s*(.*)$", re.I)
PROVISO_RE = re.compile(r"^(Provided(?:\s+further)?\s+that)\b[:,]?\s*(.*)$", re.I)
ILLUSTRATION_RE = re.compile(r"^(Illustration(?:\s+\d+)?\.?)\s*[-:.\u2014]?\s*(.*)$", re.I)
PARENT_BRIDGE_RE = re.compile(r"^(and\s+as\s+(?:reduced|further\s+adjusted)\s+by)\b", re.I)
FOOTNOTE_START_RE = re.compile(
    r"^(?P<number>\d+)\.\s+"
    r"(?P<text>(?:Sub\.|Sub-sections?|Words?|Clauses?|Clause|Omtt\.|Inserted|Ins\.|"
    r"Omitted|Prior to|Explanation|Proviso).+)$",
    re.I,
)

LOWER_ROMAN = {
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
}
UPPER_ROMAN = {x.upper() for x in LOWER_ROMAN}
SECTION_X_MAX = 30.5


@dataclass
class Line:
    page: int
    x: float
    y: float
    font: float
    bold: bool
    text: str


def new_node(kind: str, number: str | None, text: str, line: Line) -> dict[str, Any]:
    return {
        "type": kind,
        "number": number,
        "text_before_children": clean_text(text),
        "text_after_children": "",
        "x": line.x,
        "y_start": line.y,
        "page_start": line.page,
        "page_end": line.page,
        "children": [],
    }


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def has_children(node: dict[str, Any]) -> bool:
    return bool(node.get("_child_order"))


def append_text(
    node: dict[str, Any] | None,
    text: str,
    line: Line,
    after_children: bool | None = None,
) -> None:
    if not node or not text.strip():
        return
    if after_children is None:
        after_children = has_children(node)
    key = "text_after_children" if after_children else "text_before_children"
    node[key] = clean_text((node.get(key) or "") + " " + text)
    node["page_end"] = line.page


def node_text(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    return clean_text(
        " ".join(
            part
            for part in (
                node.get("text_before_children", ""),
                node.get("text_after_children", ""),
                node.get("text", ""),
            )
            if part
        )
    )


def parse_lines(path: Path) -> list[Line]:
    lines: list[Line] = []
    page = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        page_match = PAGE_RE.match(raw.strip())
        if page_match:
            page = int(page_match.group(1))
            continue

        match = LINE_RE.match(raw.rstrip())
        if not match:
            continue

        text = match.group("text").strip()
        if not text:
            continue

        lines.append(
            Line(
                page=page,
                x=float(match.group("x")),
                y=float(match.group("y")),
                font=float(match.group("font")),
                bold=match.group("bold") == "True",
                text=text,
            )
        )

    return sorted(lines, key=lambda l: (l.page, l.y, l.x))


def is_header_or_page_number(line: Line) -> bool:
    if line.text in {"Income Tax Department", "Ministry of Finance, Government of India"}:
        return True
    if re.fullmatch(r"\d+", line.text) and line.font <= 7.5:
        return True
    return False


def is_section_start(line: Line) -> bool:
    return bool(SECTION_RE.match(line.text.strip())) and line.x <= SECTION_X_MAX


def token_kind(token: str, current: dict[str, Any]) -> str:
    """Classify a leading bracket token in context."""
    if token.isdigit():
        return "subsection"

    if token.islower():
        if token in LOWER_ROMAN:
            return "sub_clause"
        return "clause"

    if token.isupper():
        if token in UPPER_ROMAN:
            return "item"
        return "item"

    return "item"


def child_bucket(parent: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    key = {
        "subsection": "subsections",
        "clause": "clauses",
        "sub_clause": "sub_clauses",
        "item": "items",
        "explanation": "explanations",
        "proviso": "provisos",
        "illustration": "illustrations",
    }.get(kind, "children")
    parent.setdefault(key, [])
    return parent[key]


def attach_child(parent: dict[str, Any], child: dict[str, Any]) -> None:
    child_bucket(parent, child["type"]).append(child)
    parent.setdefault("_child_order", []).append(child)


def find_parent_for(kind: str, current: dict[str, Any]) -> dict[str, Any] | None:
    section = current.get("section")
    subsection = current.get("subsection")
    clause = current.get("clause")
    sub_clause = current.get("sub_clause")

    if kind == "subsection":
        return section
    if kind == "clause":
        return subsection or section
    if kind == "sub_clause":
        return clause or subsection or section
    if kind == "item":
        return sub_clause or clause or subsection or section
    if kind in {"explanation", "proviso", "illustration"}:
        return sub_clause or clause or subsection or section
    return section


def set_current(kind: str, node: dict[str, Any], current: dict[str, Any]) -> None:
    node.setdefault("id", node_id_for(kind, node.get("number"), current))
    current[kind] = node
    order = ["subsection", "clause", "sub_clause", "item"]
    if kind in order:
        for lower in order[order.index(kind) + 1 :]:
            current.pop(lower, None)


def deepest_current(current: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("item", "sub_clause", "clause", "subsection", "section", "chapter"):
        if current.get(key):
            return current[key]
    return None


def node_id_for(kind: str, number: str | None, current: dict[str, Any]) -> str:
    order = ("section", "subsection", "clause", "sub_clause", "item")
    parts: list[str] = []
    for key in order:
        if key == kind:
            if number:
                parts.append(str(number))
            break
        node = current.get(key)
        if node and node.get("number"):
            parts.append(str(node["number"]))
    return "-".join(parts)


def continuation_target(line: Line, current: dict[str, Any]) -> dict[str, Any] | None:
    """
    Pick the active node that owns an unnumbered continuation line.

    Legal PDFs commonly express hierarchy through indentation. If a line de-dents
    back to the clause margin, it belongs to the clause even when a sub-clause was
    the most recent numbered item.
    """
    keys = ("item", "sub_clause", "clause", "subsection", "section")
    candidates = [current[key] for key in keys if current.get(key)]
    if not candidates:
        return current.get("chapter")

    if PARENT_BRIDGE_RE.match(line.text.strip()) and current.get("clause"):
        return current["clause"]

    tolerance = 2.5
    for node in candidates:
        node_x = float(node.get("x", 0))
        if line.x + tolerance >= node_x:
            return node

    return candidates[-1]


def consume_bracket_tokens(text: str, line: Line, current: dict[str, Any]) -> str:
    """Create nodes for all leading tokens, e.g. '(1)(a)'."""
    rest = text
    created: dict[str, Any] | None = None

    while True:
        match = BRACKET_RE.match(rest)
        if not match:
            break

        token = match.group("token")
        rest = rest[match.end() :]
        kind = token_kind(token, current)
        parent = find_parent_for(kind, current)
        if not parent:
            break

        node = new_node(kind, token, "", line)
        attach_child(parent, node)
        set_current(kind, node, current)
        created = node

    if created:
        append_text(created, rest, line)
        return ""
    return text


def is_table_start(line: Line) -> bool:
    text = line.text.strip()
    return text.upper() == "TABLE" or bool(re.match(r"^Sl\.\s*No\.?\b", text, re.I))


def line_starts_main_hierarchy(line: Line) -> bool:
    text = line.text.strip()
    if is_section_start(line):
        return True
    if BRACKET_RE.match(text) and line.x <= 45:
        return True
    if EXPLANATION_RE.match(text) or PROVISO_RE.match(text) or ILLUSTRATION_RE.match(text):
        return True
    return False


def line_starts_post_table_body(line: Line) -> bool:
    text = line.text.strip()
    if is_section_start(line):
        return True
    if re.match(r"^\([a-z]\)\s+", text) and line.x <= 45:
        return True
    if EXPLANATION_RE.match(text) or PROVISO_RE.match(text) or ILLUSTRATION_RE.match(text):
        return True
    return False


def make_node_id(current: dict[str, Any], node: dict[str, Any] | None = None) -> str:
    parts: list[str] = []
    for key in ("section", "subsection", "clause", "sub_clause", "item"):
        value = node if node and node.get("type") == key else current.get(key)
        if value and value.get("number"):
            parts.append(str(value["number"]))
    return "-".join(parts)


def table_id_for(parent: dict[str, Any] | None) -> str:
    base = parent.get("id") if parent else None
    if not base:
        base = "table"
    existing_count = 0
    if parent:
        existing_count += 1 if parent.get("table") else 0
        existing_count += len(parent.get("tables", []))
    return f"{base}-table-{existing_count + 1}"


def strip_item_number(text: str) -> tuple[int | None, str]:
    match = re.match(r"^(?:\((\d+)\)|(\d+)\.?)\s*(.*)$", text.strip())
    if not match:
        bare = re.fullmatch(r"\(?(\d+)\)?\.?", text.strip())
        if bare:
            return int(bare.group(1)), ""
        return None, text.strip()
    body = match.group(3).strip()
    if not body and not re.search(r"[.)]", text.strip()):
        return None, text.strip()
    return int(match.group(1) or match.group(2)), body


def linearize_table(parent: dict[str, Any] | None, rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not parent:
        prefix = "Table"
    else:
        number = parent.get("number") or ""
        prefix = f"{parent.get('type', 'node').replace('_', ' ').title()} ({number})"

    parts = [f"{prefix}: {node_text(parent)}" if parent else prefix]
    if rows:
        parts.append("Table items:")
    for row in rows:
        item = row.get("item_no")
        cells = []
        for index, column in enumerate(columns):
            key = f"column_{chr(ord('A') + index)}"
            value = row.get(key, "")
            if value:
                cells.append(f"{column or key}: {value}")
        parts.append(f"Item {item} — " + "; ".join(cells) + ".")
    return clean_text(" ".join(parts))


def row_key(line: Line) -> tuple[int, int]:
    return (line.page, round(line.y / 4) * 4)


def assign_column_index(x: float, anchors: list[float]) -> int:
    if not anchors:
        return 0
    return min(range(len(anchors)), key=lambda i: abs(x - anchors[i]))


def column_key(index: int) -> str:
    return f"column_{chr(ord('A') + index)}"


def merge_text(existing: str, addition: str) -> str:
    return clean_text((existing + " " + addition).strip())


def table_rows_by_position(table_lines: list[Line]) -> list[list[Line]]:
    grouped: dict[tuple[int, int], list[Line]] = {}
    for line in table_lines:
        grouped.setdefault(row_key(line), []).append(line)
    return [sorted(grouped[key], key=lambda l: l.x) for key in sorted(grouped)]


def detect_column_anchors(rows: list[list[Line]]) -> list[float]:
    xs: list[float] = []
    data_xs: list[float] = []
    for row in rows:
        row_has_item = any(strip_item_number(line.text)[0] is not None for line in row)
        for line in row:
            xs.append(line.x)
            if row_has_item:
                data_xs.append(line.x)
    if data_xs:
        xs = data_xs
    if not xs:
        return []

    anchors: list[float] = []
    for x in sorted(xs):
        if not anchors or x - anchors[-1] > 55:
            anchors.append(x)
        else:
            anchors[-1] = (anchors[-1] + x) / 2
    return anchors


def normalize_header_text(text: str) -> str:
    text = clean_text(text)
    label_match = re.fullmatch(r"\(([A-Z])\)", text)
    if label_match:
        return f"[{label_match.group(1)}]"
    return text


def build_generic_table(table_lines: list[Line]) -> tuple[list[str], list[dict[str, Any]]]:
    rows_by_y = table_rows_by_position(table_lines)
    anchors = detect_column_anchors(rows_by_y)
    header_parts: dict[int, list[str]] = {}
    data_rows: list[dict[str, Any]] = []
    current_row: dict[str, Any] | None = None
    current_item: int | None = None

    for visual_row in rows_by_y:
        cells_by_col: dict[int, str] = {}
        item_no: int | None = None

        for line in visual_row:
            col = assign_column_index(line.x, anchors)
            found_item, body = strip_item_number(line.text)
            if found_item is not None and item_no is None:
                item_no = found_item
                if body:
                    cells_by_col[col] = merge_text(cells_by_col.get(col, ""), body)
                continue
            if found_item is not None and found_item == item_no:
                cells_by_col[col] = merge_text(cells_by_col.get(col, ""), body)
                continue
            cells_by_col[col] = merge_text(cells_by_col.get(col, ""), line.text)

        if item_no is None:
            if current_row is None:
                for col, text in cells_by_col.items():
                    header_parts.setdefault(col, []).append(normalize_header_text(text))
            else:
                for col, text in cells_by_col.items():
                    key = column_key(col)
                    current_row[key] = merge_text(current_row.get(key, ""), text)
            continue

        current_item = item_no
        current_row = {"item_no": current_item}
        for col, text in cells_by_col.items():
            current_row[column_key(col)] = text
        data_rows.append(current_row)

    columns = [clean_text(" ".join(header_parts.get(i, []))) for i in range(len(anchors))]
    return columns, data_rows


def structured_table_from_lines(
    start_line: Line,
    table_lines: list[Line],
    parent: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    title = "TABLE" if start_line.text.strip().upper() == "TABLE" else start_line.text.strip()
    columns, rows = build_generic_table(sorted(table_lines, key=lambda l: (l.page, l.y, l.x)))

    source_rule = make_node_id(current, parent) if parent else ""
    table = {
        "type": "table",
        "table_id": table_id_for(parent),
        "title": title,
        "page_start": start_line.page,
        "page_end": start_line.page,
        "columns": columns,
        "rows": rows,
        "linearized_text": linearize_table(parent, rows, columns),
        "source": {
            "act": "Income-tax Act",
            "rule": source_rule,
            "page_start": start_line.page,
            "page_end": start_line.page,
            "x": parent.get("x") if parent else start_line.x,
            "y_start": parent.get("y_start") if parent else start_line.y,
        },
    }
    return table


def collect_table(
    lines: list[Line],
    start: int,
    parent: dict[str, Any] | None,
    current: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    start_line = lines[start]
    table_lines: list[Line] = []
    if start_line.text.strip().upper() != "TABLE":
        table_lines.append(start_line)
    i = start + 1

    while i < len(lines):
        line = lines[i]
        if line.page != start_line.page:
            break
        if line.y > start_line.y + 260:
            break
        if (
            start_line.text.strip().upper() != "TABLE"
            and table_lines
            and re.match(r"^\[?\(\d+\)\s+", line.text.strip())
            and line.x <= SECTION_X_MAX
        ):
            break
        if (
            start_line.text.strip().upper() == "TABLE"
            and parent
            and parent.get("type") == "subsection"
            and any(strip_item_number(l.text)[0] is not None for l in table_lines)
            and re.match(r"^\[?\(\d+\)\s+", line.text.strip())
            and abs(line.x - float(parent.get("x", line.x))) <= 2.5
        ):
            break
        if (
            start_line.text.strip().upper() == "TABLE"
            and any(re.match(r"^Sl\.\s*No\.?\b", l.text.strip(), re.I) for l in table_lines)
            and any(strip_item_number(l.text)[0] is not None for l in table_lines)
            and re.match(r"^\[?\(\d+\)\s+", line.text.strip())
            and line.x <= SECTION_X_MAX
        ):
            break
        if table_lines and line_starts_post_table_body(line):
            break
        if not is_header_or_page_number(line):
            table_lines.append(line)
        i += 1

    return structured_table_from_lines(start_line, table_lines, parent, current), i


def parse_special_block(line: Line, current: dict[str, Any]) -> bool:
    for kind, regex in (
        ("explanation", EXPLANATION_RE),
        ("proviso", PROVISO_RE),
        ("illustration", ILLUSTRATION_RE),
    ):
        match = regex.match(line.text.strip())
        if not match:
            continue
        parent = find_parent_for(kind, current)
        if not parent:
            return False
        label, body = match.group(1), match.group(2)
        node = new_node(kind, label.rstrip("."), body, line)
        attach_child(parent, node)
        set_current(kind, node, current)
        return True
    return False


def parse_footnote(line: Line, current_footnote: dict[str, Any] | None) -> dict[str, Any] | None:
    match = FOOTNOTE_START_RE.match(line.text)
    if match and line.x <= 35:
        return {
            "type": "footnote",
            "number": match.group("number"),
            "text": clean_text(match.group("text")),
            "page_start": line.page,
            "page_end": line.page,
        }
    if current_footnote and line.x >= 35:
        current_footnote["text"] = clean_text(current_footnote.get("text", "") + " " + line.text)
        current_footnote["page_end"] = line.page
        return current_footnote
    return None


CHILD_OUTPUT_KEYS = {
    "subsection": "subsections",
    "clause": "clauses",
    "sub_clause": "sub_clauses",
    "item": "items",
    "explanation": "explanations",
    "proviso": "provisos",
    "illustration": "illustrations",
}


def dominant_child_key(children: list[dict[str, Any]]) -> str:
    for kind in ("subsection", "clause", "sub_clause", "item"):
        if any(child.get("type") == kind for child in children):
            return CHILD_OUTPUT_KEYS[kind]
    if children:
        return CHILD_OUTPUT_KEYS.get(children[0].get("type"), "children")
    return "children"


def finalized_text_key(base: str, child_key: str) -> str:
    return f"{base}_{child_key}"


def finalize_node(node: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "type",
        "number",
        "id",
        "title",
        "x",
        "y_start",
        "page_start",
        "page_end",
    ):
        if key in node:
            out[key] = node[key]

    raw_children = node.get("_child_order", [])
    if raw_children:
        child_key = dominant_child_key(raw_children)
        out[finalized_text_key("text_before", child_key)] = node.get("text_before_children", "")

        grouped_children: dict[str, list[dict[str, Any]]] = {}
        for child in raw_children:
            key = CHILD_OUTPUT_KEYS.get(child.get("type"), "children")
            grouped_children.setdefault(key, []).append(finalize_node(child))

        for key, children in grouped_children.items():
            out[key] = children

        out[finalized_text_key("text_after", child_key)] = node.get("text_after_children", "")
    else:
        text = node_text(node)
        if text or node.get("type") not in {"section", "chapter"}:
            out["text"] = text
        out.setdefault("children", [])

    if "table" in node:
        out["table"] = node["table"]
    if "tables" in node:
        out["tables"] = node["tables"]

    return out


def finalize_document(doc: dict[str, Any]) -> dict[str, Any]:
    finalized = {
        "chapters": [finalize_node(chapter) for chapter in doc.get("chapters", [])],
        "sections": [finalize_node(section) for section in doc.get("sections", [])],
        "footnotes": doc.get("footnotes", []),
        "orphans": doc.get("orphans", []),
    }
    if "orphan_tables" in doc:
        finalized["orphan_tables"] = doc["orphan_tables"]
    return finalized


def parse_document(lines: list[Line]) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "chapters": [],
        "sections": [],
        "footnotes": [],
        "orphans": [],
    }
    current: dict[str, Any] = {}
    current_footnote: dict[str, Any] | None = None
    pending_section_title: str | None = None
    i = 0

    while i < len(lines):
        line = lines[i]
        text = line.text.strip()

        if is_header_or_page_number(line):
            i += 1
            continue

        footnote = parse_footnote(line, current_footnote)
        if footnote:
            if footnote is not current_footnote:
                doc["footnotes"].append(footnote)
            current_footnote = footnote
            i += 1
            continue

        if is_table_start(line):
            parent = deepest_current(current)
            if parent:
                table, next_i = collect_table(lines, i, parent, current)
                if "table" not in parent:
                    parent["table"] = table
                else:
                    parent.setdefault("tables", []).append(table)
            else:
                table, next_i = collect_table(lines, i, None, current)
                doc.setdefault("orphan_tables", []).append(table)
            i = next_i
            continue

        chapter_match = CHAPTER_RE.match(text)
        if chapter_match:
            chapter = {
                "type": "chapter",
                "number": chapter_match.group(2),
                "title": clean_text(chapter_match.group(3) or ""),
                "x": line.x,
                "y_start": line.y,
                "page_start": line.page,
                "page_end": line.page,
                "sections": [],
            }
            doc["chapters"].append(chapter)
            current = {"chapter": chapter}
            i += 1
            continue

        section_match = SECTION_RE.match(text)
        if section_match and is_section_start(line):
            section = {
                "type": "section",
                "number": section_match.group("number"),
                "id": section_match.group("number"),
                "title": clean_text(pending_section_title or ""),
                "text_before_children": "",
                "text_after_children": "",
                "x": line.x,
                "y_start": line.y,
                "page_start": line.page,
                "page_end": line.page,
                "subsections": [],
                "children": [],
            }
            doc["sections"].append(section)
            if current.get("chapter"):
                current["chapter"]["sections"].append(section)
                current["chapter"].setdefault("_child_order", []).append(section)
            current = {"chapter": current.get("chapter"), "section": section}
            pending_section_title = None
            rest = section_match.group("rest")
            rest = consume_bracket_tokens(rest, line, current)
            append_text(deepest_current(current), rest, line)
            i += 1
            continue

        if line.bold and line.x <= 45 and not BRACKET_RE.match(text):
            pending_section_title = clean_text(text)
            i += 1
            continue

        if parse_special_block(line, current):
            i += 1
            continue

        rest = consume_bracket_tokens(text, line, current)
        if rest:
            node = continuation_target(line, current)
            if node:
                append_text(node, rest, line)
            else:
                doc["orphans"].append(
                    {
                        "page": line.page,
                        "x": line.x,
                        "y": line.y,
                        "text": text,
                    }
                )

        i += 1

    return doc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Position-aware extracted text file")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file")
    args = parser.parse_args()

    lines = parse_lines(args.input)
    doc = finalize_document(parse_document(lines))
    payload = json.dumps(doc, ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
