import json
from itertools import islice
from pathlib import Path
from typing import Any, Iterable

path = Path(
    "graph_data/graph_nodes.jsonl"
)

def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"{path}:{line_number}: expected an object")
                yield record

for record in islice(read_jsonl(path), 3):
    print(json.dumps(record, ensure_ascii=False, indent=2))