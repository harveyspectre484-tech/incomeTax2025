"""
Legal Reference Detection Agent for Income-Tax Act AST JSON.

Parses AST JSON, extracts internal and external legal cross-references using
Regex + LLM Fallback, classifies legal relationships into standard categories:
[depends_on, defines_term, subject_to, exception_to, procedural_reference, related_to, amended_by_footnote],
handles section ranges (e.g. sections 28 to 33 -> list target_id), and updates the JSON in-place.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv
load_dotenv()
try:
    import google.generativeai as genai
    HAS_GEMINI_SDK = True
except ImportError:
    HAS_GEMINI_SDK = False


# =====================================================================
# STEP 1: PATTERN REGEX LIBRARY & RANGE EXPANDER
# =====================================================================

NUM_TOKEN_RE = r"(?:\d+[A-Z]?|\([a-z0-9]+\))"

EXPRESS_SECTION_RE = re.compile(
    r"\b(?P<prefix>sections?|clauses?|sub-sections?|sub-clauses?|items?)\s+"
    r"(?P<numbers>" + NUM_TOKEN_RE + r"(?:\s*(?:,|\band\b|\bor\b|to)\s*" + NUM_TOKEN_RE + r")*)"
    r"(?:\s+of\s+(?:section\s+)?(?P<parent_section>\d+[A-Z]?))?",
    re.IGNORECASE,
)

EXTERNAL_ACT_RE = re.compile(
    r"(?:section|clause)\s+(?P<ref>\d+(?:\(\d+\))?)\s+of\s+the\s+"
    r"(?P<act_name>[A-Z][A-Za-z0-9\s,\(\)]+?\s*Act,\s*\d{4})",
    re.IGNORECASE,
)

RELATIVE_REF_RE = re.compile(
    r"\b(?:under|in|of|for\s+the\s+purposes?\s+of)\s+this\s+"
    r"(?P<level>chapter|section|sub-section|clause|sub-clause|item)\b",
    re.IGNORECASE,
)

RELATIONSHIP_PATTERNS = [
    (re.compile(r"\bnotwithstanding\s+anything\s+contained\s+in\b", re.I), "exception_to"),
    (re.compile(r"\bsubject\s+to\b", re.I), "subject_to"),
    (re.compile(r"\b(?:referred\s+to\s+in|computed\s+under|in\s+accordance\s+with|pursuant\s+to|specified\s+in)\b", re.I), "depends_on"),
    (re.compile(r"\b(?:as\s+defined\s+in|within\s+the\s+meaning\s+of|meaning\s+assigned\s+to)\b", re.I), "defines_term"),
    (re.compile(r"\b(?:manner\s+prescribed|procedure\s+under|as\s+prescribed\s+under)\b", re.I), "procedural_reference"),
    (re.compile(r"\b(?:inserted\s+by|substituted\s+by|amended\s+by)\b", re.I), "amended_by_footnote"),
]


def expand_section_range(numbers_str: str) -> List[str]:
    """Expands strings like '28 to 33, 44 to 49, 51 and 52' into a list of section ID strings."""
    results: List[str] = []
    # Split by comma or 'and'
    parts = re.split(r",|\band\b", numbers_str, flags=re.I)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        range_match = re.match(r"^(\d+)\s+to\s+(\d+)$", part, re.I)
        if range_match:
            start_num = int(range_match.group(1))
            end_num = int(range_match.group(2))
            for num in range(start_num, end_num + 1):
                results.append(str(num))
        else:
            clean_tok = part.strip("()").strip()
            if clean_tok and clean_tok.lower() not in {"and", "or", "to"}:
                results.append(clean_tok)
    return results


@dataclass
class ReferenceItem:
    reference_id: str
    target_id: Union[str, List[str]]
    target_type: str  # "section", "subsection", "clause", "sub_clause", "external_act"
    anchor_text: str
    relationship: str  # "depends_on", "defines_term", "subject_to", "exception_to", "procedural_reference", "related_to", "amended_by_footnote"
    confidence: str  # "high", "medium", "low"
    act_name: Optional[str] = None
    detected_by: str = "regex"  # "regex" or "llm"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "reference_id": self.reference_id,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "anchor_text": self.anchor_text,
            "relationship": self.relationship,
            "confidence": self.confidence,
            "detected_by": self.detected_by,
        }
        if self.act_name:
            d["act_name"] = self.act_name
        return d


# =====================================================================
# STEP 2: RELATIONSHIP CLASSIFIER
# =====================================================================

def classify_relationship(text: str, match_start: int) -> str:
    """Analyze text snippet preceding the reference to classify relationship."""
    prefix = text[max(0, match_start - 70):match_start]
    for pattern, rel in RELATIONSHIP_PATTERNS:
        if pattern.search(prefix):
            return rel
    return "related_to"


def resolve_relative_target(level: str, lineage: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve 'this clause', 'this section' to active lineage node ID."""
    lvl = level.lower().replace("-", "_")
    node = lineage.get(lvl)
    if node and node.get("id"):
        return str(node["id"]), lvl
    for fallback_key in ("item", "sub_clause", "clause", "subsection", "section"):
        n = lineage.get(fallback_key)
        if n and n.get("id"):
            return str(n["id"]), fallback_key
    return "unknown", "unknown"


