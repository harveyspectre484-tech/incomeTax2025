from pathlib import Path
import subprocess

BASE = Path(r"C:\Users\10459\Desktop\rag\incomeTax2025")

pdf_folder = BASE / "sectionpdf"

for pdf in sorted(pdf_folder.glob("section*output.pdf")):

    section = pdf.stem.replace("section", "").replace("output", "")

    txt = BASE /  "txtformatnew" / f"{section}income_tax_raw.txt"
    json_file = BASE /  "jsonoutputnew" / f"section{section}.json"

    print(f"\nProcessing Section {section}")

    import subprocess
    import sys

    cmd = [
        sys.executable,
        str(BASE / "newaopproch" / "rawTXT.py"),
        str(pdf),
        "-o",
        str(txt)
    ]

    print("\nRunning command:")
    print(" ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    print("Return code:", result.returncode)
    print("STDOUT:")
    print(result.stdout)
    print("STDERR:")
    print(result.stderr)

    if result.returncode != 0:
        raise RuntimeError("rawTXT.py failed")

    subprocess.run([
        "python",
        str(BASE / "income_tax_txt_to_json.py"),
        str(txt),
        "-o",
        str(json_file)
    ], check=True)

print("\nAll sections completed.")