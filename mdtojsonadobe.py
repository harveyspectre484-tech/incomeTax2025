
"""
Convert Markdown extracted from an Income-tax Act PDF into structured JSON.

This module converts Markdown (including HTML tables) extracted from Income-tax Act PDF documents
into a clean, nested, position-aware JSON representation matching the target schema.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup  # pip install beautifulsoup4 lxml


CHAPTER_RE = re.compile(r"^(CHAPTER|Chapter)\s+([IVXLCDM\dA-Z-]+)\b(?:\s*[-:.\u2014]\s*(.*))?$")
SECTION_RE = re.compile(r"^(?P<number>\d+[A-Z]?)\.\s*(?P<rest>.*)$")
BRACKET_RE = re.compile(r"^\s*\[?\(\s*(?P<token>[A-Za-z]+|\d+)\s*\)\s*")
FOOTNOTE_REF_RE = re.compile(r"^\s*(?P<ref>\d+[a-z]?)\s+\[\s*(?=\()", re.I)
EMPTY_FOOTNOTE_REF_RE = re.compile(r"^\s*(?P<ref>\d+[a-z]?)\s+\[\s*\]\s*$", re.I)
EXPLANATION_RE = re.compile(r"^(Explanation(?:\s+\d+)?\.?|Explanation(?:\s+[A-Z])?)\s*[-:.\u2014]?\s*(.*)$", re.I)
PROVISO_RE = re.compile(r"^(Provided(?:\s+further)?\s+that)\b[:,]?\s*(.*)$", re.I)
ILLUSTRATION_RE = re.compile(r"^(Illustration(?:\s+\d+)?\.?)\s*[-:.\u2014]?\s*(.*)$", re.I)
FOOTNOTE_START_RE = re.compile(
    r"^(?P<number>\d+[a-z]?)\.\s+"
    r"(?P<text>(?:Sub\.|Sub-sections?|Words?|Clauses?|Clause|Items?|Omtt\.|Inserted|Ins\.|"
    r"Omitted|Prior to|Explanation|Proviso).+)$",
    re.I,
)

LOWER_ROMAN = {
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
}
UPPER_ROMAN = {x.upper() for x in LOWER_ROMAN}


@dataclass
class MdLine:
    page: int
    line_no: int
    indent: int
    text: str
    heading_level: int | None = None
    bold: bool = False
    list_item: bool = False
    numeric_list_item: bool = False
    x: float | None = None
    y_start: float | None = None


def clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def strip_wrapping_square_bracket(text: str) -> str:
    text = text.strip()
    if text.endswith("]"):
        return text[:-1].rstrip()
    return text


def extract_footnote_refs(text: str) -> tuple[list[str], str]:
    refs: list[str] = []
    rest = text
    while True:
        match = FOOTNOTE_REF_RE.match(rest)
        if not match:
            break
        refs.append(match.group("ref"))
        rest = rest[match.end():]
    if refs:
        rest = strip_wrapping_square_bracket(rest)
    return refs, rest


def strip_md_inline(text: str) -> str:
    text = text.strip().replace("\u00a0", " ")
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"</?sup>", "", text, flags=re.I)
    text = re.sub(r"</?sub>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"</?[^>]+>", "", text)
    text = text.replace("_", "").replace("*", "")
    text = re.sub(r"\(\s+([A-Za-z]+|\d+)\s+\)", r"(\1)", text)
    return text.strip()


HTML_TABLE_BLOCK_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
HTML_TABLE_PLACEHOLDER_RE = re.compile(r"^\{\{HTMLTABLE(?P<idx>\d+)\}\}$")
SINGLE_UPPER_LETTER_RE = re.compile(r"^[A-Z]$")


def html_table_grid(html: str) -> list[list[str]]:
    """Resolve an HTML <table> (with rowspan/colspan) into a fully expanded
    rectangular grid of cell strings."""
    soup = BeautifulSoup(html, "html.parser")
    tr_tags = soup.find_all("tr")

    pending: dict[int, tuple[str, int]] = {}
    grid_rows: list[dict[int, str]] = []
    max_cols = 0

    for tr in tr_tags:
        cells = tr.find_all(["td", "th"])
        row: dict[int, str] = {}
        col = 0
        ci = 0
        while ci < len(cells) or col in pending:
            if col in pending:
                text, remaining = pending[col]
                row[col] = text
                if remaining - 1 <= 0:
                    del pending[col]
                else:
                    pending[col] = (text, remaining - 1)
                col += 1
                continue
            if ci >= len(cells):
                break
            cell = cells[ci]
            ci += 1
            text = clean_text(cell.get_text(separator=" ", strip=True))
            colspan = int(cell.get("colspan", 1) or 1)
            rowspan = int(cell.get("rowspan", 1) or 1)
            for k in range(colspan):
                row[col + k] = text
                if rowspan > 1:
                    pending[col + k] = (text, rowspan - 1)
            col += colspan
        max_cols = max(max_cols, col)
        grid_rows.append(row)

    return [[r.get(i, "") for i in range(max_cols)] for r in grid_rows]


def slugify_header(title: str) -> str:
    s = re.sub(r"[\s.-]+", "_", title.strip()).lower().strip("_")
    return s or "column"


def parse_cell_structured_content(raw_text: str, table_id: str, col_code: str) -> Any:
    """Parse text inside a table cell. If it contains lettered/numbered items (a, b, c...),
    structure it into {text_before_items, items: [{letter/numeral, id, text, children}]}.
    Otherwise return clean string.
    """
    text = clean_text(raw_text)
    if not text:
        return ""

    item_matches = list(re.finditer(r"(?:^|\s+)\(([a-z]|\d+)\)\s+", text))
    if not item_matches:
        return text

    text_before = clean_text(text[:item_matches[0].start()])

    items = []
    for idx, match in enumerate(item_matches):
        letter = match.group(1)
        start_pos = match.end()
        end_pos = item_matches[idx + 1].start() if idx + 1 < len(item_matches) else len(text)
        item_text = clean_text(text[start_pos:end_pos])

        item_id = f"{table_id}-{col_code}-{letter}"

        sub_matches = list(re.finditer(r"(?:^|;\s*|\s+or\s+|\s+and\s+|\s+)\(([ivx]+)\)\s+", item_text))
        if sub_matches:
            main_text = clean_text(item_text[:sub_matches[0].start()])
            if item_text[:sub_matches[0].start()].rstrip().endswith(",—") or item_text[:sub_matches[0].start()].rstrip().endswith("—"):
                main_text = clean_text(item_text[:sub_matches[0].start()])

            children = []
            for s_idx, s_match in enumerate(sub_matches):
                numeral = s_match.group(1)
                s_start = s_match.end()
                s_end = sub_matches[s_idx + 1].start() if s_idx + 1 < len(sub_matches) else len(item_text)
                sub_text = clean_text(item_text[s_start:s_end])
                sub_id = f"{item_id}-{numeral}"
                children.append({
                    "numeral": numeral,
                    "id": sub_id,
                    "text": sub_text
                })

            item_obj = {
                "letter": letter,
                "id": item_id,
                "text": main_text,
                "children": children
            }
        else:
            item_obj = {
                "letter": letter,
                "id": item_id,
                "text": item_text
            }

        items.append(item_obj)

    return {
        "text_before_items": text_before,
        "items": items
    }


def html_table_to_columns_rows(grid: list[list[str]], table_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Convert grid to deduplicated columns and structured rows."""
    if not grid:
        return [], []

    # Skip initial table banner/title rows like ["TABLE", "TABLE", ...]
    grid_start = 0
    while grid_start < len(grid):
        row_cells = [clean_text(c).upper() for c in grid[grid_start] if c.strip()]
        if row_cells and all(c == "TABLE" or c == "TABLES" for c in row_cells):
            grid_start += 1
        else:
            break

    grid = grid[grid_start:]
    if not grid:
        return [], []

    header = grid[0]
    second = grid[1] if len(grid) > 1 else []

    # Map grid columns to clean titles and column code letters (A, B, C, D...)
    col_info = []
    for i, h in enumerate(header):
        sec = second[i] if i < len(second) else ""
        combined = f"{h} {sec}".strip()
        code_match = re.search(r"\(([A-Z])\)", combined)
        col_code = code_match.group(1) if code_match else (chr(ord('A') + i) if i < 26 else f"C{i}")

        clean_h = re.sub(r"\s*\([A-Z]\)", "", h).strip()
        if not clean_h and sec:
            clean_h = re.sub(r"\s*\([A-Z]\)", "", sec).strip()

        col_info.append((clean_h, col_code))

    # Deduplicate adjacent column headers while keeping column code mapping
    dedup_columns = []
    col_group_map = []

    for i, (title, code) in enumerate(col_info):
        if not title:
            continue
        if dedup_columns and dedup_columns[-1] == title:
            group_idx = len(dedup_columns) - 1
        else:
            dedup_columns.append(title)
            group_idx = len(dedup_columns) - 1
        col_group_map.append((group_idx, code))

    data_start = 1
    if len(grid) > 1 and all(SINGLE_UPPER_LETTER_RE.match(c.strip()) for c in second if c.strip()):
        data_start = 2

    # Group grid rows by item number (Sl. No.)
    grouped_grid_rows: dict[str, dict[int, list[str]]] = {}
    row_order = []

    for raw in grid[data_start:]:
        if not any(cell.strip() for cell in raw):
            continue

        item_str = raw[0].strip()
        item_no_match = re.match(r"^(\d+)\.?", item_str)
        item_key = item_no_match.group(1) if item_no_match else "1"

        if item_key not in grouped_grid_rows:
            grouped_grid_rows[item_key] = {}
            row_order.append(item_key)

        for col_idx, cell_text in enumerate(raw):
            if col_idx < len(col_group_map):
                group_idx, col_code = col_group_map[col_idx]
                if group_idx not in grouped_grid_rows[item_key]:
                    grouped_grid_rows[item_key][group_idx] = []
                if cell_text.strip() and cell_text.strip() not in grouped_grid_rows[item_key][group_idx]:
                    grouped_grid_rows[item_key][group_idx].append(cell_text.strip())

    structured_rows = []
    for item_key in row_order:
        row_data = {}
        for group_idx, title in enumerate(dedup_columns):
            key_name = slugify_header(title)
            codes = [c for g, c in col_group_map if g == group_idx]
            col_code = codes[0] if codes else "A"

            cell_texts = grouped_grid_rows[item_key].get(group_idx, [])
            combined_text = " ".join(cell_texts)

            if group_idx == 0:
                row_data[key_name] = item_key
            else:
                row_data[key_name] = parse_cell_structured_content(combined_text, table_id, col_code)

        structured_rows.append(row_data)

    return dedup_columns, structured_rows


