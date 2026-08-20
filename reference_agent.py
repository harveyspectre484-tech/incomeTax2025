"""
Legal Reference Detection Agent for Income-Tax Act AST JSON.

This script parses AST JSON produced by mdtojson.py, extracts internal and external
legal cross-references using a hybrid approach (Regex Engine + LLM Fallback),
classifies reference relationships, and updates the JSON in-place with a `references` array.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Optional Gemini LLM Integration
try:
    import google.generativeai as genai
    HAS_GEMINI_SDK = True
except ImportError:
    HAS_GEMINI_SDK = False


# =====================================================================
# STEP 1: PATTERN REGEX LIBRARY
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
    (re.compile(r"\bnotwithstanding\s+anything\s+contained\s+in\b", re.I), "overrides"),
    (re.compile(r"\bsubject\s+to\b", re.I), "subject_to"),
    (re.compile(r"\b(?:referred\s+to\s+in|computed\s+under|in\s+accordance\s+with|pursuant\s+to)\b", re.I), "depends_on"),
    (re.compile(r"\b(?:as\s+defined\s+in|within\s+the\s+meaning\s+of)\b", re.I), "defined_in"),
]


@dataclass
class ReferenceItem:
    reference_id: str
    target_id: str
    target_type: str  # "section", "subsection", "clause", "sub_clause", "external_act"
    anchor_text: str
    relationship: str  # "depends_on", "subject_to", "overrides", "defined_in", "refers_to"
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
# STEP 2: RELATIONSHIP CLASSIFIER & TARGET RESOLVER
# =====================================================================

def classify_relationship(text: str, match_start: int) -> str:
    """Analyze text snippet preceding the reference to classify relationship."""
    prefix = text[max(0, match_start - 60):match_start]
    for pattern, rel in RELATIONSHIP_PATTERNS:
        if pattern.search(prefix):
            return rel
    return "refers_to"


def resolve_relative_target(level: str, lineage: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve 'this clause', 'this section' to the active lineage node ID."""
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
# STEP 3: REFERENCE DETECTION ENGINE (HYBRID: REGEX + LLM)
# =====================================================================

class ReferenceDetectorAgent:
    """Agent that scans text in AST JSON nodes and detects cross-references."""

    def __init__(self, use_llm_fallback: bool = False, model_name: str = "gemini-1.5-flash"):
        self.use_llm_fallback = use_llm_fallback
        self.model_name = model_name
        self.ref_counter = 0
        self.llm_model = None

        if self.use_llm_fallback:
            self.init_llm_client()

    def init_llm_client(self) -> None:
        """Initialize LLM model client if API key is provided."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if HAS_GEMINI_SDK and api_key:
            genai.configure(api_key=api_key)
            self.llm_model = genai.GenerativeModel(self.model_name)
            print(f"[Agent] LLM Fallback activated using Gemini ({self.model_name})")
        else:
            print("[Agent Warning] --use-llm passed but GEMINI_API_KEY environment variable or google-generativeai package is not set. LLM fallback will run in heuristic mode.")

    def generate_ref_id(self, node_id: str) -> str:
        self.ref_counter += 1
        prefix = node_id if node_id else "node"
        return f"ref-{prefix}-{self.ref_counter:03d}"

    def extract_references_from_text(self, text: str, lineage: Dict[str, Any]) -> List[ReferenceItem]:
        refs: List[ReferenceItem] = []
        seen_anchors = set()

        if not text or not text.strip():
            return refs

        # --- 1. External Act References ---
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

        # --- 2. Express Internal Section References ---
        for match in EXPRESS_SECTION_RE.finditer(text):
            anchor = match.group(0)
            if anchor in seen_anchors:
                continue

            prefix_type = match.group("prefix").lower()
            numbers_str = match.group("numbers")
            parent_sec = match.group("parent_section")

            tokens = [t.strip() for t in re.split(r",|\band\b|\bor\b|to", numbers_str, flags=re.I) if t.strip()]
            rel = classify_relationship(text, match.start())

            for tok in tokens:
                clean_tok = tok.strip("()")
                if not clean_tok or clean_tok.lower() in {"and", "or", "to"}:
                    continue

                if "section" in prefix_type:
                    target_type = "section"
                    target_id = clean_tok
                elif "clause" in prefix_type and "sub" not in prefix_type:
                    target_type = "clause"
                    sec_id = parent_sec or (lineage.get("section", {}).get("number") if lineage.get("section") else "")
                    target_id = f"{sec_id}-{clean_tok}" if sec_id else clean_tok
                elif "sub_clause" in prefix_type or "sub-clause" in prefix_type:
                    target_type = "sub_clause"
                    sec_id = parent_sec or (lineage.get("section", {}).get("number") if lineage.get("section") else "")
                    target_id = f"{sec_id}-{clean_tok}" if sec_id else clean_tok
                else:
                    target_type = "section"
                    target_id = clean_tok

                ref_id = self.generate_ref_id(lineage.get("_current_id", ""))
                refs.append(
                    ReferenceItem(
                        reference_id=ref_id,
                        target_id=str(target_id),
                        target_type=target_type,
                        anchor_text=anchor,
                        relationship=rel,
                        confidence="high",
                        detected_by="regex",
                    )
                )
                seen_anchors.add(anchor)

        # --- 3. Relative Internal References ---
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

        # --- 4. LLM Disambiguation Fallback for Complex / Missed References ---
        if self.use_llm_fallback:
            llm_refs = self.llm_disambiguate_fallback(text, lineage, list(seen_anchors))
            for ref in llm_refs:
                if ref.anchor_text not in seen_anchors:
                    refs.append(ref)

        return refs

    def llm_disambiguate_fallback(self, text: str, lineage: Dict[str, Any], existing_anchors: List[str]) -> List[ReferenceItem]:
        """Calls LLM model to detect complex references missed by regex patterns."""
        llm_refs: List[ReferenceItem] = []

        if not self.llm_model:
            return llm_refs

        node_id = lineage.get("_current_id", "unknown")

        prompt = f"""You are a specialized Legal AI Agent parsing cross-references in the Income-tax Act.
