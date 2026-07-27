
# """
# Convert Markdown extracted from an Income-tax Act PDF into structured JSON.

# This is the Markdown equivalent of the position-aware TXT parser. It keeps the
# same broad JSON shape, but it does not require x/y/font metadata. It can parse:
#   - headings such as CHAPTER IV
#   - sections such as 206. text...
#   - legal hierarchy tokens such as (1)(a)(i)
#   - Explanation / Provided that / Illustration blocks
#   - Markdown pipe tables
#   - simple borderless tables converted to Markdown as aligned text

# Usage:
#   python income_tax_md_to_json.py input.md -o output.json
# """

# from __future__ import annotations

# import argparse
# import json
# import re
# from dataclasses import dataclass
# from pathlib import Path
# from typing import Any


# CHAPTER_RE = re.compile(r"^(CHAPTER|Chapter)\s+([IVXLCDM\dA-Z-]+)\b(?:\s*[-:.\u2014]\s*(.*))?$")
# SECTION_RE = re.compile(r"^(?P<number>\d+[A-Z]?)\.\s*(?P<rest>.*)$")
# BRACKET_RE = re.compile(r"^\s*\[?\(\s*(?P<token>[A-Za-z]+|\d+)\s*\)\s*")
# EXPLANATION_RE = re.compile(r"^(Explanation(?:\s+\d+)?\.?|Explanation(?:\s+[A-Z])?)\s*[-:.\u2014]?\s*(.*)$", re.I)
# PROVISO_RE = re.compile(r"^(Provided(?:\s+further)?\s+that)\b[:,]?\s*(.*)$", re.I)
# ILLUSTRATION_RE = re.compile(r"^(Illustration(?:\s+\d+)?\.?)\s*[-:.\u2014]?\s*(.*)$", re.I)
# FOOTNOTE_START_RE = re.compile(
#     r"^(?P<number>\d+)\.\s+"
#     r"(?P<text>(?:Sub\.|Sub-sections?|Words?|Clauses?|Clause|Omtt\.|Inserted|Ins\.|"
#     r"Omitted|Prior to|Explanation|Proviso).+)$",
#     re.I,
# )

# LOWER_ROMAN = {
#     "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
#     "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
# }
# UPPER_ROMAN = {x.upper() for x in LOWER_ROMAN}


# @dataclass
# class MdLine:
#     page: int
#     line_no: int
#     indent: int
#     text: str
#     heading_level: int | None = None
#     bold: bool = False
#     list_item: bool = False
#     numeric_list_item: bool = False


# def clean_text(text: str) -> str:
#     text = text.replace("\u00a0", " ")
#     return re.sub(r"\s+", " ", text).strip()


# def strip_md_inline(text: str) -> str:
#     text = text.strip().replace("\u00a0", " ")
#     text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
#     text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
#     text = re.sub(r"`([^`]+)`", r"\1", text)
#     text = re.sub(r"^\s*[-*+]\s+", "", text)
#     text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
#     text = re.sub(r"</?sup>", "", text, flags=re.I)
#     text = re.sub(r"</?sub>", "", text, flags=re.I)
#     text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
#     text = re.sub(r"</?[^>]+>", "", text)
#     text = re.sub(r"\(\s+([A-Za-z]+|\d+)\s+\)", r"(\1)", text)
#     return text.strip()


# def parse_md_lines(path: Path) -> list[MdLine]:
#     lines: list[MdLine] = []
#     page = 1

#     for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
#         raw = raw.rstrip()
#         if not raw.strip():
#             continue

#         page_match = re.match(r"^\s*(?:<!--\s*)?PAGE\s+(\d+)(?:\s*-->)?\s*$", raw, re.I)
#         if page_match:
#             page = int(page_match.group(1))
#             continue

#         heading_level = None
#         heading_match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", raw)
#         text = raw
#         if heading_match:
#             heading_level = len(heading_match.group(1))
#             text = heading_match.group(2)

#         indent = len(raw) - len(raw.lstrip(" "))
#         stripped = text.strip()
#         list_item = bool(re.match(r"^[-*+]\s+", stripped))
#         numeric_list_item = bool(re.match(r"^[-*+]\s+\d+[.)]\s+", stripped))
#         bold = bool(re.match(r"^\*\*[^*].*?\*\*$", stripped) or re.match(r"^__[^_].*?__$", stripped))
#         text = strip_md_inline(text)
#         if text:
#             lines.append(
#                 MdLine(
#                     page=page,
#                     line_no=line_no,
#                     indent=indent,
#                     text=text,
#                     heading_level=heading_level,
#                     bold=bold,
#                     list_item=list_item,
#                     numeric_list_item=numeric_list_item,
#                 )
#             )

#     return lines


# def new_node(kind: str, number: str | None, text: str, line: MdLine) -> dict[str, Any]:
#     return {
#         "type": kind,
#         "number": number,
#         "text_before_children": clean_text(text),
#         "text_after_children": "",
#         "indent": line.indent,
#         "line_start": line.line_no,
#         "page_start": line.page,
#         "page_end": line.page,
#         "children": [],
#     }


# def has_children(node: dict[str, Any]) -> bool:
#     return bool(node.get("_child_order"))


# def append_text(node: dict[str, Any] | None, text: str, line: MdLine, after_children: bool | None = None) -> None:
#     if not node or not text.strip():
#         return
#     if after_children is None:
#         after_children = has_children(node)
#     key = "text_after_children" if after_children else "text_before_children"
#     node[key] = clean_text((node.get(key) or "") + " " + text)
#     node["page_end"] = line.page


# def node_text(node: dict[str, Any] | None) -> str:
#     if not node:
#         return ""
#     return clean_text(" ".join(part for part in (
#         node.get("text_before_children", ""),
#         node.get("text_after_children", ""),
#         node.get("text", ""),
#     ) if part))


# def token_kind(token: str, current: dict[str, Any], line: MdLine | None = None) -> str:
#     if token.isdigit():
#         return "subsection"
#     if token.islower():
#         current_item = current.get("item")
#         if current_item and line and line.indent >= int(current_item.get("indent", -1)):
#             return "item"
#         return "sub_clause" if token in LOWER_ROMAN else "clause"
#     if token.isupper():
#         return "item"
#     return "item"


# def child_bucket(parent: dict[str, Any], kind: str) -> list[dict[str, Any]]:
#     key = {
#         "subsection": "subsections",
#         "clause": "clauses",
#         "sub_clause": "sub_clauses",
#         "item": "items",
#         "explanation": "explanations",
#         "proviso": "provisos",
#         "illustration": "illustrations",
#     }.get(kind, "children")
#     return parent.setdefault(key, [])


