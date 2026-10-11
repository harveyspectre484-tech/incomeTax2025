from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_TYPES = {"chapter", "schedule"}
HIERARCHY_PARENTS = {"chapter_division": "chapter", "chapter_subdivision": "chapter_division"}
EXPECTED_PARENT_TYPES = {
    "subsection": {"section"},
    "table_row": {"table"},
    "table_cell_item": {"table_row"},
    "part": {"schedule"},
    "paragraph": {"schedule", "part"},
    "subparagraph": {"paragraph"},
    "note": {"schedule"},
    "footnote": {"schedule"},
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def validate_parents(
    candidates: list[dict[str, Any]], resolved_edges: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    issues: list[dict[str, str]] = []

    def add(severity: str, code: str, node_id: str, parent_id: str | None, message: str) -> None:
        issues.append({
            "severity": severity,
            "code": code,
            "node_id": node_id,
            "parent_id": parent_id or "",
            "message": message,
        })

    counts = Counter(node.get("id") for node in candidates)
    nodes = {node["id"]: node for node in candidates if node.get("id")}
    parent_edges = Counter((edge.get("from_id"), edge.get("to_id")) for edge in resolved_edges if edge.get("edge_type") == "parent")
    contains_edges = Counter((edge.get("to_id"), edge.get("from_id")) for edge in resolved_edges if edge.get("edge_type") == "contains")

    for node_id, count in sorted(counts.items(), key=lambda item: str(item[0])):
        if node_id and count != 1:
            add("ERROR", "duplicate_node_id", node_id, None, f"Node ID occurs {count} times.")

    for node in candidates:
        node_id = node.get("id")
        if not node_id or counts[node_id] != 1:
            continue
        parent_id = node.get("parent_id")
        node_type = node.get("node_type")
        if node_type in ROOT_TYPES:
            if parent_id is not None:
                add("ERROR", "root_has_parent", node_id, parent_id, f"{node_type} root must have null parent_id.")
        elif not parent_id:
            add("ERROR", "missing_parent", node_id, None, "Non-root node has no parent_id.")

        if not parent_id:
            continue
        if parent_id not in nodes:
            add("ERROR", "missing_parent_node", node_id, parent_id, "Recorded parent node is absent.")
            continue
        parent = nodes[parent_id]
        parent_type = parent.get("node_type")

        expected = HIERARCHY_PARENTS.get(node_type)
        if expected and parent_type != expected:
            add("ERROR", "wrong_hierarchy_parent_type", node_id, parent_id, f"{node_type} requires a {expected} parent, found {parent_type}.")
        allowed = EXPECTED_PARENT_TYPES.get(node_type)
        if allowed and parent_type not in allowed:
            add("ERROR", "wrong_parent_type", node_id, parent_id, f"{node_type} parent is {parent_type}; expected one of {sorted(allowed)}.")

        if node_type == "section":
            deepest = node.get("subdivision_id") or node.get("division_id") or node.get("chapter_id")
            if parent_id != deepest:
                add("ERROR", "wrong_section_hierarchy_parent", node_id, parent_id, f"Section should attach to deepest hierarchy node {deepest!r}.")
            actual_path: dict[str, str | None] = {"chapter": None, "chapter_division": None, "chapter_subdivision": None}
            current = parent_id
            seen: set[str] = set()
            while current in nodes and current not in seen:
                seen.add(current)
                ancestor = nodes[current]
                if ancestor.get("node_type") in actual_path:
                    actual_path[ancestor["node_type"]] = current
                current = ancestor.get("parent_id")
            expected_path = {
                "chapter": node.get("chapter_id"),
                "chapter_division": node.get("division_id"),
                "chapter_subdivision": node.get("subdivision_id"),
            }
            if actual_path != expected_path:
                add("ERROR", "section_hierarchy_path_mismatch", node_id, parent_id, "Recorded Chapter path differs from actual structural ancestors.")
        elif node_type == "chapter_division" and node.get("chapter_id") != parent_id:
            add("ERROR", "wrong_division_chapter", node_id, parent_id, "Division chapter_id disagrees with parent_id.")
        elif node_type == "chapter_subdivision" and node.get("division_id") != parent_id:
            add("ERROR", "wrong_subdivision_division", node_id, parent_id, "Subdivision division_id disagrees with parent_id.")
        elif node_type == "subsection" and parent_id != node.get("section_id"):
            add("ERROR", "wrong_subsection_section", node_id, parent_id, "Subsection must attach to its recorded section_id.")

        if parent_edges[(node_id, parent_id)] != 1:
            add("ERROR", "parent_edge_mismatch", node_id, parent_id, f"Expected one parent edge; found {parent_edges[(node_id, parent_id)]}.")
        if contains_edges[(node_id, parent_id)] != 1:
            add("ERROR", "contains_edge_mismatch", node_id, parent_id, f"Expected one inverse contains edge; found {contains_edges[(node_id, parent_id)]}.")

    for pair, count in parent_edges.items():
        child_id, parent_id = pair
        if child_id not in nodes or nodes[child_id].get("parent_id") != parent_id:
            add("ERROR", "unexpected_parent_edge", str(child_id), str(parent_id), "Parent edge disagrees with candidate node.")
        if contains_edges[pair] != count:
            add("ERROR", "inverse_edge_count_mismatch", str(child_id), str(parent_id), "Parent and contains edge counts differ.")
    for pair, count in contains_edges.items():
        if parent_edges[pair] != count:
            add("ERROR", "unexpected_contains_edge", str(pair[0]), str(pair[1]), "Contains edge has no matching parent edge.")

    # Follow recorded parents, not ID segments; a repeated node on a path is a cycle.
    visited: set[str] = set()
    for node_id in nodes:
        if node_id in visited:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current = node_id
        while current in nodes and current not in visited:
            if current in positions:
                add("ERROR", "parent_cycle", current, nodes[current].get("parent_id"), f"Structural cycle: {' -> '.join(path[positions[current]:] + [current])}")
                break
            positions[current] = len(path)
            path.append(current)
            current = nodes[current].get("parent_id")
        visited.update(path)

    summary = {
        "stage": "4A_parent_selection",
        "candidate_nodes_checked": len(candidates),
        "root_nodes": dict(sorted(Counter(node.get("node_type") for node in candidates if node.get("parent_id") is None).items())),
        "recorded_parent_links": sum(node.get("parent_id") is not None for node in candidates),
        "resolved_parent_edge_occurrences": sum(parent_edges.values()),
        "resolved_contains_edge_occurrences": sum(contains_edges.values()),
        "error_count": sum(issue["severity"] == "ERROR" for issue in issues),
        "warning_count": sum(issue["severity"] == "WARN" for issue in issues),
        "issue_counts": dict(sorted(Counter(issue["code"] for issue in issues).items())),
    }
    return issues, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate canonical structural parents and inverse graph edges.")
    parser.add_argument("--candidate-nodes", required=True)
    parser.add_argument("--resolved-edges", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    issues, summary = validate_parents(read_jsonl(Path(args.candidate_nodes)), read_jsonl(Path(args.resolved_edges)))
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["severity", "code", "node_id", "parent_id", "message"])
        writer.writeheader()
        writer.writerows(issues)
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Checked {summary['candidate_nodes_checked']} nodes and {summary['recorded_parent_links']} parent links")
    print(f"Errors: {summary['error_count']}; warnings: {summary['warning_count']}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
