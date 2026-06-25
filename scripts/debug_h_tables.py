"""Inspect raw H-share table contents."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf

PDF_PATH = "f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf"

doc = parse_h_pdf(PDF_PATH)

with open("storage/debug_h_tables.log", "w", encoding="utf-8") as f:
    for table in doc.tables:
        f.write(f"\n{'='*70}\n")
        f.write(f"Table: {table.table_id} | Page: {table.page} | Title: {table.title.zh or table.title.en}\n")
        f.write(f"Cells: {len(table.cells)}\n")
        f.write("-"*70 + "\n")

        # Build row map
        rows = {}
        for cell in table.cells:
            rows.setdefault(cell.row, []).append(cell)

        for r in sorted(rows.keys()):
            cells = sorted(rows[r], key=lambda c: c.col)
            row_text = " | ".join(f"[{c.col}]{c.text}" for c in cells)
            f.write(f"  r{r}: {row_text}\n")

        # Check if any BS keywords appear
        bs_keywords = ["資產", "負債", "權益", "資產總", "負債總", "Total assets", "Total liabilities"]
        has_bs = any(kw in c.text for c in table.cells for kw in bs_keywords)
        if has_bs:
            f.write("*** CONTAINS BS KEYWORDS ***\n")

print(f"Dumped {len(doc.tables)} tables to storage/debug_h_tables.log")
