import subprocess
from pathlib import Path
import subprocess

BASE = Path(r"C:\Users\10459\Desktop\rag\incomeTax2025")

# # pdf_folder = BASE / "sectionpdf"

# # for pdf in sorted(pdf_folder.glob("section*output.pdf")):

# #     section = pdf.stem.replace("section", "").replace("output", "")

# #     txt = BASE /  "txtformatnew" / f"{section}income_tax_raw.txt"
# #     json_file = BASE /  "jsonoutputnew" / f"section{section}.json"

# #     print(f"\nProcessing Section {section}")

# #     import subprocess
# #     import sys

# #     cmd = [
# #         sys.executable,
# #         str(BASE / "newaopproch" / "rawTXT.py"),
# #         str(pdf),
# #         "-o",
# #         str(txt)
# #     ]

# #     print("\nRunning command:")
# #     print(" ".join(cmd))

# #     result = subprocess.run(
# #         cmd,
# #         capture_output=True,
# #         text=True
# #     )

# #     print("Return code:", result.returncode)
# #     print("STDOUT:")
# #     print(result.stdout)
# #     print("STDERR:")
# #     print(result.stderr)

# #     if result.returncode != 0:
# #         raise RuntimeError("rawTXT.py failed")

# #     subprocess.run([
# #         "python",
# #         str(BASE / "income_tax_txt_to_json.py"),
# #         str(txt),
# #         "-o",
# #         str(json_file)
# #     ], check=True)

# print("\nAll sections completed.")
list2 = list(range(3,536))

# list1 = [19,31,36,39,46,52,58,61,63,69,70,73,193,194,201,202,204,
# 206,207,208,209,210,211,214,218,227,263,265,286 ,288,322,337,352,354,393,394,402,408,423,425,437,536]

# list3 = list(set(list2) - set(list1))

for num in list2:
    input = fr"/Users/avikalchauhan/incometax2025anti/incomeTax2025/txt_to_json/{num}section.json"

    output = fr"/Users/avikalchauhan/incometax2025anti/incomeTax2025/canonicalize_legal_json/{num}section_can.json"

    subprocess.run(["python",r"/Users/avikalchauhan/incometax2025anti/incomeTax2025/canonicalize_legal_json.py",input,  output]) 

# import subprocess

# cmd = [
#     "python3",
#     "/Users/avikalchauhan/Documents/ChatGPT/legal RAG/tools/validate_canonical_units.py",
#     "--raw-dir",
#     "/Users/avikalchauhan/incometax2025anti/incomeTax2025/txt_to_json",
#     "--canonical-dir",
#     "/Users/avikalchauhan/incometax2025anti/incomeTax2025/canonicalize_legal_json",
#     "--report",
#     "/Users/avikalchauhan/incometax2025anti/incomeTax2025/canonical_validation_report1.csv",
# ]

# subprocess.run(cmd, check=True)