import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


def read_json(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def normalized(value):
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def find_raw_tables(node, owner_id=None, found=None):
    """Find nested raw tables, retaining their nearest legal-unit owner."""
    if found is None:
        found = []

    if isinstance(node, dict):
        is_table = isinstance(node.get("rows"), list) and (
            isinstance(node.get("columns"), list)
            or node.get("type") == "table"
            or "table_id" in node
        )

        current_id = node.get("id")
        if is_table:
            found.append({
                "owner_id": owner_id,
                "table_id": node.get("id") or node.get("table_id"),
                "columns": node.get("columns", []),
                "rows": node["rows"],
            })

        child_owner = current_id or owner_id
        for value in node.values():
            find_raw_tables(value, child_owner, found)

    elif isinstance(node, list):
        for value in node:
            find_raw_tables(value, owner_id, found)

    return found


def row_label(row, position):
    if isinstance(row, dict):
        for key in ("sl_no", "item_no", "row_number", "number"):
            if row.get(key) is not None:
                return str(row[key])
    return str(position)


def raw_cell_values(row):
    """Collect textual cell content, including nested item text."""
    values = []

    def visit(value, key=None):
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key not in {
                    "id", "type", "marker", "letter", "number",
                    "sl_no", "item_no", "row_number", "page_start",
                    "page_end", "line_start", "x", "y_start",
                }:
                    visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())

    visit(row)
    return values


def add_issue(issues, source, table_id, row_label_value, row_id, issue, detail):
    issues.append({
        "source_file": str(source),
        "table_id": table_id or "",
        "row_number": row_label_value or "",
        "row_id": row_id or "",
        "issue": issue,
        "detail": detail,
    })


def audit_file(raw_path, canonical_path, issues):
    raw = read_json(raw_path)
    canonical = read_json(canonical_path)

    units = canonical.get("legal_units", [])
    by_id = {}
    for unit in units:
        unit_id = unit.get("id")
        if not unit_id:
            add_issue(issues, canonical_path, "", "", "", "missing_unit_id",
                      "A canonical legal unit has no ID.")
            continue
        if unit_id in by_id:
            add_issue(issues, canonical_path, "", "", unit_id, "duplicate_unit_id",
                      "Canonical ID occurs more than once.")
        by_id[unit_id] = unit

    canonical_tables = [u for u in units if u.get("unit_type") == "table"]
    rows_by_table = defaultdict(list)
    for unit in units:
        if unit.get("unit_type") == "table_row":
            rows_by_table[unit.get("parent_id")].append(unit)

    raw_tables = find_raw_tables(raw)

    # Match tables by source ID when available; otherwise by owner and order.
    canonical_by_parent = defaultdict(list)
    for table in canonical_tables:
        canonical_by_parent[table.get("parent_id")].append(table)

    used_canonical_tables = set()
    parent_positions = defaultdict(int)

    for raw_table in raw_tables:
        owner_id = raw_table["owner_id"]
        raw_table_id = raw_table["table_id"]

        table = by_id.get(raw_table_id) if raw_table_id else None
        if table is None:
            candidates = canonical_by_parent.get(owner_id, [])
            position = parent_positions[owner_id]
            parent_positions[owner_id] += 1
            if position < len(candidates):
                table = candidates[position]

        if table is None:
            add_issue(issues, canonical_path, raw_table_id or owner_id, "", "",
                      "missing_canonical_table",
                      f"Raw table under owner {owner_id!r} has no matching canonical table.")
            continue

        table_id = table["id"]
        used_canonical_tables.add(table_id)
        canonical_rows = rows_by_table.get(table_id, [])
        raw_rows = raw_table["rows"]

        if len(raw_rows) != len(canonical_rows):
            add_issue(
                issues, canonical_path, table_id, "", "",
                "row_count_mismatch",
                f"Raw table has {len(raw_rows)} rows; canonical table has "
                f"{len(canonical_rows)} table_row units.",
            )

        rows_by_number = {
            str(row.get("row_number")): row
            for row in canonical_rows
            if row.get("row_number") is not None
        }

        headers = [str(h) for h in raw_table["columns"] if str(h).strip()]

        for position, raw_row in enumerate(raw_rows, start=1):
            label = row_label(raw_row, position)
            canonical_row = rows_by_number.get(label)

            # Some canonicalizers put the row number in the citation instead.
            if canonical_row is None:
                matches = [
                    row for row in canonical_rows
                    if label in str(row.get("citation", ""))
                ]
                if len(matches) == 1:
                    canonical_row = matches[0]

            if canonical_row is None:
                add_issue(issues, canonical_path, table_id, label, "",
                          "missing_canonical_row",
                          "No table_row unit matched this raw row number.")
                continue

            row_id = canonical_row.get("id")
            if canonical_row.get("parent_id") != table_id:
                add_issue(issues, canonical_path, table_id, label, row_id,
                          "wrong_row_parent",
                          f"Row parent is {canonical_row.get('parent_id')!r}, expected {table_id!r}.")

            row_text = normalized(canonical_row.get("text", ""))
            retrieval_text = normalized(canonical_row.get("retrieval_text", ""))

            if not row_text:
                add_issue(issues, canonical_path, table_id, label, row_id,
                          "empty_row_text", "Canonical table row has no text.")

            for header in headers:
                if normalized(header) not in retrieval_text:
                    add_issue(issues, canonical_path, table_id, label, row_id,
                              "header_missing_from_retrieval_text",
                              f"Column header {header!r} is absent from retrieval_text.")

            for cell_value in raw_cell_values(raw_row):
                value = normalized(cell_value)
                # Ignore very short fragments that are likely markers or punctuation.
                if len(value) >= 4 and value not in row_text:
                    add_issue(issues, canonical_path, table_id, label, row_id,
                              "cell_value_missing_from_row_text",
                              f"Raw cell value is not found in canonical row text: {cell_value[:180]!r}")

    # Catch table_row units that do not point to an actual canonical table.
    for table_id, rows in rows_by_table.items():
        if table_id not in by_id or by_id[table_id].get("unit_type") != "table":
            for row in rows:
                add_issue(issues, canonical_path, table_id, row.get("row_number"),
                          row.get("id"), "invalid_table_parent",
                          "table_row parent_id does not identify a canonical table.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument(
        "--canonical-dir", required=True, action="append", type=Path,
        help="Repeat for section and Schedule canonical directories.",
    )
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    issues = []
    canonical_files = []
    for directory in args.canonical_dir:
        canonical_files.extend(sorted(directory.glob("*_can.json")))

    if not canonical_files:
        raise SystemExit("No *_can.json files found in the canonical directories.")

    for canonical_path in canonical_files:
        raw_name = canonical_path.name.replace("_can.json", ".json")
        raw_path = args.raw_dir / raw_name
        if not raw_path.exists():
            add_issue(issues, canonical_path, "", "", "", "missing_raw_file",
                      f"Cannot compare; expected raw file {raw_path}.")
            continue
        audit_file(raw_path, canonical_path, issues)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    fields = ["source_file", "table_id", "row_number", "row_id", "issue", "detail"]
    with args.report.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(issues)

    print(f"Checked {len(canonical_files)} canonical files.")
    print(f"Found {len(issues)} issues. Report: {args.report}")


if __name__ == "__main__":
    main()