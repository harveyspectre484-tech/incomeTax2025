"""
Verifies that a converted .md file contains (roughly) all the words
present in the source PDF, catching genuine content loss while
tolerating harmless reordering caused by markdown table formatting.

Usage:
    python verify_pdf_md.py path/to/file.pdf path/to/file.md
"""

import sys
import re
from collections import Counter


import fitz  # PyMuPDF


def tokenize(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def get_pdf_text_with_positions(pdf_path):
    """Returns full plain text, plus a list of (word, page_num, context) for locating misses."""
    doc = fitz.open(pdf_path)
    full_text = ""
    word_locations = []  # (word, page_num, surrounding_snippet)

    for page_num, page in enumerate(doc, start=1):
        page_text = page.get_text()
        full_text += page_text

        words = re.findall(r"[a-zA-Z0-9]+", page_text)
        lower_words = [w.lower() for w in words]
        for i, w in enumerate(lower_words):
            start = max(0, i - 4)
            end = min(len(words), i + 5)
            snippet = " ".join(words[start:end])
            word_locations.append((w, page_num, snippet))

    doc.close()
    return full_text, word_locations


def verify(pdf_path, md_path):
    pdf_text, word_locations = get_pdf_text_with_positions(pdf_path)

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    pdf_words = Counter(tokenize(pdf_text))
    md_words = Counter(tokenize(md_text))

    missing = pdf_words - md_words  # words under-represented in md vs pdf
    total_missing = sum(missing.values())
    total_pdf_words = sum(pdf_words.values())

    print(f"PDF: {pdf_path}")
    print(f"MD:  {md_path}")
    print(f"Total words in PDF: {total_pdf_words}")
    print(f"Total words in MD:  {sum(md_words.values())}")
    print(f"Missing word instances: {total_missing} "
          f"({total_missing / total_pdf_words:.1%} of PDF content)")
    print()

    if not missing:
        print("✔ No missing content detected.")
        return True

    print("Missing words and where they occur in the PDF:")
    seen_shown = Counter()
    for word, count in missing.most_common():
        shown = 0
        for w, page_num, snippet in word_locations:
            if w == word and seen_shown[word] < count:
                print(f"  ✘ '{word}' (page {page_num}): ...{snippet}...")
                seen_shown[word] += 1
                shown += 1
            if shown >= count:
                break

    # simple pass/fail threshold - tune as needed
    ok = (total_missing / total_pdf_words) < 0.02
    print()
    print("✔ PASS - negligible/no content loss" if ok else "⚠ WARNING - review missing content above")
    return ok


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python verify_pdf_md.py <file.pdf> <file.md>")
        sys.exit(1)

    verify(sys.argv[1], sys.argv[2])