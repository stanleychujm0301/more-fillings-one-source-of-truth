"""Find how fixed_assets is matched in H-share tables."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

print("=== Searching for fixed_assets candidates in H tables ===")
for table in h_doc.tables:
    for cell in table.cells:
        raw = cell.text.strip()
        if not raw:
            continue
        # Check if any keyword in fixed_assets aliases is a substring
        entry = glossary.get_entry("fixed_assets")
        aliases = [entry.zh_cn, entry.zh_hk, entry.en] + list(entry.aliases) if entry else []
        matched = any(a and a in raw for a in aliases)
        if matched or "固定" in raw or "資產" in raw or "Y�a" in raw:
            row_cells = sorted([c for c in table.cells if c.row == cell.row], key=lambda c: c.col)
            row_text = " | ".join(f"[{c.col}]={c.text}" for c in row_cells)
            print(f"\nTable {table.table_id} page {table.page}:")
            print(f"  raw='{raw}' (bytes: {raw.encode('utf-8')})")
            print(f"  row: {row_text}")