# def attach_child(parent: dict[str, Any], child: dict[str, Any]) -> None:
#     child_bucket(parent, child["type"]).append(child)
#     parent.setdefault("_child_order", []).append(child)


# def find_parent_for(kind: str, current: dict[str, Any]) -> dict[str, Any] | None:
#     section = current.get("section")
#     subsection = current.get("subsection")
#     clause = current.get("clause")
#     sub_clause = current.get("sub_clause")

#     if kind == "subsection":
#         return section
#     if kind == "clause":
#         return subsection or section
#     if kind == "sub_clause":
#         return clause or subsection or section
#     if kind == "item":
#         return sub_clause or clause or subsection or section
#     if kind in {"explanation", "proviso", "illustration"}:
#         return sub_clause or clause or subsection or section
#     return section


# def node_id_for(kind: str, number: str | None, current: dict[str, Any]) -> str:
#     parts: list[str] = []
#     for key in ("section", "subsection", "clause", "sub_clause", "item"):
#         if key == kind:
#             if number:
#                 parts.append(str(number))
#             break
#         node = current.get(key)
#         if node and node.get("number"):
#             parts.append(str(node["number"]))
#     return "-".join(parts)


# def set_current(kind: str, node: dict[str, Any], current: dict[str, Any]) -> None:
#     node.setdefault("id", node_id_for(kind, node.get("number"), current))
#     current[kind] = node
#     order = ["subsection", "clause", "sub_clause", "item"]
#     if kind in order:
#         for lower in order[order.index(kind) + 1:]:
#             current.pop(lower, None)


# def deepest_current(current: dict[str, Any]) -> dict[str, Any] | None:
#     for key in ("item", "sub_clause", "clause", "subsection", "section", "chapter"):
#         if current.get(key):
#             return current[key]
#     return None


# def continuation_target(line: MdLine, current: dict[str, Any]) -> dict[str, Any] | None:
#     candidates = [current[key] for key in ("item", "sub_clause", "clause", "subsection", "section") if current.get(key)]
#     if not candidates:
#         return current.get("chapter")

#     # Markdown has no x-position; indentation is the best available signal.
#     for node in candidates:
#         if line.indent >= int(node.get("indent", 0)):
#             return node
#     return candidates[-1]


# def consume_bracket_tokens(text: str, line: MdLine, current: dict[str, Any]) -> str:
#     rest = text
#     created: dict[str, Any] | None = None

#     while True:
#         match = BRACKET_RE.match(rest)
#         if not match:
#             break
#         token = match.group("token")
#         rest = rest[match.end():]
#         kind = token_kind(token, current, line)
#         parent = find_parent_for(kind, current)
#         if not parent:
#             break

#         node = new_node(kind, token, "", line)
#         attach_child(parent, node)
#         set_current(kind, node, current)
#         created = node

#     if created:
#         append_text(created, rest, line)
#         return ""
#     return text


# def parse_special_block(line: MdLine, current: dict[str, Any]) -> bool:
#     for kind, regex in (
#         ("explanation", EXPLANATION_RE),
#         ("proviso", PROVISO_RE),
#         ("illustration", ILLUSTRATION_RE),
#     ):
#         match = regex.match(line.text)
#         if not match:
#             continue
#         parent = find_parent_for(kind, current)
#         if not parent:
#             return False
#         label, body = match.group(1), match.group(2)
#         node = new_node(kind, label.rstrip("."), body, line)
#         attach_child(parent, node)
#         set_current(kind, node, current)
#         return True
#     return False


# def parse_footnote(line: MdLine, current_footnote: dict[str, Any] | None) -> dict[str, Any] | None:
#     match = FOOTNOTE_START_RE.match(line.text)
#     if match:
#         return {
#             "type": "footnote",
#             "number": match.group("number"),
#             "text": clean_text(match.group("text")),
#             "page_start": line.page,
#             "page_end": line.page,
#         }
#     if current_footnote and line.indent > 0:
#         current_footnote["text"] = clean_text(current_footnote.get("text", "") + " " + line.text)
#         current_footnote["page_end"] = line.page
#         return current_footnote
#     return None


# def is_pipe_table_line(text: str) -> bool:
#     return "|" in text and len([cell for cell in text.strip("|").split("|")]) >= 2


# def is_pipe_separator(text: str) -> bool:
#     cells = [cell.strip() for cell in text.strip().strip("|").split("|")]
#     return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


# def split_pipe_row(text: str) -> list[str]:
#     return [clean_text(cell) for cell in text.strip().strip("|").split("|")]


# def strip_item_number(text: str) -> tuple[int | None, str]:
#     match = re.match(r"^(?:\((\d+)\)|(\d+)\.)\s*(.*)$", text.strip())
#     if not match:
#         bare = re.fullmatch(r"\(?(\d+)\)?\.?", text.strip())
#         if bare:
#             return int(bare.group(1)), ""
#         return None, text.strip()
#     body = match.group(3).strip()
#     if not body and not re.search(r"[.)]", text.strip()):
#         return None, text.strip()
#     return int(match.group(1) or match.group(2)), body


# def is_table_title(line: MdLine) -> bool:
#     text = line.text.strip()
#     return text.upper() == "TABLE" or bool(re.match(r"^Sl\.\s*No\.?\b", text, re.I))


# def is_running_header(line: MdLine) -> bool:
#     return line.text.strip() in {
#         "Income Tax Department",
#         "Ministry of Finance, Government of India",
#     }


# def is_borderless_table_candidate(line: MdLine) -> bool:
#     text = line.text.strip()
#     if is_pipe_table_line(text):
#         return False
#     if line.numeric_list_item:
#         return True
#     if line.list_item:
#         return False
#     if re.match(r"^Sl\.\s*No\.?\b", text, re.I):
#         return True
#     if re.match(r"^\(?\d+\)?\.?\s{2,}\S+", text):
#         return True
#     if re.search(r"\S\s{3,}\S", text) and not line_starts_hierarchy(line):
#         return True
#     return False


# def line_starts_hierarchy(line: MdLine) -> bool:
#     text = line.text.strip()
#     return bool(
#         SECTION_RE.match(text)
#         or BRACKET_RE.match(text)
#         or CHAPTER_RE.match(text)
#         or EXPLANATION_RE.match(text)
#         or PROVISO_RE.match(text)
#         or ILLUSTRATION_RE.match(text)
#     )


# def line_starts_new_table_boundary(line: MdLine) -> bool:
#     text = line.text.strip()
#     if line.list_item:
#         return False
#     if CHAPTER_RE.match(text) or SECTION_RE.match(text):
#         return True
#     subsection = BRACKET_RE.match(text)
#     return bool(subsection and subsection.group("token").isdigit())


