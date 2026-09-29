"""
Find marker lines in raw income-tax txt files and write the file names to a CSV.

A line matches ONLY if the text after the "[x= ..] [y= ..] [font= ..] [bold= ..]"
prefix is exactly one of the marker phrases (nothing else on that line).

Examples of lines that match:
    [x=  81.03] [y= 603.59] [font= 8.5] [bold=False] but does not include—
    [x=  51.96] [y= 747.86] [font= 8.5] [bold=False] where,—

Usage:
    python find_markers.py <folder_or_file> [more paths ...] -o markers.csv
    python find_markers.py ./txt_files -o markers.csv
    python find_markers.py ./txt_files -o markers.csv --files-only   # one row per file
    python find_markers.py ./txt_files -o markers.csv --markers "where,—" "except"
"""
import argparse
import csv
import re
import sys
from pathlib import Path

# Edit this list if you want different markers ("expect" in your message is
# assumed to mean "except"; both are included so nothing is missed).
DEFAULT_MARKERS = [
    "where,—",
    "but does not include—",
    "exclude",
    "except"
]

LINE_RE = re.compile(
    r"^\[x=\s*(?P<x>[\d.]+)\]\s*\[y=\s*(?P<y>[\d.]+)\]\s*"
    r"\[font=\s*(?P<font>[\d.]+)\]\s*\[bold=(?P<bold>True|False)\]\s?(?P<text>.*)$"
)
PAGE_RE = re.compile(r"^PAGE\s+(\d+)\s*$")
DASHES = str.maketrans({"–": "—", "―": "—", "‒": "—", "−": "—"})


def normalize(s: str) -> str:
    """Casefold, unify dash characters, collapse spaces."""
    s = s.translate(DASHES)
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def scan_file(path: Path, markers: set):
    hits = []
    page = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\r\n")
            pm = PAGE_RE.match(line.strip())
            if pm:
                page = int(pm.group(1))
                continue
            m = LINE_RE.match(line)
            if not m:
                continue
            text = m.group("text")
            if normalize(text) in markers:
                hits.append(
                    {
                        "page": page,
                        "line_number": lineno,
                        "x": m.group("x"),
                        "y": m.group("y"),
                        "matched_text": text.strip(),
                    }
                )
    return hits


def collect_files(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.txt")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"warning: {p} not found", file=sys.stderr)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="txt file(s) or folder(s) (searched recursively)")
    ap.add_argument("-o", "--output", default="markers.csv", help="output CSV path")
    ap.add_argument("--markers", nargs="+", default=DEFAULT_MARKERS, help="exact line texts to look for")
    ap.add_argument("--files-only", action="store_true",
                    help="write one row per file (file name + which markers were found + count)")
    args = ap.parse_args()

    markers = {normalize(m) for m in args.markers}
    files = collect_files(args.paths)
    if not files:
        sys.exit("no .txt files found")

    rows_detail, rows_files = [], []
    for f in files:
        hits = scan_file(f, markers)
        for h in hits:
            rows_detail.append({"file_name": f.name, "file_path": str(f), **h})
        if hits:
            found = sorted({h["matched_text"] for h in hits})
            rows_files.append({"file_name": f.name, "file_path": str(f),
                               "match_count": len(hits), "markers_found": " | ".join(found)})

    with open(args.output, "w", newline="", encoding="utf-8-sig") as out:
        if args.files_only:
            w = csv.DictWriter(out, fieldnames=["file_name", "file_path", "match_count", "markers_found"])
            w.writeheader(); w.writerows(rows_files)
        else:
            w = csv.DictWriter(out, fieldnames=["file_name", "file_path", "page", "line_number", "x", "y", "matched_text"])
            w.writeheader(); w.writerows(rows_detail)

    print(f"scanned {len(files)} file(s); {len(rows_files)} contain markers; "
          f"{len(rows_detail)} matching line(s) -> {args.output}")


if __name__ == "__main__":
    main()