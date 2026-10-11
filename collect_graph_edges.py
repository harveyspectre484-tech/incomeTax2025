from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

if __package__:
    from .collect_graph_nodes import relative_source, schedule_sort_key, section_sort_key
else:
    from collect_graph_nodes import relative_source, schedule_sort_key, section_sort_key


SCHEMA_VERSION = "1.2.0"
EDGE_TYPES = {
    "parent",
    "contains",
    "references",
    "references_external",
    "references_external_act",
    "references_schedule",
    "annotates",
}
EXTERNAL_TYPES = {"references_external", "references_external_act"}
EDGE_FIELDS = {
    "from_id",
    "to_id",
    "edge_type",
    "reference_text",
    "resolution_status",
    "resolution_basis",
}


def normalize_edge(source: dict[str, Any], source_file: str) -> dict[str, Any]:
    return {
        "from_id": source.get("from_id"),
        "to_id": source.get("to_id"),
        "edge_type": source.get("edge_type"),
        "reference_text": source.get("reference_text"),
        "source_file": source_file,
        "resolution_status": source.get("resolution_status"),
        "resolution_basis": source.get("resolution_basis"),
        "source_properties": {key: value for key, value in source.items() if key not in EDGE_FIELDS},
    }


def classify_edge(edge: dict[str, Any], node_counts: Counter[str]) -> tuple[str, str]:
    for field in ("from_id", "to_id", "edge_type"):
        value = edge.get(field)
        if not isinstance(value, str) or not value.strip():
            return "invalid", f"missing_{field}"
    if edge["edge_type"] not in EDGE_TYPES:
        return "invalid", "unknown_edge_type"
    if node_counts[edge["from_id"]] == 0:
        return "pending", "missing_source_node"
    if node_counts[edge["from_id"]] > 1:
        return "pending", "ambiguous_source_node"
    if edge["edge_type"] in EXTERNAL_TYPES:
        return "pending", "external_target_not_ingested"
    if node_counts[edge["to_id"]] == 0:
        return "pending", "missing_target_node"
    if node_counts[edge["to_id"]] > 1:
        return "pending", "ambiguous_target_node"
    return "resolved", "verified_endpoints"


def collect_edges(
    candidate_file: Path,
    section_dir: Path,
    schedule_dir: Path,
    hierarchy_file: Path,
    project_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    node_counts: Counter[str] = Counter()
    with candidate_file.open(encoding="utf-8") as handle:
        for line in handle:
            node = json.loads(line)
            if node.get("id"):
                node_counts[node["id"]] += 1

    sources = [
        *sorted(section_dir.glob("*section_can.json"), key=section_sort_key),
        *sorted(schedule_dir.glob("schedule*_can.json"), key=schedule_sort_key),
        hierarchy_file,
    ]
    resolved: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    report: list[dict[str, str]] = []
    raw_types: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for path in sources:
        data = json.loads(path.read_text(encoding="utf-8"))
        provenance = relative_source(path, project_root)
        for source_edge in data.get("graph_edges", []) or []:
            edge = normalize_edge(source_edge, provenance)
            raw_types[str(edge.get("edge_type"))] += 1
            classification, reason = classify_edge(edge, node_counts)
            reasons[reason] += 1
            if classification == "resolved":
                edge["resolution_status"] = "resolved"
                resolved.append(edge)
            else:
                if classification == "invalid":
                    edge["resolution_status"] = "invalid"
                elif reason.startswith("ambiguous"):
                    edge["resolution_status"] = "ambiguous"
                elif reason == "external_target_not_ingested":
                    edge["resolution_status"] = edge["resolution_status"] or "external_act_not_ingested"
                else:
                    edge["resolution_status"] = "unresolved"
                pending.append(edge)
                report.append({
                    "severity": "ERROR" if classification == "invalid" else "INFO",
                    "code": reason,
                    "from_id": str(edge.get("from_id") or ""),
                    "to_id": str(edge.get("to_id") or ""),
                    "edge_type": str(edge.get("edge_type") or ""),
                    "source_file": provenance,
                    "reference_text": str(edge.get("reference_text") or ""),
                })

    summary = {
        "schema_version": SCHEMA_VERSION,
        "stage": "4A_collect_graph_edges",
        "source_files_read": len(sources),
        "source_edge_occurrences": len(resolved) + len(pending),
        "resolved_edge_occurrences": len(resolved),
        "pending_edge_occurrences": len(pending),
        "edge_type_counts": dict(sorted(raw_types.items())),
        "pending_reason_counts": {key: value for key, value in sorted(reasons.items()) if key != "verified_endpoints"},
        "invalid_edge_occurrences": sum(row["severity"] == "ERROR" for row in report),
        "records_deduplicated": False,
    }
    return resolved, pending, report, summary


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect normalized graph edges and separate pending targets.")
    parser.add_argument("--candidate-nodes", required=True)
    parser.add_argument("--section-dir", required=True)
    parser.add_argument("--schedule-dir", required=True)
    parser.add_argument("--hierarchy-file")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--resolved", required=True)
    parser.add_argument("--pending", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    section_dir = Path(args.section_dir)
    resolved, pending, report, summary = collect_edges(
        Path(args.candidate_nodes),
        section_dir,
        Path(args.schedule_dir),
        Path(args.hierarchy_file) if args.hierarchy_file else section_dir / "chapter_hierarchy.json",
        Path(args.project_root),
    )
    write_jsonl(Path(args.resolved), resolved)
    write_jsonl(Path(args.pending), pending)
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["severity", "code", "from_id", "to_id", "edge_type", "source_file", "reference_text"])
        writer.writeheader()
        writer.writerows(report)
    print(f"Collected {summary['source_edge_occurrences']} edges: {len(resolved)} resolved, {len(pending)} pending")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
