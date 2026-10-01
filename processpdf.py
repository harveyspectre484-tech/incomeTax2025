import subprocess
from pathlib import Path
import subprocess

BASE = Path(r"C:\Users\10459\Desktop\rag\incomeTax2025")



# print("\nAll sections completed.")
list2 = list(range(2,535))

# list1 = [19,31,36,39,46,52,58,61,63,69,70,73,193,194,201,202,204,
# 206,207,208,209,210,211,214,218,227,263,265,286 ,288,322,337,352,354,393,394,402,408,423,425,437,536]

# list3 = list(set(list2) - set(list1))

for num in list2:
    input = fr"/Users/avikalchauhan/incometax2025anti/incomeTax2025/txt_to_json/{num}section.json"

    output = fr"/Users/avikalchauhan/incometax2025anti/incomeTax2025/canonicalize_legal_json/{num}section_can.json"

    subprocess.run(["python",r"/Users/avikalchauhan/incometax2025anti/incomeTax2025/canonicalize_legal_json.py",input,  output]) 

# import subprocess

# import csv
# import subprocess
# import sys
# from pathlib import Path

# base = Path(r"C:\Users\10459\Desktop\rag\incomeTax2025")
# find_py = base / "find.py"
# out_dir = base / "find"
# out_dir.mkdir(exist_ok=True)

# kept = []      # sections that have at least one match
# empty = []     # sections with no match (CSV removed)

# for num in range(2, 535):
#     input_file = base / "txtformatnew" / f"{num}income_tax_raw.txt"
#     output_file = out_dir / f"{num}section.csv"

#     if not input_file.exists():
#         print(f"skip {num}: {input_file.name} not found")
#         continue

#     subprocess.run(
#         [sys.executable, str(find_py), str(input_file), "-o", str(output_file)],
#         check=True,
#     )

#     # count data rows (everything after the header)
#     with open(output_file, newline="", encoding="utf-8-sig") as fh:
#         rows = list(csv.reader(fh))
#     data_rows = len(rows) - 1

#     if data_rows > 0:
#         kept.append((num, data_rows))
#     else:
#         output_file.unlink()          # no rows -> don't keep the CSV
#         empty.append(num)

# print(f"\nCSV kept for {len(kept)} section(s):")
# for num, n in kept:
#     print(f"  {num}section.csv  ({n} match(es))")
# print(f"\nNo match (CSV removed) for {len(empty)} section(s)")