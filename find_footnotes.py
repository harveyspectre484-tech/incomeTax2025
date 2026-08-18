import os
import re
import sys

# ==============================================================================
# SET YOUR FOLDER PATH HERE (or pass as command-line argument)
# ==============================================================================
FOLDER_PATH = r"c:\Users\10459\Desktop\rag\incomeTax2025\MDfileadobe"


def detect_footnotes(file_path):
    """
    Checks if a markdown file contains footnotes.
    Detects:
    1. Standard Markdown footnotes: [^1], [^note], [^1]:
    2. Legal/Act footnotes: '1. Ins. by...', '27. Sub. for...', '15. Omtt. by...', 'w.e.f.', etc.
    3. Footnote reference tags in text: e.g., 27[...] or [1]
    4. Footnote headings / section indicators
    """
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    patterns = [
        # Standard Markdown footnotes: [^1] or [^fn]:
        r'\[\^[^\]]+\]',
        
        # Legal Act footnote lines: "1. Ins. by...", "27. Sub. for...", "3. Omtt. by...", "w.e.f."
        r'^\s*\d+\.\s+(?:Ins\.|Sub\.|Omtt\.|Subs\.|Words|Clauses|Sections|Omitted|Inserted|Substituted|Prior to)',
        r'^\s*\d+\.\s+.*?\b(?:Act No\.|w\.e\.f\.)',
        
        # In-text numbered bracket references like 27[...], 38[...]
        r'\b\d+\[[^\]]+\]',
        
        # Explicit Footnote headers or HTML elements
        r'#+\s*Footnotes?',
        r'<(?:fn|footnote)[^>]*>'
    ]

    combined_regex = re.compile('|'.join(patterns), re.MULTILINE | re.IGNORECASE)
    
    matches = combined_regex.findall(content)
    return len(matches) > 0, matches


def main(folder_path):
    if not os.path.exists(folder_path):
        print(f"Error: Folder path '{folder_path}' does not exist.")
        return

    # Collect and sort files (supporting numeric sort like 2.section.md, 3.section.md, etc.)
    def extract_num(filename):
        num = re.findall(r'\d+', filename)
        return int(num[0]) if num else filename

    files = [f for f in os.listdir(folder_path) if f.endswith('.md')]
    files.sort(key=extract_num)

    files_with_footnotes = []

    print(f"Scanning {len(files)} markdown files in '{folder_path}'...\n")

    for filename in files:
        file_path = os.path.join(folder_path, filename)
        has_fn, matches = detect_footnotes(file_path)
        if has_fn:
            files_with_footnotes.append((filename, len(matches)))
            print(f"[FOOTNOTE FOUND] {filename} -> {len(matches)} footnote marker(s) detected")

    print("\n" + "="*50)
    print(f"Summary: {len(files_with_footnotes)} out of {len(files)} files contain footnotes.")
    print("="*50)


if __name__ == "__main__":
    # Uses command-line argument if provided, otherwise defaults to FOLDER_PATH
    target = sys.argv[1] if len(sys.argv) > 1 else FOLDER_PATH
    main(target)