# def make_node_id(current: dict[str, Any], node: dict[str, Any] | None = None) -> str:
#     parts: list[str] = []
#     for key in ("section", "subsection", "clause", "sub_clause", "item"):
#         value = node if node and node.get("type") == key else current.get(key)
#         if value and value.get("number"):
#             parts.append(str(value["number"]))
#     return "-".join(parts)


# def table_id_for(parent: dict[str, Any] | None) -> str:
#     base = parent.get("id") if parent else None
#     if not base:
#         base = "table"
#     existing_count = 0
#     if parent:
#         existing_count += 1 if parent.get("table") else 0
#         existing_count += len(parent.get("tables", []))
#     return f"{base}-table-{existing_count + 1}"


# def column_key(index: int) -> str:
#     return f"column_{chr(ord('A') + index)}"


# def merge_text(existing: str, addition: str) -> str:
#     return clean_text((existing + " " + addition).strip())


# def normalize_header_text(text: str) -> str:
#     text = clean_text(text)
#     label_match = re.fullmatch(r"\(([A-Z])\)", text)
#     if label_match:
#         return f"[{label_match.group(1)}]"
#     return text


# def linearize_table(parent: dict[str, Any] | None, rows: list[dict[str, Any]], columns: list[str]) -> str:
#     if not parent:
#         prefix = "Table"
#     else:
#         number = parent.get("number") or ""
#         prefix = f"{parent.get('type', 'node').replace('_', ' ').title()} ({number})"

#     parts = [f"{prefix}: {node_text(parent)}" if parent else prefix]
#     if rows:
#         parts.append("Table items:")
#     for row in rows:
#         item = row.get("item_no")
#         cells = []
#         for index, column in enumerate(columns):
#             key = column_key(index)
#             value = row.get(key, "")
#             if value:
#                 cells.append(f"{column or key}: {value}")
#         item_label = f"Item {item}" if item is not None else "Row"
#         parts.append(f"{item_label} - " + "; ".join(cells) + ".")
#     return clean_text(" ".join(parts))


# def rows_from_pipe_table(lines: list[MdLine]) -> tuple[list[str], list[dict[str, Any]]]:
#     raw_rows = [split_pipe_row(line.text) for line in lines if not is_pipe_separator(line.text)]
#     if not raw_rows:
#         return [], []

#     columns = [normalize_header_text(cell) for cell in raw_rows[0]]
#     rows: list[dict[str, Any]] = []
#     for raw in raw_rows[1:]:
#         row: dict[str, Any] = {}
#         item_no: int | None = None
#         for index, cell in enumerate(raw):
#             found_item, body = strip_item_number(cell)
#             if index == 0 and found_item is not None:
#                 item_no = found_item
#                 row["item_no"] = found_item
#                 cell = body
#             row[column_key(index)] = cell
#         if item_no is None:
#             first_item, first_body = strip_item_number(raw[0] if raw else "")
#             if first_item is not None:
#                 row["item_no"] = first_item
#                 row["column_A"] = first_body
#         rows.append(row)
#     return columns, rows


# def split_borderless_cells(text: str) -> list[str]:
#     cells = [clean_text(cell) for cell in re.split(r"\s{2,}", text.strip()) if clean_text(cell)]
#     if len(cells) <= 1:
#         item_no, body = strip_item_number(text)
#         if item_no is not None:
#             return [str(item_no), body]
#     return cells


# def known_income_tax_columns(header_text: str) -> list[str]:
#     compact = clean_text(header_text).lower()
#     if "sl. no" in compact and "assessee" in compact and "rate of tax" in compact and "conditions" in compact:
#         return ["Sl. No.", "Assessee", "Income", "Rate of tax", "Conditions"]
#     return []


# def looks_like_column_letters(text: str) -> bool:
#     return bool(re.fullmatch(r"(?:[A-Z]\s+){2,}[A-Z]", clean_text(text)))


# def rows_from_loose_borderless_table(lines: list[MdLine]) -> tuple[list[str], list[dict[str, Any]]]:
#     header_parts: list[str] = []
#     rows: list[dict[str, Any]] = []
#     current_row: dict[str, Any] | None = None

#     for line in lines:
#         text = clean_text(line.text)
#         if not text or is_running_header(line) or looks_like_column_letters(text):
#             continue

#         item_no, body = strip_item_number(text)
#         if item_no is not None and (line.numeric_list_item or item_no <= 50):
#             current_row = {
#                 "item_no": item_no,
#                 "column_A": body,
#                 "raw_text": body,
#             }
#             rows.append(current_row)
#             continue

#         if current_row is None:
#             header_parts.append(text)
#             continue

#         current_row["raw_text"] = merge_text(current_row.get("raw_text", ""), text)
#         current_row["column_A"] = merge_text(current_row.get("column_A", ""), text)

#     columns = known_income_tax_columns(" ".join(header_parts))
#     if not columns:
#         columns = [normalize_header_text(" ".join(header_parts)) or "Text"]
#     return columns, rows


# def rows_from_borderless_table(lines: list[MdLine]) -> tuple[list[str], list[dict[str, Any]]]:
#     if any(line.list_item for line in lines):
#         return rows_from_loose_borderless_table(lines)

#     raw_rows = [split_borderless_cells(line.text) for line in lines]
#     raw_rows = [row for row in raw_rows if row]
#     if not raw_rows:
#         return [], []

#     header_rows: list[list[str]] = []
#     rows: list[dict[str, Any]] = []
#     current_row: dict[str, Any] | None = None
#     width = max(len(row) for row in raw_rows)

#     for raw in raw_rows:
#         first_item, first_body = strip_item_number(raw[0])
#         if first_item is None and current_row is None:
#             header_rows.append(raw)
#             continue

#         if first_item is not None:
#             current_row = {"item_no": first_item}
#             values = [first_body] + raw[1:]
#             for index, value in enumerate(values[:width]):
#                 current_row[column_key(index)] = value
#             rows.append(current_row)
#             continue

#         if current_row is not None:
#             for index, value in enumerate(raw[:width]):
#                 key = column_key(index)
#                 current_row[key] = merge_text(current_row.get(key, ""), value)

#     columns: list[str] = []
#     for index in range(width):
#         parts = [row[index] for row in header_rows if index < len(row)]
#         columns.append(normalize_header_text(" ".join(parts)) or column_key(index))
#     return columns, rows


# def structured_table_from_md(
#     title: str,
#     table_lines: list[MdLine],
#     parent: dict[str, Any] | None,
#     current: dict[str, Any],
#     kind: str,
# ) -> dict[str, Any]:
#     if kind == "pipe":
#         columns, rows = rows_from_pipe_table(table_lines)
#     else:
#         columns, rows = rows_from_borderless_table(table_lines)

