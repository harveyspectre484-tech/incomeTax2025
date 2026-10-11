from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.2.0"
PROVENANCE_KEYS = {"source_file", "source_files", "source_mentions", "occurrence_count"}
NODE_SOURCE_FIELDS = {"page_start", "page_end", "line_start", "x", "y_start"}
EDGE_NONMATERIAL_KEYS = {"source_file", "source_files", "source_mentions", "occurrence_count"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalized(value: Any) -> Any:
    """Canonicalize empty formatting and unordered object keys, but not legal wording."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in sorted(value.items()) if normalized(item) is not None}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def identity_key(node: dict[str, Any]) -> str:
    identity = {
        key: value for key, value in node.items()
        if key not in PROVENANCE_KEYS | NODE_SOURCE_FIELDS | {"source_properties"}
    }
    identity["source_properties"] = {
        key: value for key, value in (node.get("source_properties") or {}).items()
        if key not in PROVENANCE_KEYS
    }
    return json.dumps(normalized(identity), sort_keys=True, ensure_ascii=False)


def provenance(record: dict[str, Any]) -> list[str]:
    values = record.get("source_files") or record.get("source_file") or []
    if isinstance(values, str):
        values = [values]
    return sorted({value for value in values if isinstance(value, str) and value.strip()})


def merge_provenance(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(records[0])
    files = sorted({source for record in records for source in provenance(record)})
    if files:
        merged["source_file"] = files[0]
    if len(records) > 1:
        merged["source_files"] = files
        merged["occurrence_count"] = sum(record.get("occurrence_count", 1) for record in records)
    source_properties = dict(merged.get("source_properties") or {})
    mentions = [mention for record in records for mention in (record.get("source_properties") or {}).get("source_mentions", [])]
    if mentions:
        unique = {json.dumps(normalized(mention), sort_keys=True, ensure_ascii=False): mention for mention in mentions}
        source_properties["source_mentions"] = [unique[key] for key in sorted(unique)]
        merged["source_properties"] = source_properties
    return merged


def deduplicate_nodes(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("Candidate node has no nonempty ID")
        by_id[node_id].append(node)

    accepted, conflicts, report = [], [], []
    for node_id, occurrences in by_id.items():
        if len({identity_key(node) for node in occurrences}) > 1:
            conflicts.extend(occurrences)
            report.append({"severity": "ERROR", "code": "conflicting_node_id", "id": node_id,
                           "count": str(len(occurrences)), "source_files": " | ".join(sorted({f for n in occurrences for f in provenance(n)}))})
        else:
            accepted.append(merge_provenance(occurrences))
            if len(occurrences) > 1:
                report.append({"severity": "INFO", "code": "merged_duplicate_node", "id": node_id,
                               "count": str(len(occurrences)), "source_files": " | ".join(provenance(accepted[-1]))})
    return accepted, conflicts, report


def edge_key(edge: dict[str, Any]) -> str:
    # A different range position, Schedule locator, target Act, or override note is material.
    material = {key: value for key, value in edge.items() if key not in EDGE_NONMATERIAL_KEYS | {"source_properties", "reference_text"}}
    text = edge.get("reference_text")
    material["reference_text"] = " ".join(text.split()).casefold() if isinstance(text, str) else None
    material["source_properties"] = {
        key: value for key, value in (edge.get("source_properties") or {}).items()
        if key not in EDGE_NONMATERIAL_KEYS
    }
    return json.dumps(normalized(material), sort_keys=True, ensure_ascii=False)


def deduplicate_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        groups[edge_key(edge)].append(edge)
    return [merge_provenance(group) for group in groups.values()]


def run(nodes: list[dict[str, Any]], resolved: list[dict[str, Any]], pending: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    accepted, conflicts, report = deduplicate_nodes(nodes)
    conflicted_ids = {node["id"] for node in conflicts}
    active_edges, moved = [], []
    for edge in resolved:
        if edge.get("from_id") in conflicted_ids or edge.get("to_id") in conflicted_ids:
            pending_edge = dict(edge)
            pending_edge["resolution_status"] = "ambiguous"
            moved.append(pending_edge)
        else:
            active_edges.append(edge)
    for edge in moved:
        report.append({"severity": "ERROR", "code": "edge_touches_conflicting_node", "id": edge.get("from_id", ""),
                       "count": "1", "source_files": " | ".join(provenance(edge))})
    merged_edges = deduplicate_edges(active_edges)
    merged_pending = deduplicate_edges([*pending, *moved])
    outputs = {"nodes": accepted, "edges": merged_edges, "pending": merged_pending, "conflicts": conflicts, "report": report}
    summary = {
        "schema_version": SCHEMA_VERSION,
        "stage": "4A_identity_and_duplicates",
        "input_node_occurrences": len(nodes),
        "active_node_count": len(accepted),
        "merged_node_occurrences": len(nodes) - len(accepted) - len(conflicts),
        "conflicting_node_ids": len(conflicted_ids),
        "input_resolved_edge_occurrences": len(resolved),
        "active_edge_count": len(merged_edges),
        "merged_resolved_edge_occurrences": len(active_edges) - len(merged_edges),
        "input_pending_edge_occurrences": len(pending),
        "pending_edge_count": len(merged_pending),
        "edges_moved_to_pending": len(moved),
        "blocking_conflicts": bool(conflicted_ids),
        "external_act_nodes_deferred": True,
    }
    return outputs, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply graph identity and duplicate rules without changing canonical IDs.")
    parser.add_argument("--candidate-nodes", required=True)
    parser.add_argument("--resolved-edges", required=True)
    parser.add_argument("--pending-edges", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    records, summary = run(read_jsonl(Path(args.candidate_nodes)), read_jsonl(Path(args.resolved_edges)), read_jsonl(Path(args.pending_edges)))
    for key, filename in (("nodes", "graph_nodes.jsonl"), ("edges", "graph_edges.jsonl"),
                          ("pending", "graph_pending_edges.jsonl"), ("conflicts", "graph_node_conflicts.jsonl")):
        write_jsonl(output / filename, records[key])
    (output / "graph_identity_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (output / "graph_identity_report.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["severity", "code", "id", "count", "source_files"])
        writer.writeheader()
        writer.writerows(records["report"])
    print(f"{summary['active_node_count']} nodes; {summary['active_edge_count']} active edges; "
          f"{summary['pending_edge_count']} pending edges; {summary['conflicting_node_ids']} conflicting IDs")
    if summary["blocking_conflicts"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
