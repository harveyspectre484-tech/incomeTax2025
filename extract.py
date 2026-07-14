import pdfplumber

def find_fraction_bars(page):
    """
    Returns list of (y_position, x0, x1) for horizontal lines that look like
    fraction bars — short, isolated horizontal lines, not table borders.
    """
    bars = []
    for l in page.lines:
        if abs(l["top"] - l["bottom"]) < 1:      # horizontal line
            width = l["x1"] - l["x0"]
            if 15 < width < 100:                  # fraction bars are short
                bars.append((l["top"], l["x0"], l["x1"]))
    return bars


def find_equals_signs(page, words):
    """
    Returns list of word dicts that are literally the '=' character,
    used as an anchor to locate formula regions (LHS = RHS).
    """
    equals_words = []
    for w in words:
        if w["text"].strip() in ("=", "＝"):   # plain '=' or occasional full-width variant
            equals_words.append(w)
    return equals_words


def find_formula_regions(page, words):
    """
    Combines '=' sign detection and fraction-bar detection to locate
    likely formula regions on the page, returning bounding info for each.
    """
    bars = find_fraction_bars(page)
    equals_signs = find_equals_signs(page, words)

    formula_regions = []

    for eq in equals_signs:
        eq_y = eq["top"]
        eq_x = eq["x0"]

        # Look for a fraction bar near this '=' sign (same row, or just below it —
        # fractions usually sit right after the '=' on the same visual line, 
        # slightly offset vertically due to numerator/denominator stacking)
        nearby_bar = None
        for bar_y, bar_x0, bar_x1 in bars:
            if abs(bar_y - eq_y) < 15 and bar_x0 > eq_x - 5:
                nearby_bar = (bar_y, bar_x0, bar_x1)
                break

        formula_regions.append({
            "equals_y": eq_y,
            "equals_x": eq_x,
            "has_fraction": nearby_bar is not None,
            "fraction_bar": nearby_bar
        })

    return formula_regions


def reconstruct_formula(page, region, words):
    """
    Given a formula region (anchored by '=' sign, optionally with a fraction bar),
    reconstructs the LHS, and RHS (including numerator/denominator if fraction present).
    """
    eq_y = region["equals_y"]
    eq_x = region["equals_x"]

    # LHS: words on the same line, before the '=' sign
    lhs_words = [
        w for w in words
        if abs(w["top"] - eq_y) < 5 and w["x1"] <= eq_x
    ]
    lhs_words.sort(key=lambda w: w["x0"])
    lhs = " ".join(w["text"] for w in lhs_words)

    if region["has_fraction"]:
        bar_y, bar_x0, bar_x1 = region["fraction_bar"]

        # NOTE: bounded to within 20pt of the bar vertically, so unrelated
        # text elsewhere on the page can't get pulled into the fraction.
        numerator_words = [
            w for w in words
            if bar_x0 - 5 <= (w["x0"] + w["x1"]) / 2 <= bar_x1 + 5
            and w["top"] < bar_y and bar_y - w["top"] < 20
        ]
        denominator_words = [
            w for w in words
            if bar_x0 - 5 <= (w["x0"] + w["x1"]) / 2 <= bar_x1 + 5
            and w["top"] > bar_y and w["top"] - bar_y < 20
        ]
        numerator_words.sort(key=lambda w: w["x0"])
        denominator_words.sort(key=lambda w: w["x0"])

        numerator = " ".join(w["text"] for w in numerator_words)
        denominator = " ".join(w["text"] for w in denominator_words)

        # Any coefficient words between '=' and the fraction bar (e.g. "K ×")
        coefficient_words = [
            w for w in words
            if abs(w["top"] - eq_y) < 5
            and w["x0"] > eq_x
            and w["x1"] < bar_x0
        ]
        coefficient_words.sort(key=lambda w: w["x0"])
        coefficient = " ".join(w["text"] for w in coefficient_words)

        rhs = f"{coefficient} ({numerator} / {denominator})".strip()
    else:
        # No fraction — just take words to the right of '=' on the same line
        rhs_words = [
            w for w in words
            if abs(w["top"] - eq_y) < 5 and w["x0"] > eq_x
        ]
        rhs_words.sort(key=lambda w: w["x0"])
        rhs = " ".join(w["text"] for w in rhs_words)

    formula_text = f"{lhs} = {rhs}".strip()

    return formula_text


