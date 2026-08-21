"""
Unified Legal Reference Pipeline (pipeline.py)

Executes the complete 3-step pipeline:
1. Markdown to AST JSON parsing (mdtojson.py)
2. LLM AST Structure Verification & Healing (llm_structure_checker.py)
3. Cross-Reference Detection & Annotation (reference_detector.py)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mdtojson import parse_markdown_to_ast
from llm_structure_checker import check_and_heal_ast
from reference_detector import process_document


def run_pipeline(
    input_md_path: Path,
    output_json_path: Path,
    use_llm_healing: bool = True,
    use_llm_ref_detection: bool = True,
    model_name: str = "gemini-1.5-flash",
) -> None:
    print(f"=== Step 1: Parsing Markdown ({input_md_path.name}) to AST JSON ===")
    md_content = input_md_path.read_text(encoding="utf-8")
    ast_json = parse_markdown_to_ast(md_content)
    print(f"  └─ Generated initial AST with {len(ast_json.get('sections', []))} section(s).")

    print("\n=== Step 2: LLM Structural Verification & Healing ===")
    if use_llm_healing:
        healed_ast = check_and_heal_ast(input_md_path, ast_json, model_name=model_name)
    else:
        print("  └─ Skipping LLM structural healing (--no-heal passed).")
        healed_ast = ast_json

    print("\n=== Step 3: Reference Detection & Annotation ===")
    final_ast = process_document(healed_ast, use_llm=use_llm_ref_detection, model_name=model_name)
    
    output_json_path.write_text(json.dumps(final_ast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[Pipeline Complete] Output successfully written to: {output_json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Legal AST & Reference Detection Pipeline")
    parser.add_argument("input_md", type=Path, help="Input legal Markdown file (act_section.md)")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output JSON file path (act_section.json)")
    parser.add_argument("--no-heal", action="store_true", help="Disable LLM AST structural healing")
    parser.add_argument("--no-llm-ref", action="store_true", help="Disable LLM reference fallback")
    parser.add_argument("--model", type=str, default="gemini-1.5-flash", help="Gemini model name")

    args = parser.parse_args()

    input_path = args.input_md
    output_path = args.output if args.output else input_path.with_suffix(".json")

    run_pipeline(
        input_md_path=input_path,
        output_json_path=output_path,
        use_llm_healing=not args.no_heal,
        use_llm_ref_detection=not args.no_llm_ref,
        model_name=args.model,
    )


if __name__ == "__main__":
    main()
