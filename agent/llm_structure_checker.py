"""
LLM Structure Verifier & Healer (llm_structure_checker.py)

Compares raw statutory Markdown (.md) against the generated AST JSON structure.
Uses Gemini LLM to detect missing nodes, malformed nesting, or misaligned text,
and returns a healed, fully-compliant AST JSON document.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False


class LLMStructureChecker:
    """Verifies and heals Legal AST JSON structures using Gemini."""

    def __init__(self, model_name: str = "gemini-1.5-flash"):
        self.model_name = model_name
        self.model = None
        self.init_llm()

    def init_llm(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if HAS_GEMINI and api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(self.model_name)
            print(f"[LLM Checker] Gemini initialized with model: {self.model_name}")
        else:
            print("[LLM Checker Warning] GEMINI_API_KEY not set or google-generativeai SDK missing. Skipping LLM structural healing.")

    def verify_and_heal(self, md_content: str, ast_json: Dict[str, Any]) -> Dict[str, Any]:
        """Compares MD content against AST JSON and repairs any structural issues."""
        if not self.model:
            print("[LLM Checker] Bypassing structural check (No Gemini client).")
            return ast_json

        prompt = f"""You are an expert Legal AI AST Validator.
You are given a raw Markdown statutory text and an initial AST JSON produced by a parser script.

Raw Markdown Text:
\"\"\"
{md_content}
\"\"\"

Initial Parsed AST JSON:
\"\"\"
{json.dumps(ast_json, ensure_ascii=False, indent=2)}
\"\"\"

Task:
1. Compare the AST JSON hierarchy with the raw Markdown.
2. Ensure every section, subsection (1), clause (a), sub-clause (i), item (A) is accurately captured in its correct nested hierarchy:
   sections -> subsections -> clauses -> sub_clauses -> items.
3. Fix any missing text, misclassified nodes, or wrong parent-child relationships.
4. Output ONLY the valid, complete, healed AST JSON object. Do NOT wrap in conversational text.

Return ONLY the raw JSON string."""

        try:
            print("[LLM Checker] Sending AST structure to Gemini for validation & healing...")
            response = self.model.generate_content(prompt)
            raw_text = response.text.strip()

            # Clean markdown fences if present
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text, flags=re.I)
                raw_text = re.sub(r"\n?```$", "", raw_text)

            healed_json = json.loads(raw_text)
            print("[LLM Checker] AST structure validated and healed successfully.")
            return healed_json
        except Exception as e:
            print(f"[LLM Checker Error] Error during structural validation: {e}. Falling back to initial AST JSON.")
            return ast_json


def check_and_heal_ast(md_file: Path, json_data: Dict[str, Any], model_name: str = "gemini-1.5-flash") -> Dict[str, Any]:
    checker = LLMStructureChecker(model_name=model_name)
    md_content = md_file.read_text(encoding="utf-8")
    return checker.verify_and_heal(md_content, json_data)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python llm_structure_checker.py <input.md> <input.json> [-o output.json]")
        sys.exit(1)

    md_file = Path(sys.argv[1])
    json_file = Path(sys.argv[2])
    output_file = json_file

    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            output_file = Path(sys.argv[idx + 1])

    ast_json = json.loads(json_file.read_text(encoding="utf-8"))
    healed_ast = check_and_heal_ast(md_file, ast_json)

    output_file.write_text(json.dumps(healed_ast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[LLM Checker] Saved healed AST JSON: {output_file}")


if __name__ == "__main__":
    main()