def reconstruct_formula_latex(page, region, words):
    """
    Same reconstruction as reconstruct_formula(), but emits a LaTeX string
    (fractions as \\frac{num}{den}) instead of the '(num / den)' plain form.
    """
    eq_y = region["equals_y"]
    eq_x = region["equals_x"]

    lhs_words = sorted(
        [w for w in words if abs(w["top"] - eq_y) < 5 and w["x1"] <= eq_x],
        key=lambda w: w["x0"]
    )
    lhs = " ".join(w["text"] for w in lhs_words)

    if region["has_fraction"]:
        bar_y, bar_x0, bar_x1 = region["fraction_bar"]

        numerator_words = sorted(
            [w for w in words
             if bar_x0 - 5 <= (w["x0"] + w["x1"]) / 2 <= bar_x1 + 5
             and w["top"] < bar_y and bar_y - w["top"] < 20],
            key=lambda w: w["x0"]
        )
        denominator_words = sorted(
            [w for w in words
             if bar_x0 - 5 <= (w["x0"] + w["x1"]) / 2 <= bar_x1 + 5
             and w["top"] > bar_y and w["top"] - bar_y < 20],
            key=lambda w: w["x0"]
        )
        coefficient_words = sorted(
            [w for w in words
             if abs(w["top"] - eq_y) < 5 and w["x0"] > eq_x and w["x1"] < bar_x0],
            key=lambda w: w["x0"]
        )

        numerator = " ".join(w["text"] for w in numerator_words)
        denominator = " ".join(w["text"] for w in denominator_words)
        coefficient = " ".join(w["text"] for w in coefficient_words)
        coefficient = coefficient.replace("×", r"\times").replace("÷", r"\div")

        rhs = rf"{coefficient} \frac{{{numerator}}}{{{denominator}}}".strip()
    else:
        rhs_words = sorted(
            [w for w in words if abs(w["top"] - eq_y) < 5 and w["x0"] > eq_x],
            key=lambda w: w["x0"]
        )
        rhs = " ".join(w["text"] for w in rhs_words)
        rhs = rhs.replace("×", r"\times").replace("÷", r"\div")

    return rf"$$ {lhs} = {rhs} $$"


def formula_consumed_word_ids(region, words):
    """
    Returns the set of id(word) for every word swallowed by this formula
    (LHS, '=', coefficient, numerator, denominator) so the normal
    line-grouping step can skip them and avoid mangling the layout.
    """
    consumed = set()
    eq_y, eq_x = region["equals_y"], region["equals_x"]

    for w in words:
        if abs(w["top"] - eq_y) < 5 and w["x1"] <= eq_x:
            consumed.add(id(w))
        if abs(w["top"] - eq_y) < 2 and abs(w["x0"] - eq_x) < 2:
            consumed.add(id(w))  # the '=' sign itself

    if region["has_fraction"]:
        bar_y, bar_x0, bar_x1 = region["fraction_bar"]
        for w in words:
            mid = (w["x0"] + w["x1"]) / 2
            if bar_x0 - 5 <= mid <= bar_x1 + 5 and 0 < abs(w["top"] - bar_y) < 20:
                consumed.add(id(w))
            elif abs(w["top"] - eq_y) < 5 and w["x0"] > eq_x and w["x1"] < bar_x0:
                consumed.add(id(w))

    return consumed


