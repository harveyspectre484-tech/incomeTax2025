from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS search_documents (
    id TEXT PRIMARY KEY,
    citation TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    title TEXT,
    text TEXT NOT NULL,
    retrieval_text TEXT NOT NULL,
    parent_id TEXT,
    section_id TEXT,
    section_number TEXT,
    schedule_id TEXT,
    schedule_number TEXT,
    schedule_title TEXT,
    chapter_id TEXT,
    chapter_number TEXT,
    chapter_title TEXT,
    division_id TEXT,
    division_number TEXT,
    subdivision_id TEXT,
    subdivision_number TEXT,
    act TEXT,
    jurisdiction TEXT,
    page_start INTEGER,
    page_end INTEGER,
    source_file TEXT,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_section ON search_documents(section_number);
CREATE INDEX IF NOT EXISTS idx_search_schedule_id ON search_documents(schedule_id);
CREATE INDEX IF NOT EXISTS idx_search_schedule_number ON search_documents(schedule_number);
CREATE INDEX IF NOT EXISTS idx_search_chapter ON search_documents(chapter_number);
CREATE INDEX IF NOT EXISTS idx_search_type ON search_documents(unit_type);
CREATE INDEX IF NOT EXISTS idx_search_parent ON search_documents(parent_id);
CREATE INDEX IF NOT EXISTS idx_search_act_jurisdiction ON search_documents(act, jurisdiction);
"""

INDEXABLE_TYPES = {
    "section", "subsection", "clause", "sub_clause", "sub_clauses", "sub_sub_clause",
    "sub_sub_sub_clause", "item", "sub_item", "condition", "operative_clause",
    "where_block", "exclusion_block", "explanation_block", "exception_block",
    "longline", "longline_clause",
    "table_row", "table_cell_item", "paragraph", "subparagraph", "note", "footnote",
    "formula", "variable", "substitution",
}
NON_SUBSTANTIVE_CELL_VALUES = {"", "***", "-", "—", "–"}
SEARCH_FIELDS = (
    "id", "citation", "section_number", "title", "text", "retrieval_text", "parent_id",
    "section_id", "schedule_id", "schedule_number", "schedule_title", "chapter_number",
    "chapter_title", "division_title", "unit_type", "act",
    "jurisdiction", "page_start", "page_end", "source_file", "metadata_json",
)


def load_nodes(path: Path) -> list[dict[str, Any]]:
    nodes = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            node = json.loads(line)
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                raise ValueError(f"Line {line_no} has no nonempty canonical ID")
            if node_id in seen:
                raise ValueError(f"Duplicate graph node ID: {node_id}")
            seen.add(node_id)
            nodes.append(node)
    return nodes


def append_unique(parts: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in parts:
        parts.append(text)


def table_columns(table: dict[str, Any] | None) -> list[str]:
    if not table:
        return []
    columns = (table.get("source_properties") or {}).get("columns", [])
    return [str(column).strip() for column in columns if str(column).strip()]


def table_heading(table: dict[str, Any] | None) -> str:
    if not table:
        return ""
    properties = table.get("source_properties") or {}
    for key in ("caption", "heading", "table_title"):
        value = str(properties.get(key) or "").strip()
        if value:
            return value
    return ""


def table_context(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    """Build enough context to understand a row or cell without its siblings."""
    context: list[str] = []
    unit_type = node.get("node_type")
    parent = by_id.get(node.get("parent_id"))
    table: dict[str, Any] | None = None
    owner: dict[str, Any] | None = None

    if unit_type == "table_row" and parent and parent.get("node_type") == "table":
        table = parent
        owner = by_id.get(table.get("parent_id"))
    elif unit_type == "table_cell_item" and parent:
        row = parent if parent.get("node_type") == "table_row" else None
        if row:
            append_unique(context, f"Parent row: {row.get('citation') or row['id']}")
            table = by_id.get(row.get("parent_id"))
            if not table or table.get("node_type") != "table":
                table = None
            owner = by_id.get(table.get("parent_id")) if table else None

    if owner:
        owner_display = str(owner.get("citation") or owner["id"])
        owner_title = str(owner.get("title") or "").strip()
        if owner_title:
            owner_display = f"{owner_display} {owner_title}"
        append_unique(context, f"Parent provision: {owner_display}")

    if table:
        append_unique(context, f"Table: {table.get('citation') or table['id']}")
        heading = table_heading(table)
        if heading:
            append_unique(context, f"Table heading: {heading}")
        columns = table_columns(table)
        if columns:
            append_unique(context, "Table columns: " + "; ".join(columns))

    if unit_type == "table_row":
        row_number = (node.get("source_properties") or {}).get("row_number")
        if row_number not in (None, ""):
            append_unique(context, f"Row number: {row_number}")
    elif unit_type == "table_cell_item":
        column_label = (node.get("source_properties") or {}).get("column_label")
        if column_label:
            append_unique(context, f"Table column: {column_label}")

    return context


def build_documents(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {node["id"]: node for node in nodes}
    docs: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()

    for node in nodes:
        unit_type = node.get("node_type") or ""
        text = str(node.get("text") or "").strip()
        if unit_type not in INDEXABLE_TYPES:
            skipped["structural_or_unsupported_type"] += 1
            continue
        if not text:
            skipped["empty_text"] += 1
            continue
        if unit_type == "table_cell_item" and text in NON_SUBSTANTIVE_CELL_VALUES:
            skipped["non_substantive_table_cell_item"] += 1
            continue

        citation = str(node.get("citation") or node["id"])
        title = str(node.get("title") or "").strip()
        source_properties = node.get("source_properties") or {}
        schedule_id = node.get("schedule_id") or source_properties.get("schedule_id")
        schedule_number = node.get("schedule_number") or source_properties.get("schedule_number")
        schedule_title = node.get("schedule_title") or source_properties.get("schedule_title")
        retrieval_parts: list[str] = []
        append_unique(retrieval_parts, citation)
        append_unique(retrieval_parts, schedule_title)
        append_unique(retrieval_parts, title)
        append_unique(retrieval_parts, node.get("chapter_title"))
        append_unique(retrieval_parts, node.get("division_title"))
        for part in table_context(node, by_id):
            append_unique(retrieval_parts, part)
        append_unique(retrieval_parts, text)
        retrieval_text = " ".join(retrieval_parts)
        metadata = {
            key: node.get(key)
            for key in (
                "chapter_id", "division_id", "subdivision_id", "subdivision_number",
                "subdivision_title", "section_id", "is_placeholder", "source_properties",
                "line_start", "x", "y_start",
            )
        }
        docs.append({
            "id": node["id"],
            "citation": citation,
            "unit_type": unit_type,
            "title": title,
            "text": text,
            "retrieval_text": retrieval_text,
            "parent_id": node.get("parent_id"),
            "section_id": node.get("section_id"),
            "section_number": node.get("section_number"),
            "schedule_id": schedule_id,
            "schedule_number": schedule_number,
            "schedule_title": schedule_title,
            "chapter_id": node.get("chapter_id"),
            "chapter_number": node.get("chapter_number", node.get("chapter")),
            "chapter_title": node.get("chapter_title"),
            "division_id": node.get("division_id"),
            "division_number": node.get("division_number"),
            "subdivision_id": node.get("subdivision_id"),
            "subdivision_number": node.get("subdivision_number"),
            "act": node.get("act"),
            "jurisdiction": node.get("jurisdiction"),
            "page_start": node.get("page_start"),
            "page_end": node.get("page_end"),
            "source_file": node.get("source_file"),
            "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        })
        type_counts[unit_type] += 1

    summary = {
        "input_nodes": len(nodes),
        "search_documents": len(docs),
        "indexed_by_unit_type": dict(sorted(type_counts.items())),
        "skipped_by_reason": dict(sorted(skipped.items())),
        "non_substantive_table_cell_values": sorted(NON_SUBSTANTIVE_CELL_VALUES),
        "canonical_ids_preserved": True,
        "external_placeholder_nodes_included": False,
    }
    return docs, summary


def write_documents(path: Path, docs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for doc in docs:
            handle.write(json.dumps(doc, ensure_ascii=False) + "\n")


def build_bm25(db_path: Path, docs: list[dict[str, Any]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("DROP TABLE IF EXISTS search_fts")
        conn.execute("DROP TABLE IF EXISTS search_documents")
        conn.executescript(SCHEMA)
        try:
            conn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(id UNINDEXED, citation, section_number, schedule_number, title, schedule_title, text, retrieval_text, tokenize='unicode61 remove_diacritics 2')")
        except sqlite3.OperationalError as exc:
            raise RuntimeError("This Python SQLite build does not include FTS5") from exc
        with conn:
            insert_sql = """INSERT INTO search_documents
                (id,citation,unit_type,title,text,retrieval_text,parent_id,section_id,section_number,
                 schedule_id,schedule_number,schedule_title,
                 chapter_id,chapter_number,chapter_title,division_id,division_number,subdivision_id,
                 subdivision_number,act,jurisdiction,page_start,page_end,source_file,metadata_json)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
            for doc in docs:
                values = tuple(doc.get(key) for key in (
                    "id", "citation", "unit_type", "title", "text", "retrieval_text", "parent_id",
                    "section_id", "section_number", "schedule_id", "schedule_number", "schedule_title",
                    "chapter_id", "chapter_number", "chapter_title",
                    "division_id", "division_number", "subdivision_id", "subdivision_number", "act",
                    "jurisdiction", "page_start", "page_end", "source_file", "metadata_json",
                ))
                conn.execute(insert_sql, values)
                conn.execute("INSERT INTO search_fts(id,citation,section_number,schedule_number,title,schedule_title,text,retrieval_text) VALUES (?,?,?,?,?,?,?,?)", (
                    doc["id"], doc["citation"], doc.get("section_number") or "",
                    doc.get("schedule_number") or "", doc.get("title") or "",
                    doc.get("schedule_title") or "", doc["text"], doc["retrieval_text"],
                ))
        conn.execute("INSERT INTO search_fts(search_fts) VALUES('optimize')")
        conn.commit()
    finally:
        conn.close()


def search_bm25(db_path: Path, query: str, limit: int = 30) -> list[dict[str, Any]]:
    terms = re.findall(r"[A-Za-z0-9]+", query)
    if not terms:
        return []
    fts_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT d.*, bm25(search_fts,0.0,8.0,10.0,10.0,6.0,6.0,1.0,1.0) AS bm25_rank
               FROM search_fts JOIN search_documents d ON d.id=search_fts.id
               WHERE search_fts MATCH ? ORDER BY bm25_rank LIMIT ?""",
            (fts_query, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build canonical-unit search documents and a weighted SQLite FTS5 BM25 index.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--nodes", required=True, help="Post-dedup graph_nodes.jsonl")
    build.add_argument("--documents", required=True, help="Output search-documents JSONL")
    build.add_argument("--db", required=True, help="Output SQLite FTS5 database")
    build.add_argument("--summary", required=True, help="Output build summary JSON")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--db", required=True)
    search.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    if args.command == "build":
        docs, summary = build_documents(load_nodes(Path(args.nodes)))
        write_documents(Path(args.documents), docs)
        build_bm25(Path(args.db), docs)
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Created {len(docs)} search documents and BM25 index at {args.db}")
    else:
        for rank, result in enumerate(search_bm25(Path(args.db), args.query, args.limit), 1):
            print(f"{rank}. {result['citation']} [{result['unit_type']}] id={result['id']} bm25={result['bm25_rank']:.4f}")
            print(f"   {result['text'][:500]}")


if __name__ == "__main__":
    main()
