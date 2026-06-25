"""Debug A-share total_assets extraction."""
import sys, io
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.glossary import glossary

old_stdout = sys.stdout
sys.stdout = io.StringIO()

doc = parse_a_pdf("f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf")

print(f"A-share unit: {doc.metadata.get('unit')}")
print(f"A-share tables: {len(doc.tables)}")

# Search for total_assets in tables
for table in doc.tables[:20]:
    for cell in table.cells:
        t = cell.text.strip()
        if "资产总计" in t or "总资产" in t or "Total assets" in t:
            print(f"\n[Table {table.table_id} p{table.page} r{cell.row} c{cell.col}] '{t}'")
            row_cells = [c for c in table.cells if c.row == cell.row]
            row_cells = sorted(row_cells, key=lambda c: c.col)
            print(f"  Row: {' | '.join(c.text for c in row_cells)}")

# Search in texts too
print("\n--- Text segments with '资产总计' ---")
for seg in doc.texts:
    if "资产总计" in seg.text:
        print(f"Page {seg.page} section={seg.section}: {seg.text[:200]}")

output = sys.stdout.getvalue()
sys.stdout = old_stdout
with open("storage/debug_a_total_assets.log", "w", encoding="utf-8") as f:
    f.write(output)
print("Saved to storage/debug_a_total_assets.log")