def find_footnote_separator_y(page):
    """
    Returns the y-position of the horizontal footnote separator line, if present.
    Returns None if no such line exists on this page (meaning no footnotes).
    """
    lines = page.lines  # horizontal/vertical lines drawn on the page
    rects = page.rects  # sometimes a thin rect is used instead of a line

    candidates = []

    for l in lines:
        # A horizontal line has same top/bottom y (or very close)
        if abs(l["top"] - l["bottom"]) < 1:
            width = l["x1"] - l["x0"]
            # Starts near left margin, doesn't run the full page width
            if l["x0"] < page.width * 0.15 and width < page.width * 0.6:
                candidates.append(l["top"])

    for r in rects:
        # Some PDFs render the separator as a very thin rectangle
        if r["height"] < 1.5:
            width = r["x1"] - r["x0"]
            if r["x0"] < page.width * 0.15 and width < page.width * 0.6:
                candidates.append(r["top"])

    if candidates:
        return min(candidates)  # topmost such line = where the footnote zone starts
    return None


def extract_page_with_positions(page):
    """Returns a list of (y_position, type, content) tuples in reading order.
    type can be 'text', 'table', 'footnote', or 'formula'.
    For 'formula', content is a dict: {"plain": ..., "latex": ...}."""
    elements = []

    footnote_start_y = find_footnote_separator_y(page)  # None if no footnote on this page

    # Tables (unchanged)
    tables = page.find_tables()
    for t in tables:
        y_top = t.bbox[1]
        elements.append((y_top, "table", t.extract()))

    words = page.extract_words()

    # ------------------------------------------------------------------
    # NEW: detect formulas (fraction bars anchored by '=' signs) FIRST.
    # Numerator/denominator words sit on their own separate visual lines
    # slightly above/below the main baseline, so if we let them fall
    # through to the generic per-line grouping below, they get split
    # into scrambled fragments (e.g. "L" / "N=K× M" instead of a single
    # coherent "N = K × (L / M)"). We pull those words out first, build
    # a clean formula element, and remove them from `words` so they are
    # never re-grouped into a garbled text line.
    # ------------------------------------------------------------------
    formula_regions = find_formula_regions(page, words)
    consumed_ids = set()
    for region in formula_regions:
        if not region["has_fraction"]:
            continue  # a plain "A = B" line isn't scrambled by line-grouping; leave it as text
        plain = reconstruct_formula(page, region, words)
        latex = reconstruct_formula_latex(page, region, words)
        elements.append((region["equals_y"], "formula", {"plain": plain, "latex": latex}))
        consumed_ids |= formula_consumed_word_ids(region, words)

    words = [w for w in words if id(w) not in consumed_ids]
    # ------------------------------------------------------------------

    lines = {}
    for w in words:
        y = round(w['top'])
        lines.setdefault(y, []).append(w)

    for y, words_in_line in lines.items():
        inside_table = any(t.bbox[1] <= y <= t.bbox[3] for t in tables)
        if inside_table:
            continue

        words_in_line.sort(key=lambda w: w["x0"])  # NEW: enforce left-to-right reading order
        text = " ".join(w["text"] for w in words_in_line).strip()
        if not text:
            continue

        # If a separator line exists on this page AND this line is below it -> footnote
        if footnote_start_y is not None and y >= footnote_start_y:
            elements.append((y, "footnote", text))
        else:
            elements.append((y, "text", text))

    elements.sort(key=lambda x: x[0])
    return elements


pdf_path = r"/Users/avikalchauhan/Desktop/raglaw/lawpdf/1_incometax_act.pdf"   # <-- put your actual PDF file path here

all_pages_data = []   # will store results for every page

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total pages: {len(pdf.pages)}")

    for page_number, page in enumerate(pdf.pages, start=1):
        page_elements = extract_page_with_positions(page)
        all_pages_data.append({
            "page_number": page_number,
            "elements": page_elements
        })
        print(f"Processed page {page_number} — {len(page_elements)} elements found")

# Quick check: print first page's elements to verify it worked
for y, kind, content in all_pages_data[0]["elements"]:
    print(y, kind, content)


print("\n========== FIRST 10 PAGES ==========\n")

for page_data in all_pages_data[4:13]:

    page_number = page_data["page_number"]

    print("\n")
    print("=" * 100)
    print(f"PAGE {page_number}")
    print("=" * 100)

    # for y, kind, content in page_data["elements"]:

    #     print(f"\nY POSITION: {y}")
    #     print(f"TYPE: {kind}")
    #     print("CONTENT:")

    #     if kind == "text":
    #         print(content)

    #     elif kind == "table":
    #         for row in content:
    #             print(row)

    #     print("-" * 100)
    for y, kind, content in page_data["elements"]:
        print(f"\nY POSITION: {y}")
        print(f"TYPE: {kind}")
        print("CONTENT:")
        print(content)
        # if kind == "table":
        #     for row in content:
        #         print(row)
        print("-" * 100)
        
        