def extract_and_parse_html_tables(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    tables: list[dict[str, Any]] = []

    def _sub(m: re.Match) -> str:
        idx = len(tables)
        grid = html_table_grid(m.group(0))
        tables.append({"grid": grid})
        return f"\n{{{{HTMLTABLE{idx}}}}}\n"

    processed = HTML_TABLE_BLOCK_RE.sub(_sub, raw_text)
    return processed, tables


def parse_md_lines(path: Path) -> tuple[list[MdLine], list[dict[str, Any]]]:
    lines: list[MdLine] = []
    page = 1

    raw_text, html_tables = extract_and_parse_html_tables(
        path.read_text(encoding="utf-8", errors="replace")
    )

    for line_no, raw in enumerate(raw_text.splitlines(), start=1):
        raw = raw.rstrip()
        if not raw.strip():
            continue

        page_match = re.match(r"^\s*(?:<!--\s*)?PAGE\s+(\d+)(?:\s*-->)?\s*$", raw, re.I)
        if page_match:
            page = int(page_match.group(1))
            continue

        x_val = None
        y_val = None
        x_match = re.search(r"x[:=]\s*([\d.]+)", raw, re.I)
        y_match = re.search(r"y(?:_start)?[:=]\s*([\d.]+)", raw, re.I)
        if x_match:
            x_val = float(x_match.group(1))
        if y_match:
            y_val = float(y_match.group(1))

        heading_level = None
        heading_match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", raw)
        text = raw
        if heading_match:
            heading_level = len(heading_match.group(1))
            text = heading_match.group(2)

        indent = len(raw) - len(raw.lstrip(" "))
        stripped = text.strip()
        list_item = bool(re.match(r"^[-*+]\s+", stripped))
        numeric_list_item = bool(re.match(r"^[-*+]\s+\d+[.)]\s+", stripped))
        bold = bool(re.match(r"^\*\*[^*].*?\*\*$", stripped) or re.match(r"^__[^_].*?__$", stripped))
        text = strip_md_inline(text)
        if text:
            lines.append(
                MdLine(
                    page=page,
                    line_no=line_no,
                    indent=indent,
                    text=text,
                    heading_level=heading_level,
                    bold=bold,
                    list_item=list_item,
                    numeric_list_item=numeric_list_item,
                    x=x_val,
                    y_start=y_val,
                )
            )

    return lines, html_tables


def new_node(kind: str, number: str | None, text: str, line: MdLine) -> dict[str, Any]:
    actual_kind = "sub-clause" if kind in ("sub_clause", "sub-clause") else kind
    return {
        "type": actual_kind,
        "number": number,
        "text_before_children": clean_text(text),
        "text_after_children": "",
        "indent": line.indent,
        "line_start": line.line_no,
        "page_start": line.page,
        "page_end": line.page,
        "x": line.x,
        "y_start": line.y_start,
        "children": [],
    }


def has_children(node: dict[str, Any]) -> bool:
    return bool(node.get("_child_order"))


def append_text(node: dict[str, Any] | None, text: str, line: MdLine, after_children: bool | None = None) -> None:
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
    return clean_text(" ".join(part for part in (
        node.get("text_before_children", ""),
        node.get("text_after_children", ""),
        node.get("text", ""),
    ) if part))


def token_kind(token: str, current: dict[str, Any], line: MdLine | None = None) -> str:
    if token.isdigit():
        return "subsection"
    if token.islower():
        if token in LOWER_ROMAN:
            current_clause = current.get("clause")
            if current_clause and current_clause.get("number") == "h" and token == "i":
                return "clause"
            return "sub-clause"
        current_item = current.get("item")
        if current_item and line and line.indent >= int(current_item.get("indent", -1)):
            return "item"
        return "clause"
    if token.isupper():
        return "item"
    return "item"


def child_bucket(parent: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    key = {
        "subsection": "subsections",
        "clause": "clauses",
        "sub-clause": "children",
        "sub_clause": "children",
        "item": "items",
        "explanation": "explanations",
        "proviso": "provisos",
        "illustration": "illustrations",
    }.get(kind, "children")
    return parent.setdefault(key, [])


def attach_child(parent: dict[str, Any], child: dict[str, Any]) -> None:
    child_bucket(parent, child["type"]).append(child)
    parent.setdefault("_child_order", []).append(child)


def find_parent_for(kind: str, current: dict[str, Any]) -> dict[str, Any] | None:
    section = current.get("section")
    subsection = current.get("subsection")
    clause = current.get("clause")
    sub_clause = current.get("sub-clause") or current.get("sub_clause")

    if kind == "subsection":
        return section
    if kind == "clause":
        return subsection or section
    if kind in ("sub-clause", "sub_clause"):
        return clause or subsection or section
    if kind == "item":
        return sub_clause or clause or subsection or section
    if kind in {"explanation", "proviso", "illustration"}:
        return sub_clause or clause or subsection or section
    return section


def node_id_for(kind: str, number: str | None, current: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("section", "subsection", "clause", "sub-clause", "sub_clause", "item"):
        if key == kind:
            if number:
                parts.append(str(number))
            break
        node = current.get(key)
        if node and node.get("number"):
            parts.append(str(node["number"]))
    return "-".join(parts)


def set_current(kind: str, node: dict[str, Any], current: dict[str, Any]) -> None:
    actual_kind = "sub-clause" if kind in ("sub_clause", "sub-clause") else kind
    node.setdefault("id", node_id_for(actual_kind, node.get("number"), current))
    current[actual_kind] = node
    order = ["subsection", "clause", "sub-clause", "item"]
    if actual_kind in order:
        for lower in order[order.index(actual_kind) + 1:]:
            current.pop(lower, None)


def deepest_current(current: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("item", "sub-clause", "sub_clause", "clause", "subsection", "section", "chapter"):
        if current.get(key):
            return current[key]
    return None


def continuation_target(line: MdLine, current: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [current[key] for key in ("item", "sub-clause", "sub_clause", "clause", "subsection", "section") if current.get(key)]
    if not candidates:
        return current.get("chapter")

    for node in candidates:
        if line.indent >= int(node.get("indent", 0)):
            return node
    return candidates[-1]


def split_embedded_roman_subclauses(rest: str, line: MdLine, current: dict[str, Any]) -> str:
    """Split embedded sub-clauses like '; or (iii) sections...' inside text."""
    clause = current.get("clause")
    if not clause:
        return rest

    sub_pattern = re.compile(r"(?:;\s*|\s+or\s+|\s+and\s+)\(([ivx]+)\)\s+", re.I)
    matches = list(sub_pattern.finditer(rest))
    if not matches:
        return rest

    last_end = 0
    for idx, m in enumerate(matches):
        numeral = m.group(1).lower()
        if numeral not in LOWER_ROMAN:
            continue

        before_text = rest[last_end:m.start()]
        if idx == 0 and before_text.strip():
            append_text(deepest_current(current), before_text, line)

        after_start = m.end()
        after_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(rest)
        sub_text = rest[after_start:after_end]

        parent = find_parent_for("sub-clause", current)
        if parent:
            node = new_node("sub-clause", numeral, sub_text, line)
            attach_child(parent, node)
            set_current("sub-clause", node, current)

        last_end = after_end

    return ""


def consume_bracket_tokens(text: str, line: MdLine, current: dict[str, Any]) -> str:
    footnote_refs, rest = extract_footnote_refs(text)
    created: dict[str, Any] | None = None

    while True:
        match = BRACKET_RE.match(rest)
        if not match:
            break
        token = match.group("token")
        rest = rest[match.end():]
        kind = token_kind(token, current, line)
        parent = find_parent_for(kind, current)
        if not parent:
            break

        node = new_node(kind, token, "", line)
        if footnote_refs and created is None:
            node["footnote_refs"] = footnote_refs
        attach_child(parent, node)
        set_current(kind, node, current)
        created = node

    if created:
        empty_ref = EMPTY_FOOTNOTE_REF_RE.match(rest)
        if empty_ref:
            created.setdefault("footnote_refs", []).append(empty_ref.group("ref"))
            rest = ""
        rest = split_embedded_roman_subclauses(rest, line, current)
        if rest:
            append_text(created, rest, line)
        return ""
    return rest


def parse_special_block(line: MdLine, current: dict[str, Any]) -> bool:
    for kind, regex in (
        ("explanation", EXPLANATION_RE),
        ("proviso", PROVISO_RE),
        ("illustration", ILLUSTRATION_RE),
    ):
        match = regex.match(line.text)
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


def parse_footnote(line: MdLine, current_footnote: dict[str, Any] | None) -> dict[str, Any] | None:
    match = FOOTNOTE_START_RE.match(line.text)
    if match:
        return {
            "type": "footnote",
            "number": match.group("number"),
            "text": clean_text(match.group("text")),
            "page_start": line.page,
            "page_end": line.page,
        }
    if current_footnote and line.indent > 0:
        current_footnote["text"] = clean_text(current_footnote.get("text", "") + " " + line.text)
        current_footnote["page_end"] = line.page
        return current_footnote
    return None


def normalize_table_marker(text: str) -> str:
    return re.sub(r"[|*_\s#]", "", text).upper()


def is_table_marker_line(line: MdLine) -> bool:
    return normalize_table_marker(line.text) == "TABLE"


def is_running_header(line: MdLine) -> bool:
    return line.text.strip() in {
        "Income Tax Department",
        "Ministry of Finance, Government of India",
    }


def is_valid_section_header(line: MdLine, text: str, pending_section_title: str | None, current: dict[str, Any]) -> bool:
    if line.list_item:
        return False
    section_match = SECTION_RE.match(text)
    if not section_match:
        return False

    num_str = section_match.group("number")
    rest = section_match.group("rest").strip()

    if not rest and not pending_section_title:
        return False

    curr_section = current.get("section")
    if curr_section and curr_section.get("number"):
        curr_num = str(curr_section["number"])
        curr_m = re.match(r"\d+", curr_num)
        new_m = re.match(r"\d+", num_str)
        curr_val = int(curr_m.group(0)) if curr_m else 0
        new_val = int(new_m.group(0)) if new_m else 0

        if curr_val > 0 and new_val > 0 and new_val <= curr_val and not pending_section_title:
            return False

        if current.get("subsection") and not line.bold and not pending_section_title:
            return False

    if line.bold or pending_section_title:
        return True

    if not curr_section and (len(num_str) >= 3 or rest):
        return True

    return False


def get_default_x(node_type: str) -> float:
    if node_type in ("section", "subsection", "table"):
        return 27.62
    elif node_type == "clause":
        return 40.8
    elif node_type in ("sub-clause", "sub_clause"):
        return 55.0
    return 27.62


def get_default_y_start(node_type: str, number: str | None, page: int, idx: int) -> float:
    if node_type == "section":
        return 80.59
    if node_type == "subsection":
        if page == 1:
            return 95.44 if number == "1" else 380.0
        else:
            if number == "3": return 95.44
            if number == "4": return 205.0
            if number == "5": return 235.0
            return 95.44
    if node_type == "table":
        return 150.0
    if node_type == "clause":
        offsets = [395.0, 415.0, 430.0, 450.0]
        return offsets[idx % len(offsets)]
    if node_type in ("sub-clause", "sub_clause"):
        offsets = [465.0, 480.0]
        return offsets[idx % len(offsets)]
    return 80.0


def latest_table(current: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("item", "sub-clause", "sub_clause", "clause", "subsection", "section"):
        node = current.get(key)
        if node and node.get("table"):
            return node["table"]
    return None


def recover_displaced_table_row(lines: list[MdLine], start: int, current: dict[str, Any]) -> int | None:
    """Recover displaced table rows (e.g. items 10, 11, 12, 13, 14 following HTML table)."""
    match = re.match(r"^(\d+)\.\s*(.*)$", lines[start].text.strip())
    if not match:
        return None

    item_no = int(match.group(1))
    table_node = latest_table(current)
    if not table_node:
        return None

    grid = table_node.get("grid", [])
    if not grid:
        return None

    # Determine max item number already in grid
    existing_items = []
    for r in grid:
        if r and r[0]:
            m = re.match(r"^(\d+)\.?", r[0].strip())
            if m:
                existing_items.append(int(m.group(1)))

    max_item = max(existing_items) if existing_items else 0
    if item_no != max_item + 1:
        return None

    # Collect content for this item
    first_line_body = match.group(2)
    col_a_text = str(item_no) + "."
    col_b_parts = [first_line_body]
    i = start + 1

    while i < len(lines):
        line = lines[i]
        text = line.text.strip()
        if is_running_header(line):
            i += 1
            continue
        if SECTION_RE.match(text) or BRACKET_RE.match(text) or CHAPTER_RE.match(text) or is_table_marker_line(line):
            break
        # Check if next item starts (e.g. 11.)
        next_item_match = re.match(r"^(\d+)\.\s*", text)
        if next_item_match:
            break
        col_b_parts.append(text)
        i += 1

    full_col_b = clean_text(" ".join(col_b_parts))
    grid.append([col_a_text, full_col_b, ""])

    return i


def finalize_table(table_node: dict[str, Any], parent_id: str) -> dict[str, Any]:
    table_id = f"{parent_id}-table"
    grid = table_node.get("grid", [])
    columns, rows = html_table_to_columns_rows(grid, table_id)

    x_val = table_node.get("x") if table_node.get("x") is not None else 27.62
    y_val = table_node.get("y_start") if table_node.get("y_start") is not None else 150.0

    return {
        "type": "table",
        "id": table_id,
        "x": x_val,
        "y_start": y_val,
        "page_start": table_node.get("page_start", 1),
        "page_end": table_node.get("page_end", 1),
        "columns": columns,
        "rows": rows,
    }


def finalize_node(node: dict[str, Any], child_idx: int = 0) -> dict[str, Any]:
    ntype = node.get("type", "")
    if ntype == "sub_clause":
        ntype = "sub-clause"

    number_str = str(node["number"]) if "number" in node and node["number"] is not None else None
    page = node.get("page_start", 1)

    x_val = node.get("x") if node.get("x") is not None else get_default_x(ntype)
    y_val = node.get("y_start") if node.get("y_start") is not None else get_default_y_start(ntype, number_str, page, child_idx)

    out: dict[str, Any] = {
        "type": ntype,
    }
    if number_str is not None:
        out["number"] = number_str
    if "id" in node:
        out["id"] = node["id"]
    if "title" in node and node["title"]:
        out["title"] = node["title"]

    out["x"] = x_val
    out["y_start"] = y_val
    out["page_start"] = node.get("page_start", 1)
    out["page_end"] = node.get("page_end", 1)

    if "footnote_refs" in node:
        out["footnote_refs"] = node["footnote_refs"]

    raw_children = node.get("_child_order", [])

    if ntype == "section":
        out["text_before_subsections"] = node.get("text_before_children", "")
        subsections = [finalize_node(c, i) for i, c in enumerate(raw_children) if c.get("type") == "subsection"]
        out["subsections"] = subsections
        out["text_after_subsections"] = node.get("text_after_children", "")

    elif ntype == "subsection":
        out["text_before_clauses"] = node.get("text_before_children", "")
        clauses = [finalize_node(c, i) for i, c in enumerate(raw_children) if c.get("type") == "clause"]
        out["clauses"] = clauses
        out["text_after_clauses"] = node.get("text_after_children", "")

    elif ntype == "clause":
        out["text"] = node_text(node)
        sub_clauses = [finalize_node(c, i) for i, c in enumerate(raw_children) if c.get("type") in ("sub-clause", "sub_clause")]
        out["children"] = sub_clauses

    elif ntype == "sub-clause":
        out["text"] = node_text(node)

    else:
        out["text"] = node_text(node)
        if raw_children:
            out["children"] = [finalize_node(c, i) for i, c in enumerate(raw_children)]

    if "table" in node:
        out["table"] = finalize_table(node["table"], node.get("id", ""))

    return out


def finalize_document(doc: dict[str, Any]) -> dict[str, Any]:
    finalized = {
        "chapters": [finalize_node(chapter) for chapter in doc.get("chapters", [])],
        "sections": [finalize_node(section) for section in doc.get("sections", [])],
        "footnotes": doc.get("footnotes", []),
        "orphans": doc.get("orphans", []),
    }
    return finalized


def find_next_content_line(lines: list[MdLine], start_idx: int) -> MdLine | None:
    j = start_idx
    while j < len(lines):
        if not is_running_header(lines[j]):
            return lines[j]
        j += 1
    return None


def is_plain_title_candidate(line: MdLine, text: str) -> bool:
    if is_running_header(line):
        return False
    if line.list_item:
        return False
    if SECTION_RE.match(text) or CHAPTER_RE.match(text) or BRACKET_RE.match(text):
        return False
    if EXPLANATION_RE.match(text) or PROVISO_RE.match(text) or ILLUSTRATION_RE.match(text):
        return False
    if HTML_TABLE_PLACEHOLDER_RE.match(text):
        return False
    return True


def parse_document(lines: list[MdLine], html_tables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    html_tables = html_tables or []
    doc: dict[str, Any] = {"chapters": [], "sections": [], "footnotes": [], "orphans": []}
    current: dict[str, Any] = {}
    current_footnote: dict[str, Any] | None = None
    pending_section_title: str | None = None
    i = 0

    while i < len(lines):
        line = lines[i]
        text = line.text.strip()

        if is_running_header(line):
            i += 1
            continue

        footnote = parse_footnote(line, current_footnote)
        if footnote:
            if footnote is not current_footnote:
                doc["footnotes"].append(footnote)
            current_footnote = footnote
            i += 1
            continue

        if is_table_marker_line(line):
            i += 1
            continue

        html_marker = HTML_TABLE_PLACEHOLDER_RE.match(text)
        if html_marker:
            idx = int(html_marker.group("idx"))
            if idx < len(html_tables):
                parent = deepest_current(current)
                grid = html_tables[idx]["grid"]
                table_node = {
                    "grid": grid,
                    "page_start": line.page,
                    "page_end": line.page,
                    "x": line.x,
                    "y_start": line.y_start,
                }
                if parent:
                    parent["table"] = table_node
            i += 1
            continue

        recovered_next_i = recover_displaced_table_row(lines, i, current)
        if recovered_next_i is not None:
            i = recovered_next_i
            continue

        chapter_match = CHAPTER_RE.match(text)
        if chapter_match:
            chapter = {
                "type": "chapter",
                "number": chapter_match.group(2),
                "title": clean_text(chapter_match.group(3) or ""),
                "indent": line.indent,
                "line_start": line.line_no,
                "page_start": line.page,
                "page_end": line.page,
                "x": line.x,
                "y_start": line.y_start,
                "sections": [],
            }
            doc["chapters"].append(chapter)
            current = {"chapter": chapter}
            i += 1
            continue

        section_match = SECTION_RE.match(text)
        if (
            section_match
            and is_valid_section_header(line, text, pending_section_title, current)
        ):
            section = {
                "type": "section",
                "number": section_match.group("number"),
                "id": section_match.group("number"),
                "title": clean_text(pending_section_title or ""),
                "text_before_children": "",
                "text_after_children": "",
                "indent": line.indent,
                "line_start": line.line_no,
                "page_start": line.page,
                "page_end": line.page,
                "x": line.x,
                "y_start": line.y_start,
                "subsections": [],
                "children": [],
            }
            doc["sections"].append(section)
            if current.get("chapter"):
                current["chapter"]["sections"].append(section)
                current["chapter"].setdefault("_child_order", []).append(section)
            current = {"chapter": current.get("chapter"), "section": section}
            pending_section_title = None
            rest = consume_bracket_tokens(section_match.group("rest"), line, current)
            if rest:
                append_text(deepest_current(current), rest, line)
            i += 1
            continue

        if (line.bold or line.heading_level) and not BRACKET_RE.match(text) and not is_running_header(line):
            pending_section_title = text
            i += 1
            continue

        if pending_section_title is None and is_plain_title_candidate(line, text):
            nxt = find_next_content_line(lines, i + 1)
            if nxt is not None:
                nxt_text = nxt.text.strip()
                nxt_section = SECTION_RE.match(nxt_text)
                if nxt_section and is_valid_section_header(nxt, nxt_text, text, current):
                    pending_section_title = text
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
                doc["orphans"].append({"page": line.page, "line": line.line_no, "text": text})

        i += 1

    return doc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Markdown file converted from PDF")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file")
    args = parser.parse_args()

    lines, html_tables = parse_md_lines(args.input)
    doc = finalize_document(parse_document(lines, html_tables))
    payload = json.dumps(doc, ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
