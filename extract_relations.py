import os
import re
import json
import argparse
from pathlib import Path

def extract_section_number(filename):
    """Extracts numeric section number from filename like '204section.md' or '2.section.md'."""
    match = re.search(r'(\d+)', filename)
    return int(match.group(1)) if match else 0

def extract_references_from_text(text, source_section=""):
    """
    Parses markdown text and extracts all cross-references to:
    - Sections (e.g. section 203, section 205(4), sections 146 or 150)
    - Subsections (e.g. sub-section (1), sub-section (3)(b))
    - Clauses & Sub-clauses (e.g. clause (22)(iii)(A), sub-clause (c))
    - Items (e.g. item (ii), item (A))
    - Chapters & Parts (e.g. Chapter VIII, Chapter XIX-C, Part A, B, E)
    - Schedules (e.g. Schedule XI, Schedule II)
    - Tables (e.g. Table: Sl. No. 1.B, Table: Sl. Nos. 2, 3 and 4)
    - External Acts (e.g. Companies Act, 2013, Securities and Exchange Board of India Act, 1992)
    """

    # Clean up inline html comments/tags if needed
    clean_txt = re.sub(r'<[^>]+>', ' ', text)
    clean_txt = re.sub(r'\s+', ' ', clean_txt)

    # 1. External Acts vs Income-tax Act sections
    external_act_pattern = re.compile(
        r'section\s+\d+[\w()]*\s+of\s+the\s+([A-Z][A-Za-z\s,]+(?:Act|Scheme|Rules),\s*\d{4}(?:\s*\(\d+\s+of\s+\d{4}\))?)',
        re.IGNORECASE
    )
    external_acts = []
    for match in external_act_pattern.finditer(clean_txt):
        external_acts.append(match.group(0))

    # 2. Section references (Income-tax Act)
    # Matches: section 203, sections 146 or 150, section 205(1)(a) to (g), section 149(2)(d)(ii)
    section_pattern = re.compile(
        r'\bsections?\s+(\d+[A-Z]?(?:\(\d+\))?(?:\([a-z]+\))?(?:\([ivx]+\))?(?:\([A-Z]+\))?'
        r'(?:\s*(?:and|or|to|,)\s*\d+[A-Z]?(?:\(\d+\))?)*)',
        re.IGNORECASE
    )
    sections_raw = []
    for match in section_pattern.finditer(clean_txt):
        full_match = match.group(0)

        # Skip if it refers to another Act (handled in external_acts)
        if " of the " in full_match.lower() and "act" in full_match.lower() and "this act" not in full_match.lower():
            continue

        # Extract target section numbers
        sec_nums = re.findall(r'\b\d+[A-Z]?\b', full_match)
        sections_raw.append({
            "text": full_match,
            "target_sections": sec_nums
        })

    # 3. Subsection references
    # Matches: sub-section (1), sub-sections (12) and (13), sub-section (3)(b)
    subsection_pattern = re.compile(
        r'\bsub-sections?\s*(\(\d+\)(?:\s*(?:and|or|to|,)\s*\(\d+\))*|\d+[\w()]*)',
        re.IGNORECASE
    )
    subsections = [m.group(0) for m in subsection_pattern.finditer(clean_txt)]

    # 4. Clauses & Sub-clauses
    clause_pattern = re.compile(
        r'\b(?:sub-)?clauses?\s*(\([a-z0-9]+\)(?:\([ivx]+\))?(?:\([A-Z]+\))?(?:\s*(?:and|or|to|,)\s*\([a-z0-9]+\))*)',
        re.IGNORECASE
    )
    clauses = [m.group(0) for m in clause_pattern.finditer(clean_txt)]

    # 5. Items
    item_pattern = re.compile(
        r'\bitems?\s*(\([ivx]+\)|\([A-Z]+\)|\d+)',
        re.IGNORECASE
    )
    items = [m.group(0) for m in item_pattern.finditer(clean_txt)]

    # 6. Chapters & Parts
    chapter_pattern = re.compile(
        r'\bChapter\s+([IVXLCDM\dA-Z-]+)\b',
        re.IGNORECASE
    )
    chapters = [m.group(0) for m in chapter_pattern.finditer(clean_txt)]

    part_pattern = re.compile(
        r'\bPart\s+([A-Z](?:\s*,\s*[A-Z])*(?:\s+and\s+this\s+Part)?)',
        re.IGNORECASE
    )
    parts = [m.group(0) for m in part_pattern.finditer(clean_txt)]

    # 7. Schedules
    schedule_pattern = re.compile(
        r'\bSchedules?\s+([IVXLCDM\d+]+\b(?:\s*\(\s*Table:\s*Sl\.\s*Nos?\.\s*[\w\d.,\s]+\s*\))?)',
        re.IGNORECASE
    )
    schedules = [m.group(0).strip() for m in schedule_pattern.finditer(clean_txt)]

    # 8. Tables
    table_pattern = re.compile(
        r'\b(?:Table:\s*Sl\.\s*Nos?\.\s*[\w\d.,\s]+?|\bTable\b|column\s+[A-Z]\s+of\s+the\s+said\s+Table)\b',
        re.IGNORECASE
    )
    tables_raw = [m.group(0).strip() for m in table_pattern.finditer(clean_txt)]
    # Clean trailing punctuation from table strings
    tables = [re.sub(r'[\s).,;:]+$', '', t) for t in tables_raw]


    # Deduplicate while maintaining order
    def unique(lst):
        seen = set()
        out = []
        for x in lst:
            key = json.dumps(x) if isinstance(x, dict) else x
            if key not in seen:
                seen.add(key)
                out.append(x)
        return out

    # Unique list of target sections referenced
    target_sec_set = set()
    for item in sections_raw:
        for s in item["target_sections"]:
            if str(s) != str(source_section):  # exclude self reference
                target_sec_set.add(s)

    return {
        "source_section": source_section,
        "referenced_sections": sorted(list(target_sec_set), key=lambda x: int(re.search(r'\d+', x).group())),
        "section_references_detail": unique(sections_raw),
        "subsections_referenced": unique(subsections),
        "clauses_referenced": unique(clauses),
        "items_referenced": unique(items),
        "chapters_referenced": unique(chapters),
        "parts_referenced": unique(parts),
        "schedules_referenced": unique(schedules),
        "tables_referenced": unique(tables),
        "external_acts_referenced": unique(external_acts)
    }