import re

# ============================================================
# REGEX PATTERNS
# ============================================================

chapter_re    = re.compile(r"^CHAPTER\s+([IVXLC]+)", re.IGNORECASE)
section_re    = re.compile(r"^(\d+[A-Z]{0,3})\.\s+(.*)")
subsection_re = re.compile(r"^\((\d+[A-Z]?)\)")
clause_re     = re.compile(r"^\(([a-z]{1,3})\)")

# Inline amendment marker, e.g. "1[(32) ..." or "1[..."
inline_marker_re   = re.compile(r"^(\d+)\[")
trailing_bracket_re = re.compile(r"\]$")

# Footnote block detection
footnote_keyword_re = re.compile(
    r"(Substituted by|"
    r"Inserted by|"
    r"Omitted by|"
    r"Subs\. by|"
    r"Ins\. by|"
    r"w\.e\.f\.|"
    r"Prior to its|"
    r"Prior to substitution|"
    r"Prior to omission|"
    r"Amended by|"
    r"Renumbered by)",
    re.IGNORECASE
)

footnote_start_re = re.compile(
    r"^\d+\.\s*(?:"
    r"Substituted by|"
    r"Inserted by|"
    r"Omitted by|"
    r"Subs\. by|"
    r"Ins\. by|"
    r"Prior to|"
    r"Amended by|"
    r"Renumbered by"
    r")",
    re.IGNORECASE
)

# Captures the leading number of a footnote block, e.g. "1." -> "1"
footnote_number_re = re.compile(r"^(\d+)\.\s")


# ============================================================
# STATE TRACKING
# ============================================================

current = {
    "chapter": None,
    "chapter_title": None,
    "section": None,
    "section_title": None,
    "subsection": None,
    "clause": None,
    "footnote_refs": []     # tracks inline marker numbers seen in current chunk
}

chunks = []
buffer = []
footnote_buffer = []
table_counter = 0
current_page_number = None
in_footnote = False


# ============================================================
# FLUSH NORMAL TEXT
# ============================================================

def flush_buffer():
    if not buffer:
        return

    content = " ".join(buffer).strip()

    

    if content:
        chunks.append({
            **{k: v for k, v in current.items() if k != "footnote_refs"},
            "type": "text",
            "page_number": current_page_number,
            "content": content,
            "footnote_refs": list(current["footnote_refs"])   # snapshot, not live ref
        })

    buffer.clear()
    current["footnote_refs"] = []   # reset after flush so it doesn't leak into next chunk


# ============================================================
# FLUSH FOOTNOTE
# ============================================================

def flush_footnote():
    if not footnote_buffer:
        return

    content = " ".join(footnote_buffer).strip()

    if content:
        num_match = footnote_number_re.match(content)
        chunks.append({
            **{k: v for k, v in current.items() if k != "footnote_refs"},
            "type": "footnote",
            "page_number": current_page_number,
            "footnote_number": num_match.group(1) if num_match else None,
            "content": content
        })

    footnote_buffer.clear()


# ============================================================
# MAIN PARSING LOOP
# ============================================================

