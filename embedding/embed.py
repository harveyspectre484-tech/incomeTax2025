#!/usr/bin/env python3
"""Embed legal search documents and upsert them into Qdrant."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import torch
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "graph_data" / "search_documents.jsonl"
DEFAULT_GRAPH_NODES = Path(__file__).resolve().parent.parent / "graph_data" / "graph_nodes.jsonl"
QDRANT_POINT_NAMESPACE = uuid.UUID("2d123c8e-5d5d-4b4e-9d12-c438bf7a52c1")


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield line_number, record


def load_documents(path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    missing_text = 0

    for line_number, record in read_jsonl(path):
        canonical_id = record.get("id")
        if not isinstance(canonical_id, str) or not canonical_id.strip():
            raise ValueError(f"{path}:{line_number}: missing non-empty id")
        if canonical_id in seen_ids:
            raise ValueError(f"{path}:{line_number}: duplicate canonical id {canonical_id!r}")
        seen_ids.add(canonical_id)

        retrieval_text = record.get("retrieval_text")
        if not isinstance(retrieval_text, str) or not retrieval_text.strip():
            missing_text += 1
            continue
        if record.get("is_placeholder") is True:
            continue

        payload = dict(record)
        metadata_json = payload.pop("metadata_json", None)
        if isinstance(metadata_json, str):
            try:
                payload["source_properties"] = json.loads(metadata_json)
            except json.JSONDecodeError:
                payload["metadata_json"] = metadata_json
        documents.append(payload)

    if missing_text:
        print(f"Skipped {missing_text} records with empty retrieval_text.", file=sys.stderr)
    if not documents:
        raise ValueError(f"No embeddable documents found in {path}")
    return documents


def load_graph_ids(path: Path) -> set[str]:
    return {
        str(record["id"])
        for _, record in read_jsonl(path)
        if isinstance(record.get("id"), str) and record["id"].strip()
    }


def collection_vector_size(client: QdrantClient, name: str) -> int | None:
    info = client.get_collection(name)
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        sizes = {config.size for config in vectors.values()}
        return next(iter(sizes)) if len(sizes) == 1 else None
    return getattr(vectors, "size", None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create embeddings from search_documents.jsonl and store them in Qdrant."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Search documents JSONL")
    parser.add_argument("--graph-nodes", type=Path, default=DEFAULT_GRAPH_NODES)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--api-key", help="Optional Qdrant API key")
    parser.add_argument("--collection", default="income_tax_2025_units")
    parser.add_argument(
        "--recreate-collection",
        action="store_true",
        help="Delete and recreate the named collection before embedding to remove stale points.",
    )
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-4B")
    parser.add_argument("--batch-size", type=int, default=2, help="Embedding batch size")
    parser.add_argument("--upsert-batch-size", type=int, default=64)
    args = parser.parse_args()

    if args.batch_size < 1 or args.upsert_batch_size < 1:
        parser.error("batch sizes must be positive")
    if not args.input.is_file():
        parser.error(f"search document file not found: {args.input}")

    documents = load_documents(args.input)
    if args.graph_nodes.is_file():
        graph_ids = load_graph_ids(args.graph_nodes)
        missing = sorted({doc["id"] for doc in documents} - graph_ids)
        if missing:
            sample = ", ".join(missing[:10])
            raise ValueError(
                f"{len(missing)} search document IDs are absent from graph_nodes.jsonl; "
                f"examples: {sample}"
            )
    else:
        print(f"Warning: graph node file not found; skipping ID cross-check: {args.graph_nodes}")

    print(f"Loading embedding model {args.model}...")
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    dtype = torch.float16 if device in {"mps", "cuda"} else torch.float32
    print(f"Using device={device}, dtype={dtype}.")
    model = SentenceTransformer(
        args.model,
        device=device,
        model_kwargs={"torch_dtype": dtype},
        processor_kwargs={"padding_side": "left"},
    )
    # model.max_seq_length = args.max_seq_length
    vector_size = model.get_embedding_dimension()
    if vector_size is None:
        raise ValueError("The embedding model did not report its output dimension")

    client = QdrantClient(url=args.qdrant_url, api_key=args.api_key)
    collection_exists = client.collection_exists(args.collection)
    if collection_exists and args.recreate_collection:
        client.delete_collection(args.collection)
        collection_exists = False
        print(f"Deleted existing collection {args.collection!r}.")

    if not collection_exists:
        client.create_collection(
            collection_name=args.collection,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )
        print(f"Created collection {args.collection!r} with vector size {vector_size}.")
    else:
        existing_size = collection_vector_size(client, args.collection)
        if existing_size != vector_size:
            raise ValueError(
                f"Collection {args.collection!r} has vector size {existing_size}; "
                f"model outputs {vector_size}. Choose a new collection or matching model dimensions."
            )

    total = len(documents)
    for start in range(0, total, args.upsert_batch_size):
        batch = documents[start : start + args.upsert_batch_size]
        texts = [doc["retrieval_text"] for doc in batch]
        vectors = model.encode(
            texts,
            batch_size=args.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(QDRANT_POINT_NAMESPACE, doc["id"])),
                vector=vector.tolist(),
                payload=doc,
            )
            for doc, vector in zip(batch, vectors, strict=True)
        ]
        client.upsert(collection_name=args.collection, points=points, wait=True)
        print(f"Stored {min(start + len(batch), total):,}/{total:,} documents")

    collection_info = client.get_collection(args.collection)
    point_count = collection_info.points_count
    if args.recreate_collection and point_count != total:
        raise ValueError(
            f"Clean collection contains {point_count:,} points after embedding; expected {total:,}."
        )

    print(
        f"Done: processed {total:,} vectors; collection {args.collection!r} "
        f"contains {point_count:,} points. "
        "Canonical IDs are in each point's payload under 'id'."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
