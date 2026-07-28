# import pymupdf4llm

# list1= [193,194,201,202,204,
# 206,207,208,209,210,211,214,218,227,263,265,286,288,322,337,352,354,393,394,402,408,423,425,437,536]

# BASE_path = r"C:\Users\10459\Desktop\rag\incomeTax2025\sectionpdf"
# for num in list1:
#     pdf_path = fr"{BASE_path}\section{num}output.pdf"
#     output_path = fr"C:\Users\10459\Desktop\rag\incomeTax2025\MDfile\{num}section.md"


#     try:
#         md_text = pymupdf4llm.to_markdown(pdf_path)

#         with open(output_path, "w") as f:
#             f.write(md_text)
#             print(f"✔ Converted section{num}output.pdf -> {output_path}")
#     except Exception as e:
#         print(f"✘ Failed on section{num}output.pdf: {e}")



import subprocess
import os
import glob

# folder containing your .md files (e.g. 194section.md, 195section.md, etc.)
input_folder = r"C:\Users\10459\Desktop\rag\incomeTax2025\MDfile"
output_folder = r"C:\Users\10459\Desktop\rag\incomeTax2025\mdjson"  # change if you want json elsewhere
script_path = r"C:\Users\10459\Desktop\rag\incomeTax2025\mdtojson.py"

md_files = glob.glob(os.path.join(input_folder, "*.md"))

for md_file in md_files:
    base_name = os.path.splitext(os.path.basename(md_file))[0]  # e.g. "194section"
    output_path = os.path.join(output_folder, f"{base_name}.json")

    cmd = ["python", script_path, md_file, "-o", output_path]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✔ Converted {md_file} -> {output_path}")
    else:
        print(f"✘ Failed on {md_file}")
        print(result.stderr)