for page_data in all_pages_data:

    page_number = page_data["page_number"]
    current_page_number = page_number
    stream = page_data["elements"]
    in_footnote = False

    for y, kind, content in stream:

        # ====================================================
        # TABLE
        # ====================================================
        if kind == "table":
            flush_buffer()
            flush_footnote()
            in_footnote = False
            table_counter += 1
            chunks.append({
                **{k: v for k, v in current.items() if k != "footnote_refs"},
                "type": "table",
                "table_id": f"TABLE_{table_counter}",
                "page_number": page_number,
                "y_position": y,
                "rows": content
            })
            continue

        # ====================================================
        # FORMULA (NEW)
        # ====================================================
        if kind == "formula":
            flush_buffer()
            flush_footnote()
            in_footnote = False
            chunks.append({
                **{k: v for k, v in current.items() if k != "footnote_refs"},
                "type": "formula",
                "page_number": page_number,
                "y_position": y,
                "content": content["plain"],
                "latex": content["latex"]
            })
            continue

        # ====================================================
        # TEXT LINE
        # ====================================================
        line = content.strip()
        if not line:
            continue

        # ----------------------------------------------------
        # STRIP INLINE AMENDMENT MARKER (e.g. "1[(32) ...")
        # Capture the number as a footnote_ref BEFORE cleaning
        # ----------------------------------------------------
        footnote_ref = None
        marker_match = inline_marker_re.match(line)
        if marker_match:
            footnote_ref = marker_match.group(1)
            line = inline_marker_re.sub("", line)
            line = trailing_bracket_re.sub("", line)

        # ====================================================
        # FOOTNOTE BLOCK DETECTION
        # MUST COME BEFORE SECTION/SUBSECTION DETECTION
        # ====================================================
        is_bottom_area       = y > 500   # NOTE: consider replacing with page-relative threshold
        is_footnote_start    = bool(footnote_start_re.match(line))
        is_footnote_keyword  = bool(footnote_keyword_re.search(line))

        if is_footnote_start or (is_bottom_area and is_footnote_keyword):
            flush_buffer()
            in_footnote = True
            footnote_buffer.append(line)
            continue

        # ====================================================
        # FOOTNOTE CONTINUATION
        # ====================================================
        if in_footnote:
            footnote_buffer.append(line)
            continue

        # ====================================================
        # CHAPTER
        # ====================================================
        chapter_match = chapter_re.match(line)
        if chapter_match:
            flush_buffer()
            current["chapter"] = chapter_match.group(1)
            current["chapter_title"] = line
            current["section"] = None
            current["section_title"] = None
            current["subsection"] = None
            current["clause"] = None
            continue

        # ====================================================
        # SECTION
        # ====================================================
        section_match = section_re.match(line)
        if section_match:
            flush_buffer()
            current["section"] = section_match.group(1)
            current["section_title"] = section_match.group(2).strip()
            current["subsection"] = None
            current["clause"] = None
            if footnote_ref:
                current["footnote_refs"].append(footnote_ref)
            buffer.append(line)
            continue

        # ====================================================
        # SUBSECTION
        # ====================================================
        subsection_match = subsection_re.match(line)
        if subsection_match:
            flush_buffer()
            current["subsection"] = subsection_match.group(1)
            current["clause"] = None
            if footnote_ref:
                current["footnote_refs"].append(footnote_ref)
            buffer.append(line)
            continue

        # ====================================================
        # CLAUSE
        # ====================================================
        clause_match = clause_re.match(line)
        if clause_match:
            flush_buffer()
            current["clause"] = clause_match.group(1)
            if footnote_ref:
                current["footnote_refs"].append(footnote_ref)
            buffer.append(line)
            continue

        # ====================================================
        # NORMAL / CONTINUATION TEXT
        # ====================================================
        if footnote_ref and footnote_ref not in current["footnote_refs"]:
            current["footnote_refs"].append(footnote_ref)

        buffer.append(line)

    # ========================================================
    # END OF PAGE — flush so text doesn't merge across pages
    # ========================================================
    flush_buffer()
    flush_footnote()
    in_footnote = False

    # DO NOT reset chapter/section/subsection/clause here —
    # they must persist across pages.


# ============================================================
# FINAL FLUSH
# ============================================================

flush_buffer()
flush_footnote()


# ============================================================
# LINK FOOTNOTES TO THEIR SOURCE CHUNKS
# Uses (page_number, footnote_number) as the matching key,
# so multiple footnotes on the same page are disambiguated.
# ============================================================

