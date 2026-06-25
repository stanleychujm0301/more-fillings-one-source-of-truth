"""Test pdfplumber on H-share financial pages."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

import pdfplumber

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

with pdfplumber.open(h_path) as pdf:
    # Try page 38 (fixed_assets page)
    page = pdf.pages[37]
    print("=== Page 38 text ===")
    text = page.extract_text() or ""
    for line in text.split("\n")[:30]:
        print(line)

    print("\n=== Page 38 tables ===")
    tables = page.extract_tables()
    for ti, table in enumerate(tables):
        print(f"\nTable {ti}:")
        for ri, row in enumerate(table[:10]):
            print(f"  Row {ri}: {row}")

    # Try page 19 (main financial summary)
    page = pdf.pages[18]
    print("\n=== Page 19 text (first 20 lines) ===")
    text = page.extract_text() or ""
    for line in text.split("\n")[:20]:
        print(line)