# =====================================================================
# STEP 3: REFERENCE DETECTION ENGINE
# =====================================================================

class ReferenceDetectorAgent:
    """Agent scanning AST text nodes for internal/external cross-references."""
#gemini-3-flash-preview
    def __init__(self, use_llm_fallback: bool = False, model_name: str = "gemini-3.5-flash"):
        self.use_llm_fallback = use_llm_fallback
        self.model_name = model_name
        self.ref_counter = 0
        self.llm_model = None

        if self.use_llm_fallback:
            self.init_llm_client()

    def init_llm_client(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if HAS_GEMINI_SDK and api_key:
            genai.configure(api_key=api_key)
            self.llm_model = genai.GenerativeModel(self.model_name)
            print(f"[ReferenceAgent] LLM Fallback activated ({self.model_name})")
        else:
            print("[ReferenceAgent Warning] GEMINI_API_KEY missing or SDK not installed. Running regex only.")

    def generate_ref_id(self, node_id: str) -> str:
        self.ref_counter += 1
        prefix = node_id if node_id else "node"
        return f"ref-{prefix}-{self.ref_counter:03d}"

    def extract_references_from_text(self, text: str, lineage: Dict[str, Any]) -> List[ReferenceItem]:
        refs: List[ReferenceItem] = []
        seen_anchors = set()

        if not text or not text.strip():
            return refs

        # 1. External Act References (e.g. section 135 of the Companies Act, 2013)
        for match in EXTERNAL_ACT_RE.finditer(text):
            anchor = match.group(0)
            if anchor in seen_anchors:
                continue
            seen_anchors.add(anchor)

            ref_num = match.group("ref")
            act_name = match.group("act_name").strip()
            rel = classify_relationship(text, match.start())
            ref_id = self.generate_ref_id(lineage.get("_current_id", ""))

            refs.append(
                ReferenceItem(
                    reference_id=ref_id,
                    target_id=f"external:{act_name}:{ref_num}",
                    target_type="external_act",
                    anchor_text=anchor,
                    relationship=rel,
                    confidence="high",
                    act_name=act_name,
                    detected_by="regex",
                )
            )

        # 2. Express Section References (e.g. sections 28 to 33, 44 to 49)
        for match in EXPRESS_SECTION_RE.finditer(text):
            anchor = match.group(0)
            if anchor in seen_anchors or any(anchor in ext_a for ext_a in seen_anchors):
                continue

            prefix_type = match.group("prefix").lower()
            numbers_str = match.group("numbers")
            parent_sec = match.group("parent_section")

            expanded_targets = expand_section_range(numbers_str)
            rel = classify_relationship(text, match.start())

            if not expanded_targets:
                continue

            target_type = "section"
            if "clause" in prefix_type and "sub" not in prefix_type:
                target_type = "clause"
            elif "sub" in prefix_type:
                target_type = "sub_clause"

            ref_id = self.generate_ref_id(lineage.get("_current_id", ""))
            
            # If multiple targets in range, use list as target_id
            target_val = expanded_targets if len(expanded_targets) > 1 else expanded_targets[0]

            refs.append(
                ReferenceItem(
                    reference_id=ref_id,
                    target_id=target_val,
                    target_type=target_type,
                    anchor_text=anchor,
                    relationship=rel,
                    confidence="high",
                    detected_by="regex",
                )
            )
            seen_anchors.add(anchor)

        # 3. Relative Internal References (e.g. this section, this clause)
        for match in RELATIVE_REF_RE.finditer(text):
            anchor = match.group(0)
            if anchor in seen_anchors:
                continue
            seen_anchors.add(anchor)

            level = match.group("level")
            target_id, target_type = resolve_relative_target(level, lineage)
            rel = classify_relationship(text, match.start())
            ref_id = self.generate_ref_id(lineage.get("_current_id", ""))

            refs.append(
                ReferenceItem(
                    reference_id=ref_id,
                    target_id=target_id,
                    target_type=target_type,
                    anchor_text=anchor,
                    relationship=rel,
                    confidence="medium",
                    detected_by="regex",
                )
            )

        # 4. LLM Disambiguation Fallback
        if self.use_llm_fallback and self.llm_model:
            llm_refs = self.llm_disambiguate_fallback(text, lineage, list(seen_anchors))
            for ref in llm_refs:
                if ref.anchor_text not in seen_anchors:
                    refs.append(ref)

        return refs

    def llm_disambiguate_fallback(
        self, text: str, lineage: Dict[str, Any], existing_anchors: List[str]
    ) -> List[ReferenceItem]:
        llm_refs: List[ReferenceItem] = []
        node_id = lineage.get("_current_id", "unknown")

        prompt = f"""You are a specialized Legal AI Agent parsing cross-references in statutory text.
Find any missing references to other sections, clauses, or external Acts not detected. 
were ever have to take a reference of income tact act take income tax act 2025, 
do not take reference of income tax act 1961.


Node ID: {node_id}
Already Detected Anchors: {json.dumps(existing_anchors)}
Text snippet:
"{text}"

Task:
1. If the json structure is not right for (section, subsection, clause, sub_clause) then correct it.
2. Identify missing cross-references (anchor_text).
3. Determine target_id (can be a string or list of strings if multiple sections/ranges).
4. Determine target_type (section, subsection, clause, sub_clause, external_act).
5. Classify relationship: "depends_on", "defines_term", "subject_to", "exception_to", "procedural_reference", "related_to", "amended_by_footnote".

Respond ONLY with a JSON array of objects:
[
  {{
    "target_id": ["28", "29", "30"],
    "target_type": "section",
    "anchor_text": "sections 28 to 30",
    "relationship": "exception_to",
    "confidence": "high"
  }}
]"""

        try:
            response = self.llm_model.generate_content(prompt)
            raw_res = response.text.strip()
            if raw_res.startswith("```"):
                raw_res = re.sub(r"^```[a-z]*\n?", "", raw_res, flags=re.I)
                raw_res = re.sub(r"\n?```$", "", raw_res)

            data = json.loads(raw_res)
            if isinstance(data, list):
                for item in data:
                    ref_id = self.generate_ref_id(node_id)
                    llm_refs.append(
                        ReferenceItem(
                            reference_id=ref_id,
                            target_id=item.get("target_id", ""),
                            target_type=str(item.get("target_type", "section")),
                            anchor_text=str(item.get("anchor_text", "")),
                            relationship=str(item.get("relationship", "related_to")),
                            confidence=str(item.get("confidence", "high")),
                            act_name=item.get("act_name"),
                            detected_by="llm",
                        )
                    )
        except Exception as e:
            print(f"[ReferenceAgent LLM Error] Node {node_id}: {e}")

        return llm_refs


# =====================================================================
# STEP 4: RECURSIVE AST TRAVERSAL
# =====================================================================

def update_node_references(node: Dict[str, Any], detector: ReferenceDetectorAgent, lineage: Dict[str, Any]) -> None:
    if not isinstance(node, dict):
        return

    node_type = node.get("type")
    node_id = node.get("id", "")
    current_lineage = dict(lineage)
    if node_type:
        current_lineage[node_type] = node
    current_lineage["_current_id"] = node_id

    text_pieces = []
    for key in ("text", "title"):
        if node.get(key):
            text_pieces.append(node[key])

    full_text = " ".join(text_pieces)

    if full_text:
        detected = detector.extract_references_from_text(full_text, current_lineage)
        if detected:
            existing_refs = node.setdefault("references", [])
            for ref in detected:
                existing_refs.append(ref.to_dict())

    child_keys = ("sections", "subsections", "clauses", "sub_clauses", "items", "sub_items", "children")
    for key in child_keys:
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    update_node_references(child, detector, current_lineage)


def process_document(doc: Dict[str, Any], use_llm: bool = False, model_name: str = "gemini-1.5-flash") -> Dict[str, Any]:
    detector = ReferenceDetectorAgent(use_llm_fallback=use_llm, model_name=model_name)
    lineage: Dict[str, Any] = {}

    for key in ("sections", "subsections"):
        items = doc.get(key, [])
        for item in items:
            update_node_references(item, detector, lineage)

    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="Legal Reference Detection Agent")
    parser.add_argument("input", type=Path, help="Input AST JSON file")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file path")
    parser.add_argument("--use-llm", action="store_true", help="Enable LLM fallback")
    parser.add_argument("--model", type=str, default="gemini-1.5-flash", help="Gemini model name")
    args = parser.parse_args()

    doc = json.loads(args.input.read_text(encoding="utf-8"))
    updated_doc = process_document(doc, use_llm=args.use_llm, model_name=args.model)

    output_path = args.output if args.output else args.input
    output_path.write_text(json.dumps(updated_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ReferenceAgent] Updated references in: {output_path}")


if __name__ == "__main__":
    main()
