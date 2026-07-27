from pypdf import PdfReader, PdfWriter

# Path to your source PDF
input_path = r"/Users/avikalchauhan/Desktop/raglaw/lawpdf/Income-tax-Act-2025_2026_2026-06-10_03-46-08_691051_en.pdf"
output_path = r"/Users/avikalchauhan/Desktop/raglaw/sectionpdf/sechedule16output.pdf"

# Pages to keep (0-indexed here — page 1 in a PDF viewer = index 0)
selected_pages = [664,665]   # e.g. keeps pages , 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,42, 43, 44, 45, 46, 47,442, 443, 444, 445, 446, 447

reader = PdfReader(input_path)
writer = PdfWriter()

for page_num in selected_pages:
    writer.add_page(reader.pages[page_num])

with open(output_path, "wb") as f:
    writer.write(f)

print(f"Saved {len(selected_pages)} pages to {output_path}")