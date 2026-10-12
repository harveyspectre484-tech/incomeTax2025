"""Select bounded, deduplicated statutory evidence from ranked bundles."""

from __future__ import annotations

from typing import Any

try:
    from .expand_rerank import LegalGraph, terms
except ImportError:
    from expand_rerank import LegalGraph, terms


def exact_passage(node: dict[str, Any]) -> str:
    citation = str(node.get("citation") or node["id"])
    body = str(node.get("text") or "").strip()
    if node.get("node_type") == "table":
        columns = (node.get("source_properties") or {}).get("columns") or []
        if columns:
            body = "Table columns: " + "; ".join(map(str, columns))
    if body:
        return f"{citation}: {body}"
    if node.get("node_type") in {"section", "schedule"} and node.get("title"):
        return f"{citation}: {node['title']}"
    return ""


def inclusion_reason(
    graph: LegalGraph, bundle: dict[str, Any], node_id: str,
) -> str | None:
    anchor_id = bundle["anchor_id"]
    if node_id == anchor_id or node_id in bundle.get("retrieved_ids", []):
        return "direct_retrieval"
    if node_id in {item["id"] for item in bundle.get("discovered_candidates", [])}:
        return "reviewable_dependency_candidate"
    if node_id in {node["id"] for node in graph.ancestry(anchor_id)}:
        return "table_header_context" if graph.nodes[node_id].get("node_type") == "table" else "governing_parent"
    for link in bundle.get("links", []):
        if not link.get("evidence_included", True):
            continue
        target_id = link["to_id"] if link["relation"] == "outgoing" else link["from_id"]
        if node_id == target_id or node_id in {node["id"] for node in graph.ancestry(target_id)}:
            return {
                "outgoing": "outgoing_reference",
                "incoming_candidate": "incoming_reference_candidate",
                "annotation": "annotation",
            }.get(link["relation"])
    parent_id = graph.nodes[node_id].get("parent_id")
    if parent_id == anchor_id and graph.nodes[node_id].get("node_type") == "table":
        return "table_header_context"
    if graph.nodes[node_id].get("node_type") == "table_row":
        table_id = parent_id
        if table_id == anchor_id or graph.nodes.get(table_id or "", {}).get("parent_id") == anchor_id:
            return "table_row_context"
    return None


