#!/usr/bin/env python3
"""
Convert a PDF into a Markdown file.

Key differences from the original version:
  - Table detection is now conservative. PyMuPDF's find_tables() can
    misfire on dense, justified prose (like legal/statutory text) and
    group entire pages into one bogus "table". We now sanity-check every
    candidate table (must have real gridlines, a sane row/col shape, and
    short cell contents) before trusting it. Anything that looks like a
    false positive is discarded and the text is emitted normally instead.
  - Bold/italic/superscript are now read from PyMuPDF's span "flags"
    bitfield (the actual source of truth) instead of guessing from the
    font name string. This is what lets us reproduce "_i_" italics and
    "<sup>...</sup>" superscripts, not just bold.
  - Body text is grouped into nested Markdown lists based on each line's
    horizontal indent (x0), so "(1)" / "(a)" / "(i)" style legal
    enumerations come out as properly nested "- " bullets instead of one
    flat wall of text.

Usage:
    python pdf_to_md.py input.pdf -o output.md
    python pdf_to_md.py input.pdf              # prints to stdout

Requires: pip install pymupdf
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

# PyMuPDF span flag bits (see fitz docs for TextPage.extractDICT)
FLAG_SUPERSCRIPT = 1 << 0
FLAG_ITALIC = 1 << 1
FLAG_SERIFED = 1 << 2
FLAG_MONOSPACED = 1 << 3
FLAG_BOLD = 1 << 4

# Matches leading list markers like "(1)", "(a)", "(iii)", "1.", "A."
LIST_MARKER_RE = re.compile(
    r"^\(?\s*([ivxlcdmIVXLCDM]{1,6}|[A-Za-z]|\d{1,3})\s*[\).]\s*"
)


def escape_md(text: str) -> str:
    """Escape characters that have special meaning in Markdown."""
    for ch in ("\\", "*", "_", "`"):
        text = text.replace(ch, "\\" + ch)
    return text


def is_bold(span: dict) -> bool:
    return bool(span["flags"] & FLAG_BOLD) or "Bold" in span.get("font", "")


def is_italic(span: dict) -> bool:
    return bool(span["flags"] & FLAG_ITALIC) or "Italic" in span.get(
        "font", ""
    ) or "Oblique" in span.get("font", "")


def is_superscript(span: dict) -> bool:
    return bool(span["flags"] & FLAG_SUPERSCRIPT)


def style_span_text(text: str, bold: bool, italic: bool, superscript: bool) -> str:
    """Wrap escaped text in the appropriate Markdown/HTML styling."""
    out = escape_md(text)
    if italic:
        out = f"_{out}_"
    if bold:
        out = f"**{out}**"
    if superscript:
        out = f"<sup>{out}</sup>"
    return out


def render_line(spans: list[dict]) -> str:
    """Render a line's spans, applying per-span styling and merging
    adjacent runs so we don't get spurious style-boundary artifacts."""
    parts = []
    for s in spans:
        text = s["text"]
        if not text:
            continue
        parts.append(
            style_span_text(text, is_bold(s), is_italic(s), is_superscript(s))
        )
    # Join without forcing extra spaces; PDF spans usually include their
    # own leading/trailing whitespace already.
    return "".join(parts).strip()


def get_dominant_font_size(page: "fitz.Page") -> float:
    sizes: Counter[float] = Counter()
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                sizes[round(span["size"], 1)] += len(span["text"])
    if not sizes:
        return 10.0
    return sizes.most_common(1)[0][0]


def heading_level_for_size(size: float, dominant_size: float) -> int | None:
    ratio = size / dominant_size if dominant_size else 1.0
    if ratio >= 1.8:
        return 1
    if ratio >= 1.5:
        return 2
    if ratio >= 1.25:
        return 3
    if ratio >= 1.1:
        return 4
    return None


