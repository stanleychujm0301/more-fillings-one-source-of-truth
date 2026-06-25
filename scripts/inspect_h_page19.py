"""Inspect H-share page 19 tables and text."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

print("=== Tables on page 19 ===")
for t in h_doc.tables:
    if t.page == 19:
        print(f"\nTable {t.table_id}:")
        rows = {}
        for c in t.cells:
            rows.setdefault(c.row, []).append(c)
        for ri in sorted(rows):
            row_cells = sorted(rows[ri], key=lambda c: c.col)
            row_text = " | ".join(f"[{c.col}]={c.text}" for c in row_cells)
            print(f"  Row {ri}: {row_text}")

print("\n=== Text segments on page 19 ===")
for txt in h_doc.texts:
    if txt.page == 19:
        print(f"  {txt.segment_id}: {txt.text[:200]}")