def gate_ranked_bundles(
    ranked_bundles: list[dict[str, Any]], *, max_bundles: int,
    max_score_drop: float | None = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if max_score_drop is not None and max_score_drop < 0:
        raise ValueError("max_score_drop must be nonnegative")
    if not ranked_bundles:
        return [], []
    scores = [float(item["rerank_score"]) for item in ranked_bundles if item.get("rerank_score") is not None]
    threshold = max(scores) - max_score_drop if scores and max_score_drop is not None else None
    selected: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    for bundle in ranked_bundles:
        score = bundle.get("rerank_score")
        if threshold is not None and score is not None and score < threshold and not bundle.get("exact_citation_match"):
            reason = "below_rerank_threshold"
        elif len(selected) >= max_bundles:
            reason = "bundle_limit"
        else:
            selected.append(bundle)
            continue
        omitted.append({"anchor_id": bundle["anchor_id"], "reason": reason, "rerank_score": score})
    return selected, omitted


def select_final_evidence(
    graph: LegalGraph, ranked_bundles: list[dict[str, Any]],
    max_bundles: int = 10, max_chars: int = 16000, question: str = "",
    version: str = "2025",
) -> dict[str, Any]:
    if max_bundles < 1 or max_chars < 1:
        raise ValueError("Evidence limits must be positive")
    selected: dict[str, dict[str, Any]] = {}
    omitted: list[dict[str, str]] = []
    omitted_ids: set[str] = set()
    reranker_truncated_ids: set[str] = set()
    used_chars = 0
    rows_by_table: dict[str, list[str]] = {}
    for bundle in ranked_bundles[:max_bundles]:
        for node_id in bundle["source_ids"]:
            node = graph.nodes[node_id]
            if node.get("node_type") == "table_row" and node.get("parent_id"):
                rows_by_table.setdefault(node["parent_id"], [])
                if node_id not in rows_by_table[node["parent_id"]]:
                    rows_by_table[node["parent_id"]].append(node_id)

    preferred_rows: dict[str, str] = {}
    question_terms = terms(question)
    for table_id, row_ids in rows_by_table.items():
        if len(row_ids) < 2:
            continue
        exact_rows = [
            bundle["anchor_id"] for bundle in ranked_bundles[:max_bundles]
            if bundle.get("exact_citation_match") and bundle["anchor_id"] in row_ids
        ]
        if len(exact_rows) == 1:
            preferred_rows[table_id] = exact_rows[0]
            continue
        scores = sorted(
            ((len(question_terms & terms(str(graph.nodes[row_id].get("text") or ""))), row_id)
             for row_id in row_ids), reverse=True,
        )
        if scores[0][0] >= 2 and scores[0][0] > scores[1][0]:
            preferred_rows[table_id] = scores[0][1]

    for bundle in ranked_bundles[:max_bundles]:
        anchor_id = bundle["anchor_id"]
        anchor = graph.nodes[anchor_id]
        if (anchor.get("node_type") == "table_row" and
                anchor.get("parent_id") in preferred_rows and
                preferred_rows[anchor["parent_id"]] != anchor_id):
            continue
        reranker_truncated_ids.update(bundle.get("truncated_source_ids") or [])
        for node_id in bundle["source_ids"]:
            node = graph.nodes[node_id]
            if (node.get("node_type") == "table_row" and
                    node.get("parent_id") in preferred_rows and
                    preferred_rows[node["parent_id"]] != node_id):
                if node_id not in omitted_ids:
                    omitted.append({"id": node_id, "reason": "nonmatching_table_alternative"})
                    omitted_ids.add(node_id)
                continue
            if node_id in selected:
                if anchor_id not in selected[node_id]["supporting_anchors"]:
                    selected[node_id]["supporting_anchors"].append(anchor_id)
                continue
            reason = inclusion_reason(graph, bundle, node_id)
            if reason is None:
                if node_id not in omitted_ids:
                    omitted.append({"id": node_id, "reason": "unexplained_inclusion"})
                    omitted_ids.add(node_id)
                continue
            passage = exact_passage(node)
            if not passage:
                continue
            needed = len(passage) + (1 if selected else 0)
            if used_chars + needed > max_chars:
                if node_id not in omitted_ids:
                    omitted.append({"id": node_id, "reason": "context_budget"})
                    omitted_ids.add(node_id)
                continue
            selected[node_id] = {
                "id": node_id,
                "citation": node.get("citation"),
                "unit_type": node.get("node_type"),
                "text": node.get("text") or "",
                "display_text": passage,
                "parent_id": node.get("parent_id"),
                "inclusion_reason": reason,
                "relationship_to_query": reason,
                "origin_id": anchor_id,
                "act": node.get("act"),
                "version": version,
                "jurisdiction": node.get("jurisdiction"),
                "source_file": node.get("source_file"),
                "source_year": (node.get("source_properties") or {}).get("source_year"),
                "effective_from": (node.get("source_properties") or {}).get("effective_from"),
                "effective_to": (node.get("source_properties") or {}).get("effective_to"),
                "effective_date_verified": (node.get("source_properties") or {}).get("effective_date_verified", False),
                "table_columns": (node.get("source_properties") or {}).get("columns") if node.get("node_type") == "table" else None,
                "supporting_anchors": [anchor_id],
            }
            used_chars += needed

    text = "\n".join(unit["display_text"] for unit in selected.values())
    return {
        "evidence_units": list(selected.values()),
        "context_text": text,
        "used_chars": len(text),
        "max_chars": max_chars,
        "omitted_units": omitted,
        "reranker_truncated_ids": sorted(reranker_truncated_ids),
        "selected_bundle_count": min(max_bundles, len(ranked_bundles)),
    }
