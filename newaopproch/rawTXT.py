# # import fitz

# # pdf_path = r"/Users/avikalchauhan/Desktop/raglaw/sectionpdf/section7output.pdf"

# # doc = fitz.open(pdf_path)

# # output = []



# # for page_num, page in enumerate(doc, start=1):

# #     page_dict = page.get_text("dict")

# #     page_lines = []

# #     for block in page_dict["blocks"]:

# #         if block["type"] != 0:
# #             continue

# #         for line in block["lines"]:

# #             spans = line["spans"]

# #             text = ""

# #             x0 = spans[0]["bbox"][0]
# #             y0 = spans[0]["bbox"][1]

# #             font_size = spans[0]["size"]

# #             is_bold = False

# #             for span in spans:

# #                 text += span["text"]

# #                 if "Bold" in span["font"]:
# #                     is_bold = True

# #             page_lines.append({

# #                 "page": page_num,

# #                 "text": text.strip(),

# #                 "x0": round(x0,2),

# #                 "y0": round(y0,2),

# #                 "font_size": round(font_size,2),

# #                 "bold": is_bold

# #             })

# #     output.append(page_lines)

# # doc.close()


# # for page in output[:2]:

# #     print("="*80)

# #     print("PAGE",page[0]["page"])

# #     print("="*80)

# #     for line in page:

# #         print(line)



# # with open("/Users/avikalchauhan/Desktop/raglaw/newaopproch/txtformat/7income_tax_raw.txt","w",encoding="utf-8") as f:

# #     for page in output:

# #         f.write("\n")
# #         f.write("="*80)
# #         f.write("\n")

# #         f.write(f"PAGE {page[0]['page']}\n")

# #         f.write("="*80)
# #         f.write("\n")

# #         for line in page:

# #             f.write(
# #                 f"[x={line['x0']:7.2f}] "
# #                 f"[y={line['y0']:7.2f}] "
# #                 f"[font={line['font_size']:4.1f}] "
# #                 f"[bold={line['bold']}] "
# #                 f"{line['text']}\n"
# #             )

# import fitz

# SUPERSCRIPT_BIT = 1 << 0  # bit 0 of span['flags'] = superscript

# def extract_page_lines(page, page_num,size_ratio_threshold=0.85):
#     raw_spans = []
#     for block in page.get_text("dict")["blocks"]:
#         if block["type"] != 0:
#             continue
#         for line in block["lines"]:
#             for span in line["spans"]:
#                 raw_spans.append({
#                     "text": span["text"],
#                     "x0": span["bbox"][0],
#                     "y0": span["bbox"][1],
#                     "y1": span["bbox"][3],
#                     "size": span["size"],
#                     "bold": "Bold" in span["font"],
#                 })
#     if not raw_spans:
#         return []

#     # Determine the dominant (body) font size for this page
#     from collections import Counter
#     size_counts = Counter(round(s["size"], 1) for s in raw_spans)
#     dominant_size = size_counts.most_common(1)[0][0]

#     # Mark superscript by size ratio, not by flags
#     for s in raw_spans:
#         s["superscript"] = s["size"] < dominant_size * size_ratio_threshold
#     # Separate body spans from superscript/footnote-marker spans
    
    
#     body_spans = [s for s in raw_spans if not s["superscript"]]
#     sup_spans  = [s for s in raw_spans if s["superscript"]]

#     # Cluster body spans into visual lines by y0 proximity
#     body_spans.sort(key=lambda s: (round(s["y0"], 1), s["x0"]))
#     lines = []
#     for s in body_spans:
#         placed = False
#         for ln in lines:
#             if abs(ln["y0"] - s["y0"]) <= 3.0:   # tolerance in points
#                 ln["spans"].append(s)
#                 placed = True
#                 break
#         if not placed:
#             lines.append({"y0": s["y0"], "spans": [s]})

#     # Merge each superscript into the nearest line (by y-distance)
#     for sup in sup_spans:
#         best_line = min(lines, key=lambda ln: abs(ln["y0"] - sup["y0"]))
#         best_line["spans"].append(sup)

#     # Sort spans within each line by x0 and build final text
#     page_lines = []
#     for ln in lines:
#         ln["spans"].sort(key=lambda s: s["x0"])
#         text = ""
#         for s in ln["spans"]:
#             text += f"^{s['text']}" if s["superscript"] else s["text"]
#         page_lines.append({
#             "page": page_num,
#             "text": text.strip(),
#             "x0": round(ln["spans"][0]["x0"], 2),
#             "y0": round(ln["y0"], 2),
#             "font_size": round(ln["spans"][0]["size"], 2),
#             "bold": ln["spans"][0]["bold"],
#         })

#     page_lines.sort(key=lambda l: l["y0"])
#     return page_lines


# pdf_path = r"/Users/avikalchauhan/Desktop/raglaw/sectionpdf/section193output.pdf"

# doc = fitz.open(pdf_path)

# output = []

# for page_num, page in enumerate(doc, start=1):
#     page_lines = extract_page_lines(page, page_num)
#     output.append(page_lines)

# doc.close()


# for page in output[:2]:
#     print("=" * 80)
#     print("PAGE", page[0]["page"])
#     print("=" * 80)
#     for line in page:
#         print(line)


# with open(r"/Users/avikalchauhan/Desktop/raglaw/newaopproch/txtformat/193income_tax_raw.txt", "w", encoding="utf-8") as f:
#     for page in output:
#         f.write("\n")
#         f.write("=" * 80)
#         f.write("\n")
#         f.write(f"PAGE {page[0]['page']}\n")
#         f.write("=" * 80)
#         f.write("\n")
#         for line in page:
#             f.write(
#                 f"[x={line['x0']:7.2f}] "
#                 f"[y={line['y0']:7.2f}] "
#                 f"[font={line['font_size']:4.1f}] "
#                 f"[bold={line['bold']}] "
#                 f"{line['text']}\n"
#             )

#!/usr/bin/env python3
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


def split_table_cells(chars: list[Char]) -> list[list[Char]]:
    if not chars:
        return []
    cells: list[list[Char]] = [[chars[0]]]
    prev = chars[0]
    for ch in chars[1:]:
        gap = ch.x0 - prev.x1
        if gap > TABLE_CELL_GAP:
            cells.append([ch])
        else:
            cells[-1].append(ch)
        prev = ch
    return cells


def line_to_outline(page_num: int, chars: list[Char], table_mode: bool) -> list[OutLine]:
    if not chars:
        return []

    cells = split_table_cells(chars) if table_mode else [chars]
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


def extract_page_lines(page: fitz.Page, page_num: int, size_ratio_threshold: float = 0.85) -> list[OutLine]:
    lines = cluster_lines(iter_chars(page, size_ratio_threshold))
    output: list[OutLine] = []
    in_table = False
    seen_table_row = False

    for chars in lines:
        normal_outline = line_to_outline(page_num, chars, table_mode=False)
        if not normal_outline:
            continue
        whole = normal_outline[0]

        if in_table and seen_table_row and is_hierarchy_start(whole):
            in_table = False

        if is_centered_table_heading(whole):
            in_table = True
            seen_table_row = False
            output.extend(normal_outline)
            continue

        outlines = line_to_outline(page_num, chars, table_mode=in_table)
        if in_table and any(re.match(r"^\(?\d+\)?\.?", item.text.strip()) for item in outlines):
            seen_table_row = True
        output.extend(outlines)

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
