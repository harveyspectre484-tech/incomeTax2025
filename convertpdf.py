# import pdfplumber
# import pandas as pd

# def pdf_table_to_legal_html(pdf_path, output_html_path):
#     all_tables_html = []

#     with pdfplumber.open(pdf_path) as pdf:
#         for page_num, page in enumerate(pdf.pages):
#             # Extract raw tables based on bounding box gridlines
#             tables = page.extract_tables()
#             for t_idx, table in enumerate(tables):
#                 if not table or len(table) < 2:
#                     continue
                
#                 headers = table[0]
#                 rows = table[1:]
                
#                 # Convert table structure to Pandas DataFrame
#                 df = pd.DataFrame(rows, columns=headers)
                
#                 # Render to clean HTML table (escape=False allows preserving <em> tags)
#                 table_html = df.to_html(index=False, classes="legal-table", escape=False)
#                 all_tables_html.append(f"<h3>Page {page_num + 1} Table</h3>\n" + table_html)

#     # HTML document with Legal Styling (Bluebook hanging indents + Statutory blocks)
#     css_style = """
#     <style>
#         table.legal-table { width: 100%; border-collapse: collapse; font-family: Georgia, serif; margin-bottom: 20px; }
#         table.legal-table th { background-color: #1b2a4a; color: white; padding: 8px; text-align: left; }
#         table.legal-table td { border: 1px solid #cbd5e1; padding: 8px; vertical-align: top; }
#         table.legal-table tr:nth-child(even) td { background-color: #f8fafc; }
#         .hanging-indent { padding-left: 2em; text-indent: -2em; }
#         .case-name { font-style: italic; }
#         .statutory-block { margin-left: 1em; border-left: 2px solid #cbd5e1; padding-left: 6px; font-style: italic; color: #334155; }
#     </style>
#     """

#     full_html = f"<!DOCTYPE html><html><head>{css_style}</head><body>" + "".join(all_tables_html) + "</body></html>"

#     with open(output_html_path, "w", encoding="utf-8") as f:
#         f.write(full_html)

# # Run extraction
# pdf_table_to_legal_html(r"C:\Users\10459\Desktop\rag\incomeTax2025\sectionpdf\section194output.pdf", r"C:\Users\10459\Desktop\rag\incomeTax2025\194legal_tables.html")


import camelot

# Stream mode uses whitespace gaps between columns instead of lines
tables = camelot.read_pdf(
    r"C:\Users\10459\Desktop\rag\incomeTax2025\sectionpdf\section194output.pdf", 
    flavor='stream'
)

# Export directly to HTML
tables[0].to_html(r"C:\Users\10459\Desktop\rag\incomeTax2025\194legal_tables.html")

# import pdfplumber
# import pandas as pd

# def extract_exact_5column_table(pdf_path, output_html_path):
#     # Standard A4 page width is ~595 points. 
#     # Define exact X-coordinates for the 5 legal columns:
#     # Col A: Sl. No. | Col B: Assessee | Col C: Income | Col D: Rate | Col E: Conditions
#     table_settings = {
#         "vertical_strategy": "explicit",
#         "explicit_vertical_lines": [40, 75, 175, 330, 390, 560],
#         "horizontal_strategy": "text",
#         "snap_tolerance": 3,
#         "join_tolerance": 3,
#     }

#     cleaned_table_rows = []

#     with pdfplumber.open(pdf_path) as pdf:
#         for page in pdf.pages:
#             # Extract table using fixed X bounding boxes
#             tables = page.extract_tables(table_settings)
            
#             for table in tables:
#                 for row in table:
#                     # Clean up broken newlines within each cell
#                     cleaned_row = [" ".join(cell.split()) if cell else "" for cell in row]
                    
#                     # Filter out non-table text (like introductory/outro sub-sections)
#                     # We only keep rows where column A has Sl. No / letters OR data exists
#                     if any(cleaned_row):
#                         cleaned_table_rows.append(cleaned_row)

#     # Reconstruct exact HTML table
#     if cleaned_table_rows:
#         # First valid row as header
#         headers = ["Sl. No.", "Income Assessee", "Income", "Rate of tax", "Conditions"]
#         df = pd.DataFrame(cleaned_table_rows, columns=headers)

#         # Drop headers/metadata rows that got caught inside the table body
#         df = df[~df["Sl. No."].str.contains("Tax on|194|below|Irrespective", case=False, na=False)]

#         table_html = df.to_html(index=False, classes="legal-table", escape=False)

#         # Apply CSS for legal table
#         css = """<style>
#             table.legal-table { width: 100%; border-collapse: collapse; font-family: Georgia, serif; }
#             table.legal-table th { background-color: #1b2a4a; color: white; padding: 10px; border: 1px solid #1b2a4a; }
#             table.legal-table td { border: 1px solid #cbd5e1; padding: 8px 10px; vertical-align: top; }
#             table.legal-table tr:nth-child(even) td { background-color: #f8fafc; }
#         </style>"""

#         full_html = f"<!DOCTYPE html><html><head>{css}</head><body>{table_html}</body></html>"

#         with open(output_html_path, "w", encoding="utf-8") as f:
#             f.write(full_html)
#         print("Successfully extracted clean 5-column table!")

# # Run script
# extract_exact_5column_table(
#     r"C:\Users\10459\Desktop\rag\incomeTax2025\sectionpdf\section194output.pdf",
#     r"C:\Users\10459\Desktop\rag\incomeTax2025\194legal_tables_clean.html"
# )