def link_footnotes(chunks):
    footnote_lookup = {}
    for c in chunks:
        if c["type"] == "footnote" and c.get("footnote_number"):
            key = (c["page_number"], c["footnote_number"])
            footnote_lookup[key] = c["content"]

    for c in chunks:
        if c["type"] == "text" and c.get("footnote_refs"):
            linked = []
            for ref in c["footnote_refs"]:
                key = (c["page_number"], ref)
                if key in footnote_lookup:
                    linked.append(footnote_lookup[key])
            if linked:
                c["linked_footnotes"] = linked

    return chunks


chunks = link_footnotes(chunks)


# ============================================================
# SUMMARY
# ============================================================

print(f"\nTotal chunks created: {len(chunks)}")

text_chunks     = sum(1 for c in chunks if c["type"] == "text")
footnote_chunks = sum(1 for c in chunks if c["type"] == "footnote")
table_chunks    = sum(1 for c in chunks if c["type"] == "table")
formula_chunks  = sum(1 for c in chunks if c["type"] == "formula")
linked_chunks   = sum(1 for c in chunks if c.get("linked_footnotes"))

print(f"Text chunks: {text_chunks}")
print(f"Footnote chunks: {footnote_chunks}")
print(f"Table chunks: {table_chunks}")
print(f"Formula chunks: {formula_chunks}")
print(f"Text chunks with linked footnotes: {linked_chunks}")


# ============================================================
# QUICK SANITY CHECK
# ============================================================

print("\n")
print("=" * 120)
print("QUICK SANITY CHECK")
print("=" * 120)

# Pages you want to inspect
pages_to_check = [7,8,9,10,11,12,13,14,15,45,46,47,48,49,50,51,52,53,54]

for chunk_number, chunk in enumerate(chunks, start=1):

    # Skip pages not selected
    if chunk["page_number"] not in pages_to_check:
        continue

    print("\n")
    print("=" * 120)
    print(f"CHUNK NUMBER : {chunk_number}")
    print(f"PAGE NUMBER  : {chunk['page_number']}")
    print(f"TYPE         : {chunk['type']}")
    print("-" * 120)
    print(f"CHAPTER      : {chunk['chapter']}")
    print(f"CHAPTER TITLE: {chunk['chapter_title']}")
    print(f"SECTION      : {chunk['section']}")
    print(f"SECTION TITLE: {chunk['section_title']}")
    print(f"SUBSECTION   : {chunk['subsection']}")
    print(f"CLAUSE       : {chunk['clause']}")

    # ---- NEW: show footnote references, if any ----
    if chunk.get("footnote_refs"):
        print(f"FOOTNOTE REFS: {chunk['footnote_refs']}")

    # ---- NEW: show footnote number, if this chunk itself is a footnote ----
    if chunk["type"] == "footnote" and chunk.get("footnote_number"):
        print(f"FOOTNOTE NO. : {chunk['footnote_number']}")

    print("=" * 120)

    # ========================================================
    # TEXT CONTENT
    # ========================================================
    if chunk["type"] == "text":
        print("\n[TEXT CONTENT]\n")
        print(chunk["content"])

        # ---- NEW: show linked footnote(s), if any ----
        if chunk.get("linked_footnotes"):
            print("\n[LINKED FOOTNOTE(S)]\n")
            for i, fn in enumerate(chunk["linked_footnotes"], start=1):
                print(f"({i}) {fn}")

    # ========================================================
    # FOOTNOTE CONTENT
    # ========================================================
    elif chunk["type"] == "footnote":
        print("\n[FOOTNOTE CONTENT]\n")
        print(chunk["content"])

    # ========================================================
    # TABLE CONTENT
    # ========================================================
    elif chunk["type"] == "table":
        print(f"\n[TABLE CONTENT: {chunk['table_id']}]\n")
        print(f"Y POSITION: {chunk['y_position']}")
        print()
        for row_number, row in enumerate(chunk["rows"], start=1):
            print(f"ROW {row_number}: {row}")

    # ========================================================
    # FORMULA CONTENT (NEW)
    # ========================================================
    elif chunk["type"] == "formula":
        print("\n[FORMULA CONTENT]\n")
        print(chunk["content"])
        print("\n[FORMULA LATEX]\n")
        print(chunk["latex"])

print("\n")
print("=" * 120)
print("END OF QUICK SANITY CHECK")
print("=" * 120)