Your job is to analyze the following statutory text snippet and find ANY cross-references to other sections, sub-sections, clauses, or external Acts that were NOT already detected.

Node ID: {node_id}
Already Detected References (DO NOT REPEAT): {json.dumps(existing_anchors)}
Text snippet:
"{text}"

Task:
1. Identify any missing legal section or Act cross-reference phrase (anchor_text).
2. Determine the target section ID (target_id) and target type (section, subsection, clause, sub_clause, external_act).
3. Classify relationship: "depends_on", "subject_to", "overrides", "defined_in", or "refers_to".

Respond ONLY with a valid JSON array of objects. Example:
[
  {{
    "target_id": "54F",
    "target_type": "section",
    "anchor_text": "section 54F",
    "relationship": "depends_on",
    "confidence": "high"
  }}
]
If no additional references exist, respond with []."""

        try:
            response = self.llm_model.generate_content(prompt)
            raw_response = response.text.strip()

            # Clean JSON formatting block if LLM returns markdown codefence
            if raw_response.startswith("```"):
                raw_response = re.sub(r"^```[a-z]*\n?", "", raw_response, flags=re.I)
                raw_response = re.sub(r"\n?```$", "", raw_response)

            data = json.loads(raw_response)
            if isinstance(data, list):
                for item in data:
                    ref_id = self.generate_ref_id(node_id)
                    llm_refs.append(
                        ReferenceItem(
                            reference_id=ref_id,
                            target_id=str(item.get("target_id", "")),
                            target_type=str(item.get("target_type", "section")),
                            anchor_text=str(item.get("anchor_text", "")),
                            relationship=str(item.get("relationship", "refers_to")),
                            confidence=str(item.get("confidence", "high")),
                            act_name=item.get("act_name"),
                            detected_by="llm",
                        )
                    )
        except Exception as e:
            print(f"[Agent LLM Error] Could not parse LLM response for node {node_id}: {e}")

        return llm_refs


# =====================================================================
# STEP 4: RECURSIVE AST TRAVERSAL & JSON IN-PLACE UPDATER
# =====================================================================

def update_node_references(node: Dict[str, Any], detector: ReferenceDetectorAgent, lineage: Dict[str, Any]) -> None:
    """Traverse AST node, scan text fields, and attach 'references' list."""
    if not isinstance(node, dict):
        return

    node_type = node.get("type")
    node_id = node.get("id", "")
    current_lineage = dict(lineage)
    if node_type:
        current_lineage[node_type] = node
    current_lineage["_current_id"] = node_id

    text_pieces = []
    for key in ("text", "text_before_subsections", "text_before_clauses",
                "text_before_sub_clauses", "text_before_items", "text_before_sub_items"):
        if node.get(key):
            text_pieces.append(node[key])

    full_node_text = " ".join(text_pieces)

    if full_node_text:
        detected = detector.extract_references_from_text(full_node_text, current_lineage)
        if detected:
            existing_refs = node.setdefault("references", [])
            for ref in detected:
                existing_refs.append(ref.to_dict())

    child_keys = ("chapters", "sections", "subsections", "clauses", "sub_clauses", "items", "sub_items", "children")
    for key in child_keys:
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    update_node_references(child, detector, current_lineage)


def process_document(doc: Dict[str, Any], use_llm: bool = False, model_name: str = "gemini-1.5-flash") -> Dict[str, Any]:
    """Process full AST JSON document and add references to all nodes."""
    detector = ReferenceDetectorAgent(use_llm_fallback=use_llm, model_name=model_name)
    lineage: Dict[str, Any] = {}

    for key in ("chapters", "sections"):
        items = doc.get(key, [])
        for item in items:
            update_node_references(item, detector, lineage)

    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="Legal Reference Detection AI Agent")
    parser.add_argument("input", type=Path, help="Input AST JSON file (from mdtojson.py)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file path (overwrites in-place if omitted)")
    parser.add_argument("--use-llm", action="store_true", help="Enable LLM fallback for ambiguous / complex references")
    parser.add_argument("--model", type=str, default="gemini-1.5-flash", help="Gemini model name to use for LLM fallback")
    args = parser.parse_args()

    doc = json.loads(args.input.read_text(encoding="utf-8"))
    updated_doc = process_document(doc, use_llm=args.use_llm, model_name=args.model)

    output_path = args.output if args.output else args.input
    output_path.write_text(json.dumps(updated_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Successfully processed references and updated: {output_path}")


if __name__ == "__main__":
    main()
