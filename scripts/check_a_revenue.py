"""Check A-share revenue in tables."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.glossary import glossary, to_simplified

a_path = "f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf"
a_doc = parse_a_pdf(a_path)

print("=== A-share tables with revenue ===")
for t in a_doc.tables:
    for cell in t.cells:
        raw = cell.text.strip()
        simp = to_simplified(raw)
        key = glossary.lookup(raw) or glossary.lookup(simp)
        if key == "revenue":
            row_cells = sorted([c for c in t.cells if c.row == cell.row], key=lambda c: c.col)
            row_text = " | ".join(f"[{c.col}]={c.text}" for c in row_cells)
            print(f"\nTable {t.table_id} page {t.page}:")
            print(f"  raw='{raw}', simp='{simp}'")
            print(f"  row: {row_text}")