#     first = table_lines[0]
#     last = table_lines[-1]
#     source_rule = make_node_id(current, parent) if parent else ""
#     return {
#         "type": "table",
#         "table_id": table_id_for(parent),
#         "title": title,
#         "format": kind,
#         "page_start": first.page,
#         "page_end": last.page,
#         "line_start": first.line_no,
#         "line_end": last.line_no,
#         "columns": columns,
#         "rows": rows,
#         "linearized_text": linearize_table(parent, rows, columns),
#         "source": {
#             "act": "Income-tax Act",
#             "rule": source_rule,
#             "page_start": first.page,
#             "page_end": last.page,
#             "line_start": first.line_no,
#         },
#     }


# def collect_pipe_table(lines: list[MdLine], start: int, parent: dict[str, Any] | None, current: dict[str, Any]) -> tuple[dict[str, Any], int]:
#     table_lines: list[MdLine] = []
#     i = start
#     while i < len(lines) and is_pipe_table_line(lines[i].text):
#         table_lines.append(lines[i])
#         i += 1
#     table = structured_table_from_md("TABLE", table_lines, parent, current, "pipe")
#     return table, i


# def collect_borderless_table(lines: list[MdLine], start: int, parent: dict[str, Any] | None, current: dict[str, Any]) -> tuple[dict[str, Any], int]:
#     title = "TABLE"
#     table_lines: list[MdLine] = []
#     i = start
#     loose_after_title = False

#     if lines[i].text.strip().upper() == "TABLE":
#         title = "TABLE"
#         i += 1
#         loose_after_title = True

#     while i < len(lines):
#         line = lines[i]
#         if is_running_header(line):
#             i += 1
#             continue
#         if loose_after_title:
#             if table_lines and line_starts_new_table_boundary(line):
#                 break
#             table_lines.append(line)
#             i += 1
#             continue
#         if is_borderless_table_candidate(line):
#             table_lines.append(line)
#             i += 1
#             continue
#         if table_lines and line_starts_hierarchy(line):
#             break
#         if table_lines:
#             break
#         table_lines.append(line)
#         i += 1

#     if not table_lines:
#         table_lines = [lines[start]]
#         i = start + 1

#     table = structured_table_from_md(title, table_lines, parent, current, "borderless")
#     return table, i


# def attach_table(table: dict[str, Any], parent: dict[str, Any] | None, doc: dict[str, Any]) -> None:
#     if parent:
#         if "table" not in parent:
#             parent["table"] = table
#         else:
#             parent.setdefault("tables", []).append(table)
#     else:
#         doc.setdefault("orphan_tables", []).append(table)


# def latest_table(current: dict[str, Any]) -> dict[str, Any] | None:
#     if current.get("_last_table"):
#         return current["_last_table"]
#     for key in ("item", "sub_clause", "clause", "subsection", "section"):
#         node = current.get(key)
#         if not node:
#             continue
#         if node.get("tables"):
#             return node["tables"][-1]
#         if node.get("table"):
#             return node["table"]
#     return None


# def recover_displaced_table_row(lines: list[MdLine], start: int, current: dict[str, Any]) -> int | None:
#     """
#     Some PDF-to-Markdown tools emit one table row after the body text has resumed.
#     Recover a bare line like "3." as a row for the latest table if that item number
#     is missing. This is intentionally conservative and only handles bare numbers.
#     """
#     match = re.fullmatch(r"(\d+)\.", lines[start].text.strip())
#     if not match:
#         return None

#     item_no = int(match.group(1))
#     table = latest_table(current)
#     if not table:
#         return None
#     if any(row.get("item_no") == item_no for row in table.get("rows", [])):
#         return None

#     parts: list[str] = []
#     i = start + 1
#     included_condition = False
#     while i < len(lines):
#         line = lines[i]
#         text = clean_text(line.text)
#         if is_running_header(line):
#             i += 1
#             break
#         if line_starts_new_table_boundary(line):
#             break
#         if line.list_item and not included_condition:
#             parts.append(text)
#             included_condition = True
#             i += 1
#             continue
#         if line.list_item:
#             break
#         parts.append(text)
#         i += 1

#     row = {
#         "item_no": item_no,
#         "column_A": clean_text(" ".join(parts)),
#         "raw_text": clean_text(" ".join(parts)),
#         "recovered_from_displaced_markdown": True,
#     }
#     table.setdefault("rows", []).append(row)
#     table["rows"].sort(key=lambda row_item: row_item.get("item_no", 10**9))
#     return i


# CHILD_OUTPUT_KEYS = {
#     "subsection": "subsections",
#     "clause": "clauses",
#     "sub_clause": "sub_clauses",
#     "item": "items",
#     "explanation": "explanations",
#     "proviso": "provisos",
#     "illustration": "illustrations",
#     "section": "sections",
# }


# def dominant_child_key(children: list[dict[str, Any]]) -> str:
#     for kind in ("subsection", "clause", "sub_clause", "item", "section"):
#         if any(child.get("type") == kind for child in children):
#             return CHILD_OUTPUT_KEYS[kind]
#     if children:
#         return CHILD_OUTPUT_KEYS.get(children[0].get("type"), "children")
#     return "children"


# def finalized_text_key(base: str, child_key: str) -> str:
#     return f"{base}_{child_key}"


# def finalize_node(node: dict[str, Any]) -> dict[str, Any]:
#     out: dict[str, Any] = {}
#     for key in ("type", "number", "id", "title", "indent", "line_start", "page_start", "page_end"):
#         if key in node:
#             out[key] = node[key]

#     raw_children = node.get("_child_order", [])
#     if raw_children:
#         child_key = dominant_child_key(raw_children)
#         out[finalized_text_key("text_before", child_key)] = node.get("text_before_children", "")

#         grouped_children: dict[str, list[dict[str, Any]]] = {}
#         for child in raw_children:
#             key = CHILD_OUTPUT_KEYS.get(child.get("type"), "children")
#             grouped_children.setdefault(key, []).append(finalize_node(child))

#         for key, children in grouped_children.items():
#             out[key] = children

#         out[finalized_text_key("text_after", child_key)] = node.get("text_after_children", "")
#     else:
#         text = node_text(node)
#         if text or node.get("type") not in {"section", "chapter"}:
#             out["text"] = text
#         out.setdefault("children", [])

#     if "table" in node:
#         out["table"] = node["table"]
#     if "tables" in node:
#         out["tables"] = node["tables"]
#     return out


# def finalize_document(doc: dict[str, Any]) -> dict[str, Any]:
#     finalized = {
#         "chapters": [finalize_node(chapter) for chapter in doc.get("chapters", [])],
#         "sections": [finalize_node(section) for section in doc.get("sections", [])],
#         "footnotes": doc.get("footnotes", []),
#         "orphans": doc.get("orphans", []),
#     }
#     if "orphan_tables" in doc:
#         finalized["orphan_tables"] = doc["orphan_tables"]
#     return finalized


