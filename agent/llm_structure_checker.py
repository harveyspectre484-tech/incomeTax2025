"""
LLM Structure Verifier & Healer (llm_structure_checker.py)

Compares raw statutory Markdown (.md) against the generated AST JSON structure.
Uses Novita AI (Kimi K-3 model) to detect missing nodes, malformed nesting, or misaligned text,
and returns a healed, fully-compliant AST JSON document.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from novita_client import NovitaAIClient


class LLMStructureChecker:
    """Verifies and heals Legal AST JSON structures using Novita AI Kimi K-3 model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "moonshotai/kimi-k3",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        top_p: float = 0.95,
    ):
        self.client = NovitaAIClient(
            api_key=api_key,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )
        if self.client.is_configured():
            print(f"[LLM Checker] Novita AI client initialized (Model: {model_name}, Temp: {temperature})")
        else:
            print("[LLM Checker Warning] NOVITA_API_KEY environment variable not set. Skipping structural healing.")

    def verify_and_heal(self, md_content: str, ast_json: Dict[str, Any]) -> Dict[str, Any]:
        """Compares MD content against AST JSON and repairs structural issues using Kimi K-3."""
        if not self.client.is_configured():
            print("[LLM Checker] Bypassing structural check (No Novita API key).")
            return ast_json

        system_prompt = (
            "You are an expert Legal AI Abstract Syntax Tree(ATS) Validator. You check statutory Markdown text against "
            "an AST JSON representation and return a corrected, healed JSON structure."
        )

        prompt = f"""Compare the raw Markdown text with the initial AST JSON structure and heal any discrepancies.

Raw Markdown Text:
\"\"\"
{md_content}
\"\"\"

Initial Parsed AST JSON:
\"\"\"
{json.dumps(ast_json, ensure_ascii=False, indent=2)}
\"\"\"

Return ONLY the raw JSON string.
1. Compare the AST JSON hierarchy with the raw Markdown.
2. Ensure every section, subsection (1), clause (a), sub-clause (i), item (A) is accurately captured in its correct nested hierarchy:
   sections -> subsections -> clauses -> sub_clauses -> items.
3. Fix any missing text, misclassified nodes, or wrong parent-child relationships.
4. Output ONLY the valid, complete, healed AST JSON object. Do NOT wrap in conversational text.

Return ONLY the raw JSON string without markdown explanation."""

        try:
            print(f"[LLM Checker] Sending AST structure to Novita AI ({self.client.model}) for verification...")
            raw_text = self.client.generate_chat_completion(prompt, system_prompt=system_prompt)
            raw_text = raw_text.strip()

            # Clean markdown fences
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text, flags=re.I)
                raw_text = re.sub(r"\n?```$", "", raw_text)

            healed_json = json.loads(raw_text)
            print("[LLM Checker] AST structure validated and healed successfully.")
            return healed_json
        except Exception as e:
            print(f"[LLM Checker Error] Could not parse LLM response: {e}. Falling back to initial AST JSON.")
            return ast_json


def check_and_heal_ast(
    md_file: Path,
    json_data: Dict[str, Any],
    api_key: Optional[str] = None,
    model_name: str = "moonshotai/kimi-k3",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    top_p: float = 0.95,
) -> Dict[str, Any]:
    checker = LLMStructureChecker(
        api_key=api_key,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )
    md_content = md_file.read_text(encoding="utf-8")
    return checker.verify_and_heal(md_content, json_data)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python llm_structure_checker.py <input.md> <input.json> [-o output.json] [--model MODEL] [--temp TEMP]")
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
