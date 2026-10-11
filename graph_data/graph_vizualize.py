import json
from pathlib import Path
from pyvis.network import Network

graph_dir = Path(
    "/Users/avikalchauhan/incometax2025anti/incomeTax2025/graph_data"
)
nodes_file = graph_dir / "graph_nodes.jsonl"
edges_file = graph_dir / "graph_edges.jsonl"
output_file = graph_dir / "all_sections_graph.html"

net = Network(
    height="850px",
    width="100%",
    directed=True,
    notebook=False,
    bgcolor="#ffffff",
    font_color="#222222",
)

# Add each legal unit as a graph node.
with nodes_file.open(encoding="utf-8") as file:
    for line in file:
        node = json.loads(line)
        node_id = node["id"]
        label = node.get("citation") or node_id
        title = "\n".join(
            str(value) for value in (
                node.get("citation"),
                node.get("title"),
                node.get("node_type"),
                node.get("text"),
            ) if value
        )

        net.add_node(
            node_id,
            label=label,
            title=title,
            group=node.get("node_type", "unknown"),
        )

# Add the active relationships. "contains" duplicates "parent" in reverse.
with edges_file.open(encoding="utf-8") as file:
    for line in file:
        edge = json.loads(line)

        if edge["edge_type"] == "contains":
            continue

        if edge["from_id"] not in net.node_ids or edge["to_id"] not in net.node_ids:
            continue

        net.add_edge(
            edge["from_id"],
            edge["to_id"],
            label=edge["edge_type"],
            title=edge.get("reference_text") or edge["edge_type"],
            arrows="to",
        )

# Initial layout; you can drag nodes and zoom in the resulting page.
net.barnes_hut()
net.write_html(str(output_file), open_browser=False)

print(f"Graph written to: {output_file}")
print("Open all_sections_graph.html in your browser.")