# def parse_document(lines: list[MdLine]) -> dict[str, Any]:
#     doc: dict[str, Any] = {"chapters": [], "sections": [], "footnotes": [], "orphans": []}
#     current: dict[str, Any] = {}
#     current_footnote: dict[str, Any] | None = None
#     pending_section_title: str | None = None
#     i = 0

#     while i < len(lines):
#         line = lines[i]
#         text = line.text.strip()

#         footnote = parse_footnote(line, current_footnote)
#         if footnote:
#             if footnote is not current_footnote:
#                 doc["footnotes"].append(footnote)
#             current_footnote = footnote
#             i += 1
#             continue

#         if is_pipe_table_line(text):
#             parent = deepest_current(current)
#             table, next_i = collect_pipe_table(lines, i, parent, current)
#             attach_table(table, parent, doc)
#             current["_last_table"] = table
#             i = next_i
#             continue

#         if (is_table_title(line) and not line.list_item) or is_borderless_table_candidate(line):
#             parent = deepest_current(current)
#             table, next_i = collect_borderless_table(lines, i, parent, current)
#             if table["rows"]:
#                 attach_table(table, parent, doc)
#                 current["_last_table"] = table
#                 i = next_i
#                 continue

#         recovered_next_i = recover_displaced_table_row(lines, i, current)
#         if recovered_next_i is not None:
#             i = recovered_next_i
#             continue

#         chapter_match = CHAPTER_RE.match(text)
#         if chapter_match:
#             chapter = {
#                 "type": "chapter",
#                 "number": chapter_match.group(2),
#                 "title": clean_text(chapter_match.group(3) or ""),
#                 "indent": line.indent,
#                 "line_start": line.line_no,
#                 "page_start": line.page,
#                 "page_end": line.page,
#                 "sections": [],
#             }
#             doc["chapters"].append(chapter)
#             current = {"chapter": chapter}
#             i += 1
#             continue

#         section_match = SECTION_RE.match(text)
#         if (
#             section_match
#             and not line.list_item
#             and (len(section_match.group("number")) >= 3 or bool(section_match.group("rest").strip()))
#         ):
#             section = {
#                 "type": "section",
#                 "number": section_match.group("number"),
#                 "id": section_match.group("number"),
#                 "title": clean_text(pending_section_title or ""),
#                 "text_before_children": "",
#                 "text_after_children": "",
#                 "indent": line.indent,
#                 "line_start": line.line_no,
#                 "page_start": line.page,
#                 "page_end": line.page,
#                 "subsections": [],
#                 "children": [],
#             }
#             doc["sections"].append(section)
#             if current.get("chapter"):
#                 current["chapter"]["sections"].append(section)
#                 current["chapter"].setdefault("_child_order", []).append(section)
#             current = {"chapter": current.get("chapter"), "section": section}
#             pending_section_title = None
#             rest = consume_bracket_tokens(section_match.group("rest"), line, current)
#             append_text(deepest_current(current), rest, line)
#             i += 1
#             continue

#         if (line.bold or line.heading_level) and not BRACKET_RE.match(text):
#             pending_section_title = text
#             i += 1
#             continue

#         if parse_special_block(line, current):
#             i += 1
#             continue

#         rest = consume_bracket_tokens(text, line, current)
#         if rest:
#             node = continuation_target(line, current)
#             if node:
#                 append_text(node, rest, line)
#             else:
#                 doc["orphans"].append({"page": line.page, "line": line.line_no, "text": text})

#         i += 1

#     return doc


# def main() -> None:
#     parser = argparse.ArgumentParser()
#     parser.add_argument("input", type=Path, help="Markdown file converted from PDF")
#     parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file")
#     args = parser.parse_args()

#     lines = parse_md_lines(args.input)
#     doc = finalize_document(parse_document(lines))
#     payload = json.dumps(doc, ensure_ascii=False, indent=2)

#     if args.output:
#         args.output.write_text(payload + "\n", encoding="utf-8")
#     else:
#         print(payload)


# if __name__ == "__main__":
#     main()

