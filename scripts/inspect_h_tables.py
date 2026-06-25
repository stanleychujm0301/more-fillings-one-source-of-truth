"""Inspect H-share tables for Everbright Bank to diagnose extraction errors."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

doc = parse_h_pdf(h_path)

print(f"H-share: {doc.total_pages} pages, {len(doc.tables)} tables")
print(f"Unit: {doc.metadata.get('unit')}, Currency: {doc.metadata.get('currency')}")
print()

# Keywords to search for
keywords = ["长期股权投资", "现金及现金等价物", "筹资活动", "投资活动", "营业收入", "股本", "无形资产", "预计负债"]

for table in doc.tables:
    # Check if table contains any keyword in first column
    rows = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    found = False
    for row_idx, cells in sorted(rows.items()):
        cells_sorted = sorted(cells, key=lambda c: c.col)
        if not cells_sorted:
            continue
        label = to_simplified(cells_sorted[0].text.strip())
        for kw in keywords:
            if kw in label:
                found = True
                row_data = " | ".join(f"[{c.col}] {c.text.strip()[:30]}" for c in cells_sorted)
                print(f"Table {table.table_id} (p{table.page}): {row_data}")
                break
        if found:
            break

print("\n=== All tables with numeric rows ===")
for table in doc.tables[:30]:
    rows = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    numeric_rows = 0
    for row_idx, cells in sorted(rows.items()):
        cells_sorted = sorted(cells, key=lambda c: c.col)
        label = to_simplified(cells_sorted[0].text.strip()) if cells_sorted else ""
        vals = [c.text.strip() for c in cells_sorted[1:] if c.text.strip()]
        # Check if any value looks like a number
        has_num = any(any(ch.isdigit() for ch in v) for v in vals)
        if has_num and len(label) > 1:
            numeric_rows += 1

    if numeric_rows > 0:
        print(f"\n--- {table.table_id} (p{table.page}, title={table.title.zh or table.title.en or ''}) ---")
        for row_idx, cells in sorted(rows.items())[:10]:
            cells_sorted = sorted(cells, key=lambda c: c.col)
            label = to_simplified(cells_sorted[0].text.strip()) if cells_sorted else ""
            vals = [c.text.strip() for c in cells_sorted[1:] if c.text.strip()]
            if vals:
                print(f"  {label[:20]:20s} -> {vals[:4]}")
