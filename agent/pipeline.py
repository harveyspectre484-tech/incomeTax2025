"""
Unified Legal Reference Pipeline (pipeline.py)

Executes the complete 3-step pipeline using mdtojsonadobe.py and Novita AI (Kimi K-3 model):
1. Markdown to Adobe AST JSON parsing (mdtojsonadobe.py)
2. LLM AST Structure Verification & Healing (llm_structure_checker.py)
3. Cross-Reference Detection & Annotation (reference_detector.py)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from mdtojsonadobe import parse_markdown_to_adobe_ast
from llm_structure_checker import check_and_heal_ast
from reference_detector import process_document


def run_pipeline(
    input_md_path: Path,
    output_json_path: Path,
    use_llm_healing: bool = True,
    use_llm_ref_detection: bool = True,
    api_key: Optional[str] = None,
    model_name: str = "moonshotai/kimi-k3",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    top_p: float = 0.95,
) -> None:
    print(f"=== Step 1: Parsing Markdown ({input_md_path.name}) using mdtojsonadobe ===")
    ast_json = parse_markdown_to_adobe_ast(input_md_path)
    sections_cnt = len(ast_json.get("sections", []))
    chapters_cnt = len(ast_json.get("chapters", []))
    print(f"  └─ Generated Adobe AST with {chapters_cnt} chapter(s) and {sections_cnt} section(s).")

    print(f"\n=== Step 2: Novita AI ({model_name}) Structural Verification & Healing ===")
    if use_llm_healing:
        healed_ast = check_and_heal_ast(
            input_md_path,
            ast_json,
            api_key=api_key,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        )
    else:
        print("  └─ Skipping LLM structural healing (--no-heal passed).")
        healed_ast = ast_json

    print(f"\n=== Step 3: Reference Detection & Annotation ({model_name}) ===")
    final_ast = process_document(
        healed_ast,
        use_llm=use_llm_ref_detection,
        api_key=api_key,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )

    output_json_path.write_text(json.dumps(final_ast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[Pipeline Complete] Output successfully written to: {output_json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Legal AST & Reference Detection Pipeline (Novita AI + Kimi K-3)")
    parser.add_argument("input_md", type=Path, help="Input legal Markdown file (act_section.md)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file path (act_section.json)")
    parser.add_argument("--no-heal", action="store_true", help="Disable LLM AST structural healing")
    parser.add_argument("--no-llm-ref", action="store_true", help="Disable LLM reference fallback")
    parser.add_argument("--api-key", type=str, default=None, help="Novita API Key (defaults to NOVITA_API_KEY env var)")
    parser.add_argument("--model", type=str, default="moonshotai/kimi-k3", help="Novita AI model name (default: moonshotai/kimi-k3)")
    parser.add_argument("--temperature", type=float, default=0.1, help="LLM sampling temperature (default: 0.1)")
    parser.add_argument("--max-tokens", type=int, default=4096, help="LLM max output tokens (default: 4096)")
    parser.add_argument("--top-p", type=float, default=0.95, help="LLM top_p parameter (default: 0.95)")

    args = parser.parse_args()

    input_path = args.input_md
    output_path = args.output if args.output else input_path.with_suffix(".json")

    run_pipeline(
        input_md_path=input_path,
        output_json_path=output_path,
        use_llm_healing=not args.no_heal,
        use_llm_ref_detection=not args.no_llm_ref,
        api_key=args.api_key,
        model_name=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        top_p=args.top_p,
    )


if __name__ == "__main__":
    main()