#!/usr/bin/env python3
"""
Convert Markdown extracted from an Income-tax Act PDF into structured JSON.

This is the Markdown equivalent of the position-aware TXT parser. It keeps the
same broad JSON shape, but it does not require x/y/font metadata. It can parse:
  - headings such as CHAPTER IV
  - sections such as 206. text...
  - legal hierarchy tokens such as (1)(a)(i)
  - Explanation / Provided that / Illustration blocks
  - Markdown pipe tables
  - simple borderless tables converted to Markdown as aligned text

Usage:
  python income_tax_md_to_json.py input.md -o output.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def parse_md_lines(path: Path) -> list[MdLine]:
    lines: list[MdLine] = []
    page = 1

    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        raw = raw.rstrip()
        if not raw.strip():
            continue

        page_match = re.match(r"^\s*(?:<!--\s*)?PAGE\s+(\d+)(?:\s*-->)?\s*$", raw, re.I)
        if page_match:
            page = int(page_match.group(1))
            continue

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
                )
            )

    return lines


def new_node(kind: str, number: str | None, text: str, line: MdLine) -> dict[str, Any]:
    return {
        "type": kind,
        "number": number,
        "text_before_children": clean_text(text),
        "text_after_children": "",
        "indent": line.indent,
        "line_start": line.line_no,
        "page_start": line.page,
        "page_end": line.page,
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
        current_item = current.get("item")
        if current_item and line and line.indent >= int(current_item.get("indent", -1)):
            return "item"
        return "sub_clause" if token in LOWER_ROMAN else "clause"
    if token.isupper():
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
    return parent.setdefault(key, [])


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


def node_id_for(kind: str, number: str | None, current: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("section", "subsection", "clause", "sub_clause", "item"):
        if key == kind:
            if number:
                parts.append(str(number))
            break
        node = current.get(key)
        if node and node.get("number"):
            parts.append(str(node["number"]))
    return "-".join(parts)


def set_current(kind: str, node: dict[str, Any], current: dict[str, Any]) -> None:
    node.setdefault("id", node_id_for(kind, node.get("number"), current))
    current[kind] = node
    order = ["subsection", "clause", "sub_clause", "item"]
    if kind in order:
        for lower in order[order.index(kind) + 1:]:
            current.pop(lower, None)


def deepest_current(current: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("item", "sub_clause", "clause", "subsection", "section", "chapter"):
        if current.get(key):
            return current[key]
    return None


def continuation_target(line: MdLine, current: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [current[key] for key in ("item", "sub_clause", "clause", "subsection", "section") if current.get(key)]
    if not candidates:
        return current.get("chapter")

    # Markdown has no x-position; indentation is the best available signal.
    for node in candidates:
        if line.indent >= int(node.get("indent", 0)):
            return node
    return candidates[-1]


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
        append_text(created, rest, line)
        return ""
    return text


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


def is_pipe_table_line(text: str) -> bool:
    stripped = text.strip()
    return "|" in stripped and (stripped.count("|") >= 2 or len(stripped.split("|")) >= 3)


def is_pipe_separator(text: str) -> bool:
    cells = [cell.strip() for cell in text.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def split_pipe_row(text: str) -> list[str]:
    raw_cells = text.strip().split("|")
    if len(raw_cells) > 2 and raw_cells[0] == "" and raw_cells[-1] == "":
        raw_cells = raw_cells[1:-1]
    elif len(raw_cells) > 1 and raw_cells[0] == "":
        raw_cells = raw_cells[1:]
    elif len(raw_cells) > 1 and raw_cells[-1] == "":
        raw_cells = raw_cells[:-1]
    return [clean_text(cell) for cell in raw_cells]


def strip_item_number(text: str) -> tuple[int | None, str]:
    match = re.match(r"^(?:\((\d+)\)|(\d+)\.)\s*(.*)$", text.strip())
    if not match:
        bare = re.fullmatch(r"\(?(\d+)\)?\.?", text.strip())
        if bare:
            return int(bare.group(1)), ""
        return None, text.strip()
    body = match.group(3).strip()
    if not body and not re.search(r"[.)]", text.strip()):
        return None, text.strip()
    return int(match.group(1) or match.group(2)), body


def is_table_title(line: MdLine) -> bool:
    text = line.text.strip()
    return text.upper() == "TABLE" or bool(re.match(r"^Sl\.\s*No\.?\b", text, re.I))


def is_running_header(line: MdLine) -> bool:
    return line.text.strip() in {
        "Income Tax Department",
        "Ministry of Finance, Government of India",
    }


def is_borderless_table_candidate(line: MdLine) -> bool:
    text = line.text.strip()
    if is_pipe_table_line(text):
        return False
    if line.numeric_list_item:
        return True
    if line.list_item:
        return False
    if re.match(r"^Sl\.\s*No\.?\b", text, re.I):
        return True
    if re.match(r"^\(?\d+\)?\.?\s{2,}\S+", text):
        return True
    if re.search(r"\S\s{3,}\S", text) and not line_starts_hierarchy(line):
        return True
    return False


def line_starts_hierarchy(line: MdLine) -> bool:
    text = line.text.strip()
    return bool(
        SECTION_RE.match(text)
        or BRACKET_RE.match(text)
        or CHAPTER_RE.match(text)
        or EXPLANATION_RE.match(text)
        or PROVISO_RE.match(text)
        or ILLUSTRATION_RE.match(text)
    )


def line_starts_new_table_boundary(line: MdLine) -> bool:
    text = line.text.strip()
    if line.list_item:
        return False
    if CHAPTER_RE.match(text) or SECTION_RE.match(text):
        return True
    subsection = BRACKET_RE.match(text)
    return bool(subsection and subsection.group("token").isdigit())


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


def column_key(index: int) -> str:
    return f"column_{chr(ord('A') + index)}"


def merge_text(existing: str, addition: str) -> str:
    return clean_text((existing + " " + addition).strip())


def normalize_header_text(text: str) -> str:
    text = clean_text(text)
    label_match = re.fullmatch(r"\(([A-Z])\)", text)
    if label_match:
        return f"[{label_match.group(1)}]"
    return text


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
            key = column_key(index)
            value = row.get(key, "")
            if value:
                cells.append(f"{column or key}: {value}")
        item_label = f"Item {item}" if item is not None else "Row"
        parts.append(f"{item_label} - " + "; ".join(cells) + ".")
    return clean_text(" ".join(parts))


def rows_from_pipe_table(lines: list[MdLine]) -> tuple[list[str], list[dict[str, Any]]]:
    raw_rows = [split_pipe_row(line.text) for line in lines if not is_pipe_separator(line.text)]
    if not raw_rows:
        return [], []

    header_idx = 0
    for idx, raw in enumerate(raw_rows):
        if any(strip_item_number(cell)[0] is not None for cell in raw):
            header_idx = idx
            break

    columns = []
    if header_idx > 0:
        width = max(len(r) for r in raw_rows[:header_idx])
        for c in range(width):
            parts = [r[c] for r in raw_rows[:header_idx] if c < len(r)]
            columns.append(normalize_header_text(" ".join(parts)))
    else:
        columns = [normalize_header_text(cell) for cell in raw_rows[0]]

    rows: list[dict[str, Any]] = []
    current_row: dict[str, Any] | None = None

    for raw in raw_rows[header_idx:]:
        item_no, body = strip_item_number(raw[0] if raw else "")
        if item_no is not None:
            current_row = {"item_no": item_no}
            values = [body] + raw[1:]
            for index, value in enumerate(values):
                current_row[column_key(index)] = value
            rows.append(current_row)
            continue

        if current_row is not None:
            for index, value in enumerate(raw):
                key = column_key(index)
                if value:
                    current_row[key] = merge_text(current_row.get(key, ""), value)

    return columns, rows


def split_borderless_cells(text: str) -> list[str]:
    cells = [clean_text(cell) for cell in re.split(r"\s{2,}", text.strip()) if clean_text(cell)]
    if len(cells) <= 1:
        item_no, body = strip_item_number(text)
        if item_no is not None:
            return [str(item_no), body]
    return cells


def known_income_tax_columns(header_text: str) -> list[str]:
    compact = clean_text(header_text).lower()
    if "sl. no" in compact and "assessee" in compact and "rate of tax" in compact and "conditions" in compact:
        return ["Sl. No.", "Assessee", "Income", "Rate of tax", "Conditions"]
    return []


def looks_like_column_letters(text: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Z]\s+){2,}[A-Z]", clean_text(text)))


DEDUCTION_MARKERS = [
    "Entire amount.",
    "Minimum of—",
    "Amount being minimum of—",
    "Compensation received.",
    "Amount received, as restricted",
    "The commuted value shall be",
    "Rs. 500000.",
]


def rows_from_loose_borderless_table(lines: list[MdLine]) -> tuple[list[str], list[dict[str, Any]]]:
    header_parts: list[str] = []
    rows: list[dict[str, Any]] = []
    current_row: dict[str, Any] | None = None

    for line in lines:
        text = clean_text(line.text)
        if not text or is_running_header(line) or looks_like_column_letters(text):
            continue

        item_no, body = strip_item_number(text)
        if item_no is not None and (line.numeric_list_item or item_no <= 50 or current_row is not None):
            col_a = body
            col_b = ""
            for marker in DEDUCTION_MARKERS:
                if marker in body:
                    parts = body.split(marker, 1)
                    col_a = clean_text(parts[0])
                    col_b = clean_text(marker + parts[1])
                    break

            current_row = {
                "item_no": item_no,
                "column_A": "",
                "column_B": col_a,
                "column_C": col_b,
                "raw_text": body,
            }
            rows.append(current_row)
            continue

        if current_row is None:
            header_parts.append(text)
            continue

        current_row["raw_text"] = merge_text(current_row.get("raw_text", ""), text)

        is_deduction = any(text.startswith(m) or text == m for m in DEDUCTION_MARKERS)
        if is_deduction or (current_row.get("column_C") and not text.startswith("(") and not line.indent > 4):
            current_row["column_C"] = merge_text(current_row.get("column_C", ""), text)
        elif any(marker in text for marker in DEDUCTION_MARKERS):
            for marker in DEDUCTION_MARKERS:
                if marker in text:
                    parts = text.split(marker, 1)
                    if parts[0].strip():
                        current_row["column_B"] = merge_text(current_row.get("column_B", ""), parts[0])
                    current_row["column_C"] = merge_text(current_row.get("column_C", ""), marker + parts[1])
                    break
        else:
            current_row["column_B"] = merge_text(current_row.get("column_B", ""), text)

    columns = known_income_tax_columns(" ".join(header_parts))
    if not columns:
        columns = ["Sl. No.", "Nature of sum", "Amount of deduction"]
    return columns, rows


def rows_from_borderless_table(lines: list[MdLine]) -> tuple[list[str], list[dict[str, Any]]]:
    if any(line.list_item for line in lines) or any(strip_item_number(l.text)[0] is not None for l in lines):
        return rows_from_loose_borderless_table(lines)

    raw_rows = [split_borderless_cells(line.text) for line in lines]
    raw_rows = [row for row in raw_rows if row]
    if not raw_rows:
        return [], []

    header_rows: list[list[str]] = []
    rows: list[dict[str, Any]] = []
    current_row: dict[str, Any] | None = None
    width = max(len(row) for row in raw_rows)

    for raw in raw_rows:
        first_item, first_body = strip_item_number(raw[0])
        if first_item is None and current_row is None:
            header_rows.append(raw)
            continue

        if first_item is not None:
            current_row = {"item_no": first_item}
            values = [first_body] + raw[1:]
            for index, value in enumerate(values[:width]):
                current_row[column_key(index)] = value
            rows.append(current_row)
            continue

        if current_row is not None:
            for index, value in enumerate(raw[:width]):
                key = column_key(index)
                current_row[key] = merge_text(current_row.get(key, ""), value)

    columns: list[str] = []
    for index in range(width):
        parts = [row[index] for row in header_rows if index < len(row)]
        columns.append(normalize_header_text(" ".join(parts)) or column_key(index))
    return columns, rows


def structured_table_from_md(
    title: str,
    table_lines: list[MdLine],
    parent: dict[str, Any] | None,
    current: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    if kind == "pipe":
        columns, rows = rows_from_pipe_table(table_lines)
    else:
        columns, rows = rows_from_borderless_table(table_lines)

    first = table_lines[0]
    last = table_lines[-1]
    source_rule = make_node_id(current, parent) if parent else ""
    return {
        "type": "table",
        "table_id": table_id_for(parent),
        "title": title,
        "format": kind,
        "page_start": first.page,
        "page_end": last.page,
        "line_start": first.line_no,
        "line_end": last.line_no,
        "columns": columns,
        "rows": rows,
        "linearized_text": linearize_table(parent, rows, columns),
        "source": {
            "act": "Income-tax Act",
            "rule": source_rule,
            "page_start": first.page,
            "page_end": last.page,
            "line_start": first.line_no,
        },
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


def collect_table(lines: list[MdLine], start: int, parent: dict[str, Any] | None, current: dict[str, Any]) -> tuple[dict[str, Any], int]:
    title = "TABLE"
    table_lines: list[MdLine] = []
    i = start

    if lines[i].text.strip().upper() == "TABLE":
        title = "TABLE"
        i += 1

    while i < len(lines):
        line = lines[i]
        text = line.text.strip()

        if is_running_header(line):
            i += 1
            continue

        if table_lines:
            sub_match = BRACKET_RE.match(text)
            if sub_match and sub_match.group("token").isdigit():
                break
            if is_valid_section_header(line, text, None, current):
                break
            if CHAPTER_RE.match(text):
                break

        table_lines.append(line)
        i += 1

    if not table_lines:
        table_lines = [lines[start]]
        i = start + 1

    has_pipe = any(is_pipe_table_line(l.text) for l in table_lines)
    if has_pipe:
        pipe_lines = [l for l in table_lines if is_pipe_table_line(l.text)]
        borderless_lines = [l for l in table_lines if not is_pipe_table_line(l.text)]

        cols_pipe, rows_pipe = rows_from_pipe_table(pipe_lines) if pipe_lines else ([], [])
        cols_b, rows_b = rows_from_borderless_table(borderless_lines) if borderless_lines else ([], [])

        columns = cols_pipe if cols_pipe else cols_b
        combined_rows = list(rows_pipe)
        existing_item_nos = {r.get("item_no") for r in combined_rows if r.get("item_no") is not None}
        for rb in rows_b:
            item_no = rb.get("item_no")
            if item_no is not None and item_no in existing_item_nos:
                for ex in combined_rows:
                    if ex.get("item_no") == item_no:
                        for k, v in rb.items():
                            if k not in ex or not ex[k]:
                                ex[k] = v
                            elif v and v not in ex[k]:
                                ex[k] = merge_text(ex[k], v)
                        break
            else:
                combined_rows.append(rb)
                if item_no is not None:
                    existing_item_nos.add(item_no)

        combined_rows.sort(key=lambda r: r.get("item_no", 10**9))
        table_format = "pipe"
        first = table_lines[0]
        last = table_lines[-1]
        source_rule = make_node_id(current, parent) if parent else ""
        table = {
            "type": "table",
            "table_id": table_id_for(parent),
            "title": title,
            "format": table_format,
            "page_start": first.page,
            "page_end": last.page,
            "line_start": first.line_no,
            "line_end": last.line_no,
            "columns": columns,
            "rows": combined_rows,
            "linearized_text": linearize_table(parent, combined_rows, columns),
            "source": {
                "act": "Income-tax Act",
                "rule": source_rule,
                "page_start": first.page,
                "page_end": last.page,
                "line_start": first.line_no,
            },
        }
    else:
        table = structured_table_from_md(title, table_lines, parent, current, "borderless")

    return table, i


def merge_into_table(existing_table: dict[str, Any], new_table: dict[str, Any]) -> None:
    existing_table["page_end"] = max(existing_table.get("page_end", 0), new_table.get("page_end", 0))
    existing_table["line_end"] = max(existing_table.get("line_end", 0), new_table.get("line_end", 0))

    if not existing_table.get("columns") or existing_table["columns"] == ["Text"]:
        if new_table.get("columns") and new_table["columns"] != ["Text"]:
            existing_table["columns"] = new_table["columns"]

    existing_rows = existing_table.setdefault("rows", [])
    existing_item_nos = {r.get("item_no") for r in existing_rows if r.get("item_no") is not None}

    for row in new_table.get("rows", []):
        item_no = row.get("item_no")
        if item_no is not None and item_no in existing_item_nos:
            for ex in existing_rows:
                if ex.get("item_no") == item_no:
                    for k, v in row.items():
                        if k not in ex or not ex[k]:
                            ex[k] = v
                        elif v and v not in ex[k]:
                            ex[k] = merge_text(ex[k], v)
                    break
        else:
            existing_rows.append(row)
            if item_no is not None:
                existing_item_nos.add(item_no)

    existing_rows.sort(key=lambda r: r.get("item_no", 10**9))
    existing_table["linearized_text"] = linearize_table(None, existing_rows, existing_table.get("columns", []))


def attach_table(table: dict[str, Any], parent: dict[str, Any] | None, doc: dict[str, Any]) -> None:
    if parent:
        if "table" not in parent:
            parent["table"] = table
        else:
            merge_into_table(parent["table"], table)
    else:
        doc.setdefault("orphan_tables", []).append(table)


def latest_table(current: dict[str, Any]) -> dict[str, Any] | None:
    if current.get("_last_table"):
        return current["_last_table"]
    for key in ("item", "sub_clause", "clause", "subsection", "section"):
        node = current.get(key)
        if not node:
            continue
        if node.get("tables"):
            return node["tables"][-1]
        if node.get("table"):
            return node["table"]
    return None


def recover_displaced_table_row(lines: list[MdLine], start: int, current: dict[str, Any]) -> int | None:
    """
    Some PDF-to-Markdown tools emit one table row after the body text has resumed.
    Recover a bare line like "3." as a row for the latest table if that item number
    is missing. This is intentionally conservative and only handles bare numbers.
    """
    match = re.fullmatch(r"(\d+)\.", lines[start].text.strip())
    if not match:
        return None

    item_no = int(match.group(1))
    table = latest_table(current)
    if not table:
        return None
    if any(row.get("item_no") == item_no for row in table.get("rows", [])):
        return None

    parts: list[str] = []
    i = start + 1
    included_condition = False
    while i < len(lines):
        line = lines[i]
        text = clean_text(line.text)
        if is_running_header(line):
            i += 1
            break
        if line_starts_new_table_boundary(line):
            break
        if line.list_item and not included_condition:
            parts.append(text)
            included_condition = True
            i += 1
            continue
        if line.list_item:
            break
        parts.append(text)
        i += 1

    row = {
        "item_no": item_no,
        "column_A": clean_text(" ".join(parts)),
        "raw_text": clean_text(" ".join(parts)),
        "recovered_from_displaced_markdown": True,
    }
    table.setdefault("rows", []).append(row)
    table["rows"].sort(key=lambda row_item: row_item.get("item_no", 10**9))
    return i


CHILD_OUTPUT_KEYS = {
    "subsection": "subsections",
    "clause": "clauses",
    "sub_clause": "sub_clauses",
    "item": "items",
    "explanation": "explanations",
    "proviso": "provisos",
    "illustration": "illustrations",
    "section": "sections",
}


def dominant_child_key(children: list[dict[str, Any]]) -> str:
    for kind in ("subsection", "clause", "sub_clause", "item", "section"):
        if any(child.get("type") == kind for child in children):
            return CHILD_OUTPUT_KEYS[kind]
    if children:
        return CHILD_OUTPUT_KEYS.get(children[0].get("type"), "children")
    return "children"


def finalized_text_key(base: str, child_key: str) -> str:
    return f"{base}_{child_key}"


def finalize_node(node: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("type", "number", "id", "title", "indent", "line_start", "page_start", "page_end", "footnote_refs"):
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


def collect_footnote_links(obj: Any, links: dict[str, list[str]]) -> None:
    if isinstance(obj, dict):
        node_id = obj.get("id") or (
            f"{obj.get('type')}:{obj.get('number')}"
            if obj.get("type") and obj.get("number")
            else None
        )
        if node_id:
            for ref in obj.get("footnote_refs", []):
                links.setdefault(str(ref), []).append(str(node_id))
        for value in obj.values():
            collect_footnote_links(value, links)
    elif isinstance(obj, list):
        for value in obj:
            collect_footnote_links(value, links)


def finalize_document(doc: dict[str, Any]) -> dict[str, Any]:
    finalized = {
        "chapters": [finalize_node(chapter) for chapter in doc.get("chapters", [])],
        "sections": [finalize_node(section) for section in doc.get("sections", [])],
        "footnotes": doc.get("footnotes", []),
        "orphans": doc.get("orphans", []),
    }
    footnote_links: dict[str, list[str]] = {}
    collect_footnote_links(finalized.get("chapters", []), footnote_links)
    collect_footnote_links(finalized.get("sections", []), footnote_links)
    if footnote_links:
        finalized["footnote_reference_map"] = footnote_links
        for footnote in finalized["footnotes"]:
            refs = footnote_links.get(str(footnote.get("number")), [])
            if refs:
                footnote["referenced_by"] = refs
    if "orphan_tables" in doc:
        finalized["orphan_tables"] = doc["orphan_tables"]
    return finalized


def parse_document(lines: list[MdLine]) -> dict[str, Any]:
    doc: dict[str, Any] = {"chapters": [], "sections": [], "footnotes": [], "orphans": []}
    current: dict[str, Any] = {}
    current_footnote: dict[str, Any] | None = None
    pending_section_title: str | None = None
    i = 0

    while i < len(lines):
        line = lines[i]
        text = line.text.strip()

        footnote = parse_footnote(line, current_footnote)
        if footnote:
            if footnote is not current_footnote:
                doc["footnotes"].append(footnote)
            current_footnote = footnote
            i += 1
            continue

        if is_pipe_table_line(text) or (is_table_title(line) and not line.list_item) or is_borderless_table_candidate(line):
            parent = deepest_current(current)
            table, next_i = collect_table(lines, i, parent, current)
            if table.get("rows"):
                attach_table(table, parent, doc)
                current["_last_table"] = table
                i = next_i
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
            append_text(deepest_current(current), rest, line)
            i += 1
            continue

        if (line.bold or line.heading_level) and not BRACKET_RE.match(text):
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

    lines = parse_md_lines(args.input)
    doc = finalize_document(parse_document(lines))
    payload = json.dumps(doc, ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
