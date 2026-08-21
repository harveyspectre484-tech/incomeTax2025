"""
Markdown to Legal AST JSON Parser (mdtojson.py)

Parses statutory Markdown text into a structured Abstract Syntax Tree (AST) JSON
representing Sections, Subsections, Clauses, Sub-clauses, Items, and Sub-items.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class LegalASTParser:
    """Parses legal section Markdown files into structured AST JSON."""

    SECTION_HEADER_RE = re.compile(
        r"^(?:#+\s*)?(?:Section\s+)?(?P<sec_num>\d+[A-Z]*)\.?\s*(?P<title>.*)", re.IGNORECASE
    )
    SUBSECTION_RE = re.compile(r"^\((?P<num>\d+[A-Z]*)\)\s*(?P<text>.*)")
    CLAUSE_RE = re.compile(r"^\((?P<num>[a-z]{1,3})\)\s*(?P<text>.*)")
    SUBCLAUSE_RE = re.compile(r"^\((?P<num>[ivxlcdm]+)\)\s*(?P<text>.*)")
    ITEM_RE = re.compile(r"^\((?P<num>[A-Z]{1,2})\)\s*(?P<text>.*)")
    SUBITEM_RE = re.compile(r"^\((?P<num>[IVXLCDM]+)\)\s*(?P<text>.*)")

    def __init__(self, content: str):
        self.lines = content.splitlines()

    def parse(self) -> Dict[str, Any]:
        """Main entry point to parse markdown lines into AST root."""
        root: Dict[str, Any] = {"sections": []}
        current_section: Optional[Dict[str, Any]] = None

        line_no = 0
        while line_no < len(self.lines):
            line = self.lines[line_no].strip()
            if not line:
                line_no += 1
                continue

            sec_match = self.SECTION_HEADER_RE.match(line)
            if sec_match:
                sec_num = sec_match.group("sec_num")
                sec_title = sec_match.group("title").strip()
                current_section = {
                    "type": "section",
                    "number": sec_num,
                    "id": sec_num,
                    "title": sec_title,
                    "line_start": line_no + 1,
                    "text": line,
                    "subsections": [],
                    "clauses": [],
                    "children": [],
                }
                root["sections"].append(current_section)
                line_no += 1
                continue

            if current_section is None:
                # If no explicit section header, create default section
                current_section = {
                    "type": "section",
                    "number": "1",
                    "id": "1",
                    "title": "",
                    "line_start": line_no + 1,
                    "text": "",
                    "subsections": [],
                    "clauses": [],
                    "children": [],
                }
                root["sections"].append(current_section)

            # Parse children within current section
            line_no = self._parse_node(current_section, line_no)

        return root

    def _parse_node(self, parent: Dict[str, Any], start_line: int) -> int:
        """Parses lines into node children."""
        line = self.lines[start_line].strip()
        indent = len(self.lines[start_line]) - len(self.lines[start_line].lstrip())
        parent_id = parent.get("id", "")
        sec_id = parent_id.split("-")[0] if parent_id else ""

        # Determine node pattern
        node_type = None
        num = None
        text_content = ""

        sub_m = self.SUBSECTION_RE.match(line)
        cl_m = self.CLAUSE_RE.match(line)
        subcl_m = self.SUBCLAUSE_RE.match(line)
        item_m = self.ITEM_RE.match(line)
        subitem_m = self.SUBITEM_RE.match(line)

        if sub_m:
            node_type = "subsection"
            num = sub_m.group("num")
            text_content = sub_m.group("text")
        elif subcl_m and parent.get("type") in ("clause", "subsection"):
            node_type = "sub_clause"
            num = subcl_m.group("num")
            text_content = subcl_m.group("text")
        elif cl_m:
            node_type = "clause"
            num = cl_m.group("num")
            text_content = cl_m.group("text")
        elif item_m:
            node_type = "item"
            num = item_m.group("num")
            text_content = item_m.group("text")
        elif subitem_m:
            node_type = "sub_item"
            num = subitem_m.group("num")
            text_content = subitem_m.group("text")
        else:
            # Continues text of parent node
            if parent.get("text"):
                parent["text"] += " " + line
            else:
                parent["text"] = line
            return start_line + 1

        node_id = f"{parent_id}-{num}" if parent_id else f"{sec_id}-{num}"
        node: Dict[str, Any] = {
            "type": node_type,
            "number": num,
            "id": node_id,
            "indent": indent,
            "line_start": start_line + 1,
            "page_start": 1,
            "page_end": 1,
            "text": line,
            "children": [],
        }

        # Attach to correct child list in parent
        child_key = f"{node_type}s" if not node_type.endswith("s") else node_type
        if child_key in parent and isinstance(parent[child_key], list):
            parent[child_key].append(node)
        else:
            parent["children"].append(node)

        return start_line + 1


def parse_markdown_to_ast(md_content: str) -> Dict[str, Any]:
    parser = LegalASTParser(md_content)
    return parser.parse()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python mdtojson.py <input.md> [-o output.json]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = input_path.with_suffix(".json")

    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])

    md_text = input_path.read_text(encoding="utf-8")
    ast_data = parse_markdown_to_ast(md_text)

    output_path.write_text(json.dumps(ast_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[mdtojson] Successfully generated AST JSON: {output_path}")


if __name__ == "__main__":
    main()

