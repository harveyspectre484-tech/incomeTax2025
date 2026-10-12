

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
import sys
import torch
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from graph_data.build_hybrid_index import search_bm25

DEFAULT_DB = ROOT / "graph_data" / "legal_act_bm25.sqlite"
DEFAULT_COLLECTION = "income_tax_2025_units_v3"
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-4B"

QUERY_PROMPT = (
    "Instruct: Given a legal question, retrieve relevant provisions "
    "from the Income-tax Act, 2025 that answer the question.\n"
    "Query: "
)
# Public filter name -> field shared by SQLite and Qdrant payloads.
FILTER_FIELDS = {
    "chapter": "chapter_number",
    "section": "section_number",
    "unit_type": "unit_type",
    "jurisdiction": "jurisdiction",
}
CITATION_RE = re.compile(
    r"\bsection\s+(?P<section>\d+[A-Za-z]?)"
    r"(?P<locators>(?:\s*\([A-Za-z0-9]+\))*)",
    re.IGNORECASE,
)


def normalize_citation(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()
"""Initialize once
This opens the existing database, loads documents by canonical ID, connects to Qdrant, and loads the model. 
Construct this class once for the life of your retrieval process."""

class HybridRetriever:
    def __init__(self,db_path = DEFAULT_DB, qdrant_url = "http://localhost:6333",
    collection : str = DEFAULT_COLLECTION,model_name: str = DEFAULT_MODEL) -> None:
        self.db_path = db_path
        self.collection = collection

        if not db_path.is_file():
            raise FileNotFoundError(f"BM25 database not found: {db_path}")

        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row

# look for this row it will make slow
        rows = self.db.execute("SELECT * FROM search_documents").fetchall()
        self.documents = {row["id"]: dict(row) for row in rows}

# The count check catches an incomplete collection. Matching counts alone do not prove every ID matches, so the vector search below also checks each returned ID."""
        if len(self.documents) != len(rows):
            raise ValueError("SQLite contains duplicate canonical IDs")

        self.qdrant = QdrantClient(url=qdrant_url)
        info = self.qdrant.get_collection(collection)
        if info.points_count != len(self.documents):
            raise ValueError(
                f"Index counts differ: SQLite has {len(self.documents)} documents, "
                f"Qdrant {collection!r} has {info.points_count} points"
            )

        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

        dtype = torch.float16 if device in {"mps", "cuda"} else torch.float32
        self.model = SentenceTransformer(
            model_name,
            device=device,
            model_kwargs={"torch_dtype": dtype},
            processor_kwargs={"padding_side": "left"},
        )

    def close(self) -> None:
        self.db.close()

  #3. Search BM25 --> Ranks start at 1. bm25_rank is the existing SQLite score; it is kept for inspection only."""

    def bm25_search(self,question: str,limit: int = 30,
        filters: dict[str, str] | None = None,) -> list[dict[str, Any]]:
        filters = filters or {}
        if not filters:
            rows = search_bm25(self.db_path, question, limit)
        else:
            terms = re.findall(r"[A-Za-z0-9]+", question)
            if not terms:
                return []
            fts_query = " OR ".join(f'"{term}"' for term in terms)

            conditions = ["search_fts MATCH ?"]
            parameters: list[Any] = [fts_query]

            for filter_name, value in filters.items():
                column = FILTER_FIELDS[filter_name]
                conditions.append(f"d.{column} = ?")
                parameters.append(value)

            parameters.append(limit)

            sql = f"""
                SELECT d.*,
                       bm25(
                           search_fts,
                           0.0, 8.0, 10.0, 10.0,
                           6.0, 6.0, 1.0, 1.0
                       ) AS bm25_rank
                FROM search_fts
                JOIN search_documents AS d ON d.id = search_fts.id
                WHERE {" AND ".join(conditions)}
                ORDER BY bm25_rank
                LIMIT ?
            """
            rows = [
                dict(row)
                for row in self.db.execute(sql, parameters).fetchall()
            
            ]
        results = []
        seen: set[str] = set()

        for rank, row in enumerate(rows, start=1):
            canonical_id = row["id"]
            if canonical_id in seen:
                raise ValueError(f"Duplicate BM25 result ID: {canonical_id}")
            if canonical_id not in self.documents:
                raise ValueError(f"BM25 ID missing from documents: {canonical_id}")

            seen.add(canonical_id)
            results.append({
                "id": canonical_id,
                "rank": rank,
                "score": float(row["bm25_rank"]),
            })
        return results

#4. Embed the question and search Qdrant --> The prompt is applied only to the query. Documents in your existing collection were embedded without that prompt.

    def vector_search(self,question: str,limit: int = 30,
        filters: dict[str, str] | None = None,) -> list[dict[str, Any]]:
        vector = self.model.encode(
            question,
            prompt=QUERY_PROMPT,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

        conditions = [
            models.FieldCondition(
                key=FILTER_FIELDS[name],
                match=models.MatchValue(value=value),
            )
            for name, value in (filters or {}).items()
        ]
        query_filter = models.Filter(must=conditions) if conditions else None

        response = self.qdrant.query_points(
            collection_name=self.collection,
            query=vector.tolist(),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        results = []
        seen: set[str] = set()

        for rank, point in enumerate(response.points, start=1):
            payload = point.payload or {}
            canonical_id = payload.get("id")

            if not isinstance(canonical_id, str) or not canonical_id.strip():
                raise ValueError(f"Qdrant point {point.id} has no canonical payload ID")
            if canonical_id not in self.documents:
                raise ValueError(f"Qdrant ID {canonical_id!r} is absent from SQLite")
            if canonical_id in seen:
                raise ValueError(f"Duplicate canonical ID in Qdrant results: {canonical_id}")

            seen.add(canonical_id)
            results.append({
                "id": canonical_id,
                "rank": rank,
                "score": float(point.score),
            })

        return results

## 5. Combine the ranks with RRF --> Each method contributes 1 / (k + rank) when it found the document.

    @staticmethod
    def fuse(bm25_results: list[dict[str, Any]],
        vector_results: list[dict[str, Any]],
        limit: int = 30,
        k: int = 60,) -> list[dict[str, Any]]:
        combined: dict[str, dict[str, Any]] = {}

        for method, results in (
            ("bm25", bm25_results),
            ("vector", vector_results)):
            for result in results:
                canonical_id = result["id"]
                entry = combined.setdefault(
                    canonical_id,
                    {
                        "id": canonical_id,
                        "rrf_score": 0.0,
                        "bm25_rank": None,
                        "vector_rank": None,
                        "bm25_score": None,
                        "vector_score": None,
                        "matched_by": [],
                    },
                )

                entry["rrf_score"] += 1.0 / (k + result["rank"])
                entry[f"{method}_rank"] = result["rank"]
                entry[f"{method}_score"] = result["score"]
                entry["matched_by"].append(method)

        def sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
            both = len(entry["matched_by"]) == 2
            best_rank = min(
                rank
                for rank in (entry["bm25_rank"], entry["vector_rank"])
                if rank is not None
            )
            return (
                -entry["rrf_score"],
                -int(both),
                best_rank,
                entry["id"],
            )

        return sorted(combined.values(), key=sort_key)[:limit]

# citastion lookup
    def find_exact_citation_ids(
        self,
        question: str,
        filters: dict[str, str],
    ) -> list[str]:
        found: list[str] = []

        for match in CITATION_RE.finditer(question):
            # Avoid treating a named external Act as this Income-tax Act.
            following = question[match.end():]
            named_act = re.match(
                r"\s+of\s+(?:the\s+)?([^.;?,]+?\bAct\b)",
                following,
                re.IGNORECASE,
            )
            if named_act and not re.search(
                r"\bIncome[\s-]*tax\s+Act\b",
                named_act.group(1),
                re.IGNORECASE,
            ):
                continue

            section = match.group("section")
            locators = re.findall(
                r"\(([A-Za-z0-9]+)\)",
                match.group("locators"),
            )
            candidate_id = "-".join([section, *locators])
            requested_citation = normalize_citation(match.group(0))

            document = self.documents.get(candidate_id)

            # Some canonical IDs cannot be derived directly from the
            # printed citation. Try an exact stored-citation match.
            if document is None or normalize_citation(
                document["citation"]
            ) != requested_citation:
                matching_documents = [
                    doc
                    for doc in self.documents.values()
                    if normalize_citation(doc["citation"])
                    == requested_citation
                ]
                if len(matching_documents) != 1:
                    continue
                document = matching_documents[0]

            if any(
                str(document.get(FILTER_FIELDS[name]) or "") != value
                for name, value in filters.items()
            ):
                continue

            if document["id"] not in found:
                found.append(document["id"])

        return found

# 6. Attach exact text and run the full search --> The returned records contain exact text from SQLite. retrieval_text is also included so you can inspect what was searched.

    def retrieve(self,question: str,*,bm25_limit: int = 30,
        
        vector_limit: int = 30,
        final_limit: int = 30,
        rrf_k: int = 60,
        filters: dict[str, str] | None = None,
        ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("Question cannot be empty")

        filters = filters or {}
        unknown = set(filters) - set(FILTER_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported filters: {sorted(unknown)}")
         
        
        for name, value in filters.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Filter {name!r} must be nonempty text")

        exact_citation_ids = self.find_exact_citation_ids(
            question, filters
        )


        bm25_results = self.bm25_search(
            question, bm25_limit, filters
        )
        vector_results = self.vector_search(
            question, vector_limit, filters
        )
        # fused = self.fuse(
        #     bm25_results, vector_results, final_limit, rrf_k
        # )
        # Keep all fused results temporarily so an exact citation can
        # retain its BM25/vector ranks even if RRF placed it below 30.
        fused = self.fuse(
            bm25_results,
            vector_results,
            bm25_limit + vector_limit,
            rrf_k,
        )
        fused_by_id = {item["id"]: item for item in fused}

        ordered = []
        for canonical_id in exact_citation_ids:
            result = dict(fused_by_id.get(canonical_id) or {
                "id": canonical_id,
                "rrf_score": 0.0,
                "bm25_rank": None,
                "vector_rank": None,
                "bm25_score": None,
                "vector_score": None,
                "matched_by": [],
            })
            result["matched_by"] = [
                "citation", *result["matched_by"]
            ]
            result["exact_citation_match"] = True
            ordered.append(result)

        pinned_ids = set(exact_citation_ids)
        for result in fused:
            if result["id"] not in pinned_ids:
                ordered.append({
                    **result,
                    "exact_citation_match": False,
                })

        fused = ordered[:max(final_limit, len(exact_citation_ids))]

        candidates = []
        for result in fused:
            document = self.documents[result["id"]]
            candidates.append({                                   #if we can use collections.deque here? to make it more efficient
                "id": result["id"],
                "citation": document["citation"],
                "unit_type": document["unit_type"],
                "text": document["text"],
                "retrieval_text": document["retrieval_text"],
                "parent_id": document["parent_id"],
                "section_number": document["section_number"],
                "schedule_number": document["schedule_number"],
                **{key: value for key, value in result.items() if key != "id"},
            })

        bm25_ids = {item["id"] for item in bm25_results}
        vector_ids = {item["id"] for item in vector_results}

        return {
            "question": question,
            "filters": filters,
            "bm25_results": bm25_results,
            "vector_results": vector_results,
            "candidates": candidates,
            "overlap_count": len(bm25_ids & vector_ids),
            "bm25_only_ids": sorted(bm25_ids - vector_ids),
            "vector_only_ids": sorted(vector_ids - bm25_ids),
            "exact_citation_ids": exact_citation_ids,
        }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--chapter")
    parser.add_argument("--section")
    parser.add_argument("--unit-type")
    parser.add_argument("--jurisdiction")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    args = parser.parse_args()

    filters = {
        name: value
        for name, value in {
            "chapter": args.chapter,
            "section": args.section,
            "unit_type": args.unit_type,
            "jurisdiction": args.jurisdiction,
        }.items()
        if value is not None
    }

    retriever = HybridRetriever(
        db_path=args.db,
        qdrant_url=args.qdrant_url,
        collection=args.collection,
    )

    try:
        if args.test:
            questions = [
                "Section 36(3)(a)(i)",
                "deduction for bad and doubtful debts",
                "How much can a bank deduct for loans that may not be recovered?",
            ]
        elif args.question:
            questions = [args.question]
        else:
            questions = None

        if questions is not None:
            for question in questions:
                result = retriever.retrieve(question, filters=filters)
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Enter a question. Type 'quit' to stop.")
            while True:
                try:
                    question = input("> ").strip()
                except EOFError:
                    break
                if question.lower() in {"quit", "exit"}:
                    break
                if question:
                    result = retriever.retrieve(
                        question, filters=filters
                    )
                    print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        retriever.close()


if __name__ == "__main__":
    main()
        