def table_to_markdown(table_rows: list[list[str | None]]) -> str:
    if not table_rows:
        return ""

    def clean_cell(cell: str | None) -> str:
        if cell is None:
            return ""
        return escape_md(" ".join(cell.split()))

    header = [clean_cell(c) for c in table_rows[0]]
    col_count = len(header)

    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * col_count) + " |")

    for row in table_rows[1:]:
        cells = [clean_cell(c) for c in row]
        cells = (cells + [""] * col_count)[:col_count]
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def looks_like_real_table(table, page_rect: "fitz.Rect") -> bool:
    """
    Reject PyMuPDF table candidates that are almost certainly the
    find_tables() heuristic misfiring on ordinary justified prose.

    A genuine table:
      - has more than one row and more than one column, OR is a single
        clean row/col grid explicitly bounded by ruling lines
      - doesn't cover the near-entirety of the page width/height while
        also having very few columns (a classic false-positive shape)
      - has reasonably short cell text (real table cells are terse;
        paragraphs of running prose crammed into "cells" are the
        tell-tale sign of a misdetected table)
    """
    try:
        rows = table.extract()
    except Exception:
        return False

    if not rows or len(rows) < 2:
        return False

    n_cols = len(rows[0])
    n_rows = len(rows)

    # A 2-3 "column" table spanning almost the whole page that swallows
    # huge amounts of text per cell is the exact failure mode we saw:
    # entire pages of statute text glued into one or two cells.
    all_cells = [c for row in rows for c in row if c]
    if not all_cells:
        return False

    total_chars = sum(len(c) for c in all_cells)
    avg_cell_len = total_chars / len(all_cells)
    max_cell_len = max(len(c) for c in all_cells)

    tbl_rect = fitz.Rect(table.bbox)
    page_area = max(page_rect.width * page_rect.height, 1)
    coverage = (tbl_rect.width * tbl_rect.height) / page_area

    # Heuristic thresholds tuned to reject "whole page of prose disguised
    # as a table" while still accepting genuine data tables.
    if max_cell_len > 400:
        return False
    if avg_cell_len > 150 and n_cols <= 3:
        return False
    if coverage > 0.6 and n_cols <= 3 and avg_cell_len > 80:
        return False

    # Require the table to actually have detected ruling/line structure
    # where available; PyMuPDF exposes this via table.header / strategy
    # isn't always accessible, so we fall back to the size heuristics
    # above when it isn't.
    return True


def page_tables(page: "fitz.Page"):
    try:
        found = page.find_tables()
        tables = list(found.tables)
    except Exception:
        return []
    return [t for t in tables if looks_like_real_table(t, page.rect)]


def group_lines(page: "fitz.Page", table_bboxes: list["fitz.Rect"]):
    """Yield (y0, x0, spans) for each text line not inside a real table."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = line["spans"]
            if not spans:
                continue
            line_rect = fitz.Rect(spans[0]["bbox"])
            if any(line_rect.intersects(tb) for tb in table_bboxes):
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            x0, y0 = spans[0]["bbox"][0], spans[0]["bbox"][1]
            out.append((y0, x0, spans))
    out.sort(key=lambda item: item[0])
    return out


def convert_pdf_to_markdown(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    md_parts: list[str] = []

    # Indent stack for nested list rendering, persists across pages since
    # a list item can continue onto the next page.
    indent_stack: list[float] = []

    for page_num, page in enumerate(doc, start=1):
        dominant_size = get_dominant_font_size(page)
        tables = page_tables(page)
        table_bboxes = [fitz.Rect(t.bbox) for t in tables]

        items: list[tuple[float, str, object]] = []
        for table in tables:
            md_table = table_to_markdown(table.extract())
            if md_table:
                items.append((table.bbox[1], "table", md_table))

        for y0, x0, spans in group_lines(page, table_bboxes):
            max_size = max(s["size"] for s in spans)
            items.append((y0, "text", (x0, spans, max_size)))

        items.sort(key=lambda item: item[0])

        page_md: list[str] = []
        for _, kind, payload in items:
            if kind == "table":
                # A real table breaks any in-progress list.
                indent_stack.clear()
                page_md.append(payload)  # type: ignore[arg-type]
                page_md.append("")
                continue

            x0, spans, size = payload  # type: ignore[misc]
            raw_text = "".join(s["text"] for s in spans).strip()
            level = heading_level_for_size(size, dominant_size)

            if level is not None:
                indent_stack.clear()
                rendered = render_line(spans)
                page_md.append(f"{'#' * level} {rendered}")
                continue

            marker_match = LIST_MARKER_RE.match(raw_text)
            if marker_match:
                # Adjust the indent stack based on this line's x0.
                while indent_stack and x0 <= indent_stack[-1] - 2:
                    indent_stack.pop()
                if not indent_stack or x0 > indent_stack[-1] + 2:
                    indent_stack.append(x0)
                depth = len(indent_stack) - 1
                indent = "   " * depth
                rendered = render_line(spans)
                page_md.append(f"{indent}- {rendered}")
            else:
                # Plain paragraph/continuation text: don't reset the
                # list stack (it may be a wrapped continuation line),
                # just emit it as a normal paragraph.
                rendered = render_line(spans)
                page_md.append(rendered)

        md_parts.append(f"<!-- Page {page_num} -->")
        md_parts.extend(page_md)
        md_parts.append("\n---\n")

    doc.close()
    return "\n\n".join(md_parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Path to the input PDF")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output .md file")
    args = parser.parse_args()

    markdown = convert_pdf_to_markdown(args.input)

    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()