def analyze_folder(folder_path, target_section=None):
    folder = Path(folder_path)
    if not folder.exists():
        print(f"Error: Folder '{folder_path}' does not exist.")
        return

    md_files = [f for f in folder.glob("*.md")]
    md_files.sort(key=lambda p: extract_section_number(p.name))

    results = {}
    incoming_references = {} # Maps section_X -> list of sections that reference section_X

    for file_path in md_files:
        sec_num = extract_section_number(file_path.name)
        if target_section and int(target_section) != sec_num:
            continue

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        ref_data = extract_references_from_text(content, source_section=sec_num)
        results[sec_num] = ref_data

        # Populate incoming references
        for ref_sec in ref_data["referenced_sections"]:
            incoming_references.setdefault(ref_sec, []).append(sec_num)

    return results, incoming_references

def print_section_report(sec_num, data, incoming):
    print("=" * 70)
    print(f" RELATION REPORT FOR SECTION {sec_num}")
    print("=" * 70)

    print(f"\nOUTGOING REFERENCES (Section {sec_num} refers to):")
    if data["referenced_sections"]:
        print(f"   • Target Sections: {', '.join(['Section ' + str(s) for s in data['referenced_sections']])}")
    else:
        print("   • Target Sections: None")

    if data["section_references_detail"]:
        print("\n   [Detailed Section Mentions]:")
        for d in data["section_references_detail"]:
            print(f"     - {d['text']}")

    if data["subsections_referenced"]:
        print(f"\n   [Subsections Referenced]: {', '.join(data['subsections_referenced'])}")

    if data["clauses_referenced"]:
        print(f"   [Clauses/Sub-clauses]:    {', '.join(data['clauses_referenced'])}")

    if data["items_referenced"]:
        print(f"   [Items]:                  {', '.join(data['items_referenced'])}")

    if data["chapters_referenced"]:
        print(f"   [Chapters]:               {', '.join(data['chapters_referenced'])}")

    if data["parts_referenced"]:
        print(f"   [Parts]:                  {', '.join(data['parts_referenced'])}")

    if data["schedules_referenced"]:
        print(f"   [Schedules]:              {', '.join(data['schedules_referenced'])}")

    if data["tables_referenced"]:
        print(f"   [Tables / Columns]:       {', '.join(data['tables_referenced'])}")

    if data["external_acts_referenced"]:
        print(f"\n   [External Acts]:")
        for act in data["external_acts_referenced"]:
            print(f"     - {act}")

    inc = incoming.get(str(sec_num), []) or incoming.get(sec_num, [])
    print(f"\nINCOMING REFERENCES (Sections that refer to Section {sec_num}):")
    if inc:
        print(f"   • Referenced by Sections: {', '.join(['Section ' + str(s) for s in inc])}")
    else:
        print("   • Referenced by: None")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract relations and references in Income Tax Act 2025 MD files.")
    parser.add_argument("--folder", default=r"c:\Users\10459\Desktop\rag\incomeTax2025\MDfileadobe", help="Path to markdown folder")
    parser.add_argument("--section", default="204", help="Section number to analyze (e.g. 204 or 'all')")
    parser.add_argument("--export", help="Export full relation graph to JSON file (e.g. relations.json)")

    args = parser.parse_args()

    target_sec = None if args.section.lower() == 'all' else args.section

    results, incoming = analyze_folder(args.folder, target_sec if target_sec else None)

    if target_sec and int(target_sec) in results:
        # If analyzing single section, recalculate full incoming references graph first
        full_results, full_incoming = analyze_folder(args.folder, None)
        print_section_report(int(target_sec), full_results[int(target_sec)], full_incoming)
    else:
        # Summary for all sections
        print(f"Analyzed {len(results)} section markdown files in '{args.folder}'.")
        print("\nTop referenced sections across the Income Tax Act:")
        
        # Calculate full incoming graph
        full_results, full_incoming = analyze_folder(args.folder, None)
        sorted_incoming = sorted(full_incoming.items(), key=lambda x: len(x[1]), reverse=True)
        
        for sec, callers in sorted_incoming[:15]:
            print(f"  • Section {sec} is referenced by {len(callers)} other section(s): {callers[:8]}...")

        if args.export:
            export_data = {
                "sections": full_results,
                "incoming_references": full_incoming
            }
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)
            print(f"\nFull relations graph exported to: {args.export}")
