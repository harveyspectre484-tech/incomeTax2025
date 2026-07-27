"""
Extract PDF text with x/y coordinates, preserving table cell boundaries.

Why this exists:
  PyMuPDF's normal line/span extraction can collapse table columns into one
  string, e.g. "industry or10%" even though "10%" is visually in the rate
  column. This extractor uses character positions and, inside TABLE blocks,
  splits a visual line into separate cells when there is a large horizontal gap.

Usage:
  python extract_position_text_table_aware.py input.pdf -o output.txt
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz


SECTION_X_MAX = 30.5
NORMAL_WORD_GAP = 1.8
TABLE_CELL_GAP = 18.0
LINE_Y_TOLERANCE = 3.0
HARD_BOUNDARY_MARGIN = 5.0  # pts subtracted from a discovered column start


@dataclass
class Char:
    c: str
    x0: float
    x1: float
    y0: float
    y1: float
    size: float
    bold: bool
    superscript: bool = False


@dataclass
class OutLine:
    page: int
    text: str
    x0: float
    y0: float
    font_size: float
    bold: bool


def iter_chars(page: fitz.Page, size_ratio_threshold: float) -> list[Char]:
    chars: list[Char] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bold = "Bold" in span.get("font", "")
                size = float(span.get("size", 0))
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if not c:
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    chars.append(Char(c, x0, x1, y0, y1, size, bold))

    if not chars:
        return []

    dominant_size = Counter(round(ch.size, 1) for ch in chars).most_common(1)[0][0]
    for ch in chars:
        ch.superscript = ch.size < dominant_size * size_ratio_threshold
    return chars


def cluster_lines(chars: list[Char]) -> list[list[Char]]:
    body_chars = [ch for ch in chars if not ch.superscript]
    sup_chars = [ch for ch in chars if ch.superscript]
    body_chars.sort(key=lambda ch: (round(ch.y0, 1), ch.x0))

    lines: list[list[Char]] = []
    line_y: list[float] = []
    for ch in body_chars:
        placed = False
        for idx, y0 in enumerate(line_y):
            if abs(y0 - ch.y0) <= LINE_Y_TOLERANCE:
                lines[idx].append(ch)
                placed = True
                break
        if not placed:
            lines.append([ch])
            line_y.append(ch.y0)

    for ch in sup_chars:
        if not lines:
            lines.append([ch])
            line_y.append(ch.y0)
            continue
        idx = min(range(len(lines)), key=lambda i: abs(line_y[i] - ch.y0))
        lines[idx].append(ch)

    for line in lines:
        line.sort(key=lambda ch: ch.x0)
    return sorted(lines, key=lambda line: (min(ch.y0 for ch in line), min(ch.x0 for ch in line)))


def line_text(chars: list[Char], gap_for_space: float = NORMAL_WORD_GAP) -> str:
    text = ""
    prev: Char | None = None
    for ch in chars:
        if prev and ch.x0 - prev.x1 > gap_for_space and text and not text.endswith(" "):
            text += " "
        text += f"^{ch.c}" if ch.superscript else ch.c
        prev = ch
    return clean_text(text)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_table_cells(chars: list[Char], hard_boundaries: list[float] | None = None) -> list[list[Char]]:
    """
    Split a visual line of chars into table cells.

    Cells normally break wherever the horizontal gap exceeds TABLE_CELL_GAP.
    That alone is unreliable: a row's last column (e.g. a rate value) may sit
    right after the preceding text with only ordinary word-spacing, so the
    gap-based rule misses it even though the value clearly belongs in its
    own column (same x-position as the same column in other rows).

    `hard_boundaries` are x-positions (already known to be real column
    starts, discovered from rows that DID split correctly) at which we force
    a break as soon as a char's x0 reaches or passes the boundary -- even if
    the preceding gap is small -- as long as there is at least some gap
    (i.e. we're not slicing a boundary through the middle of a word).
    """
    if not chars:
        return []

    boundaries = sorted(hard_boundaries or [])
    # Skip boundaries that are already to the left of the first char.
    b_idx = 0
    while b_idx < len(boundaries) and boundaries[b_idx] <= chars[0].x0:
        b_idx += 1

    cells: list[list[Char]] = [[chars[0]]]
    prev = chars[0]
    for ch in chars[1:]:
        gap = ch.x0 - prev.x1
        crossed_boundary = False
        if b_idx < len(boundaries) and gap > 0.1 and prev.x1 < boundaries[b_idx] <= ch.x0:
            crossed_boundary = True
            b_idx += 1
        if gap > TABLE_CELL_GAP or crossed_boundary:
            cells.append([ch])
        else:
            cells[-1].append(ch)
        prev = ch
    return cells


def line_to_outline(
    page_num: int,
    chars: list[Char],
    table_mode: bool,
    hard_boundaries: list[float] | None = None,
) -> list[OutLine]:
    if not chars:
        return []

    cells = split_table_cells(chars, hard_boundaries) if table_mode else [chars]
    out: list[OutLine] = []
    for cell in cells:
        text = line_text(cell)
        if not text:
            continue
        out.append(
            OutLine(
                page=page_num,
                text=text,
                x0=round(min(ch.x0 for ch in cell), 2),
                y0=round(min(ch.y0 for ch in cell), 2),
                font_size=round(cell[0].size, 2),
                bold=any(ch.bold for ch in cell),
            )
        )
    return out


def is_centered_table_heading(outline: OutLine) -> bool:
    return outline.text.strip().upper() == "TABLE" and outline.x0 > 150


def is_hierarchy_start(outline: OutLine) -> bool:
    text = outline.text.strip()
    if re.match(r"^\d+[A-Z]?\.\s+", text) and outline.x0 <= SECTION_X_MAX:
        return True
    if re.match(r"^\(\d+\)\s+", text) and outline.x0 <= SECTION_X_MAX:
        return True
    if re.match(r"^\([a-z]\)\s+", text) and outline.x0 <= 45:
        return True
    return False


def find_table_regions(lines: list[list[Char]]) -> list[tuple[int, int]]:
    """
    First pass: walk the whole-line (non-table-split) text to find the
    [start, end) line-index ranges that belong to a TABLE block, using the
    same heuristics as before (centered "TABLE" heading starts a region,
    a hierarchy marker like "(2)" or "3." after at least one table row ends it).
    """
    regions: list[tuple[int, int]] = []
    in_table = False
    seen_table_row = False
    region_start: int | None = None

    for idx, chars in enumerate(lines):
        whole_outline = line_to_outline(0, chars, table_mode=False)
        if not whole_outline:
            continue
        whole = whole_outline[0]

        if in_table and seen_table_row and is_hierarchy_start(whole):
            regions.append((region_start, idx))
            in_table = False
            region_start = None

        if is_centered_table_heading(whole):
            if in_table and region_start is not None:
                regions.append((region_start, idx))
            in_table = True
            seen_table_row = False
            region_start = idx
            continue

        if in_table:
            tentative = line_to_outline(0, chars, table_mode=True)
            if any(re.match(r"^\(?\d+\)?\.?", item.text.strip()) for item in tentative):
                seen_table_row = True

    if in_table and region_start is not None:
        regions.append((region_start, len(lines)))

    return regions


def resolve_table_region(
    page_num: int, lines: list[list[Char]], start: int, end: int
) -> dict[int, list[OutLine]]:
    """
    Second pass over a single table region: split every line with the
    ordinary gap-based rule first, collect the x-position of any cell that
    is NOT the first cell on its line (a "trailing" cell -- these are the
    rows, like the rate column, that already split correctly because their
    gap happened to be large enough). The smallest such x-position is very
    likely the true start of the table's rightmost column, so we re-split
    every line in the region forcing a break there too. This recovers rows
    where that same column's value sits close enough to the preceding text
    that the gap-only rule would otherwise miss it.
    """
    baseline: dict[int, list[OutLine]] = {}
    trailing_x0s: list[float] = []
    for i in range(start, end):
        outlines = line_to_outline(page_num, lines[i], table_mode=True)
        baseline[i] = outlines
        if len(outlines) > 1:
            trailing_x0s.extend(o.x0 for o in outlines[1:])

    if not trailing_x0s:
        return baseline

    boundary = min(trailing_x0s) - HARD_BOUNDARY_MARGIN
    finalized: dict[int, list[OutLine]] = {}
    for i in range(start, end):
        finalized[i] = line_to_outline(
            page_num, lines[i], table_mode=True, hard_boundaries=[boundary]
        )
    return finalized


def extract_page_lines(page: fitz.Page, page_num: int, size_ratio_threshold: float = 0.85) -> list[OutLine]:
    lines = cluster_lines(iter_chars(page, size_ratio_threshold))
    regions = find_table_regions(lines)

    resolved: dict[int, list[OutLine]] = {}
    for start, end in regions:
        resolved.update(resolve_table_region(page_num, lines, start, end))

    output: list[OutLine] = []
    for idx, chars in enumerate(lines):
        if idx in resolved:
            output.extend(resolved[idx])
        else:
            output.extend(line_to_outline(page_num, chars, table_mode=False))
    return output


def extract_pdf(pdf_path: Path) -> list[list[OutLine]]:
    doc = fitz.open(pdf_path)
    try:
        return [extract_page_lines(page, page_num) for page_num, page in enumerate(doc, start=1)]
    finally:
        doc.close()


def write_text(pages: list[list[OutLine]], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as f:
        for page in pages:
            if not page:
                continue
            f.write("\n")
            f.write("=" * 80)
            f.write("\n")
            f.write(f"PAGE {page[0].page}\n")
            f.write("=" * 80)
            f.write("\n")
            for line in page:
                f.write(
                    f"[x={line.x0:7.2f}] "
                    f"[y={line.y0:7.2f}] "
                    f"[font={line.font_size:4.1f}] "
                    f"[bold={line.bold}] "
                    f"{line.text}\n"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    write_text(extract_pdf(args.pdf), args.output)


if __name__ == "__main__":
    main()
 