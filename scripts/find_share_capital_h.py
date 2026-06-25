"""Find how share_capital is matched in H-share tables."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

print("=== Searching for share_capital matches in H tables ===")
for table in h_doc.tables:
    for cell in table.cells:
        raw = cell.text.strip()
        simp = to_simplified(raw)
        key_raw = glossary.lookup(raw)
        key_simp = glossary.lookup(simp)
        if key_raw == "share_capital" or key_simp == "share_capital":
            row_cells = sorted([c for c in table.cells if c.row == cell.row], key=lambda c: c.col)
            row_text = " | ".join(f"[{c.col}]={c.text}" for c in row_cells)
            print(f"\nTable {table.table_id} page {table.page}:")
            print(f"  raw_label='{raw}' (bytes: {raw.encode('utf-8')})")
            print(f"  simp_label='{simp}'")
            print(f"  key_raw={key_raw}, key_simp={key_simp}")
            print(f"  row: {row_text}")
