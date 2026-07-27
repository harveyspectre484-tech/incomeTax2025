# from pypdf import PdfReader, PdfWriter

# # Path to your source PDF
# input_path = r"/Users/avikalchauhan/Desktop/raglaw/lawpdf/Income-tax-Act-2025_2026_2026-06-10_03-46-08_691051_en.pdf"
# output_path = "/Users/avikalchauhan/Desktop/raglaw/sectionpdf/section39output.pdf"

# # Pages to keep (0-indexed here — page 1 in a PDF viewer = index 0)
# selected_pages = [55,56]   # e.g. keeps pages , 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,42, 43, 44, 45, 46, 47,442, 443, 444, 445, 446, 447

# reader = PdfReader(input_path)
# writer = PdfWriter()

# for page_num in selected_pages:
#     writer.add_page(reader.pages[page_num])

# with open(output_path, "wb") as f:
#     writer.write(f)

# print(f"Saved {len(selected_pages)} pages to {output_path}")


from pypdf import PdfReader, PdfWriter
import os

# Path to your source PDF
input_path = r"/Users/avikalchauhan/Desktop/raglaw/lawpdf/Income-tax-Act-2025_2026_2026-06-10_03-46-08_691051_en.pdf"

# Folder where the per-section PDFs will be saved
output_dir = r"/Users/avikalchauhan/Desktop/raglaw/sectionpdf"

# ---- Set your ranges here ----
# page_start / page_end are 0-indexed PDF page numbers (page 1 in a viewer = index 0)
# section_start / section_end are the section numbers you want those pages labeled as
# One page = one section, mapped in order (page_start -> section_start, page_start+1 -> section_start+1, ...)
page_start = 599
page_end = 618       # inclusive
section_start = 516
section_end = 535    # inclusive

page_range = range(page_start, page_end + 1)
section_range = range(section_start, section_end + 1)

if len(page_range) != len(section_range):
    raise ValueError(
        f"Page range has {len(page_range)} pages but section range has "
        f"{len(section_range)} sections — they must be the same length."
    )

os.makedirs(output_dir, exist_ok=True)

reader = PdfReader(input_path)

for page_num, section_num in zip(page_range, section_range):
    writer = PdfWriter()
    writer.add_page(reader.pages[page_num])

    output_path = os.path.join(output_dir, f"section{section_num}output.pdf")
    with open(output_path, "wb") as f:
        writer.write(f)

    print(f"Saved page {page_num} (viewer page {page_num + 1}) -> {output_path}")

print(f"\nDone. Saved {len(page_range)} section PDFs to {output_dir}")