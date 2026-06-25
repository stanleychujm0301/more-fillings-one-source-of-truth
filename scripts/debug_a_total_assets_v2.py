"""Debug A-share total_assets extraction - v2."""
import sys, io
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.glossary import glossary, to_simplified

doc = parse_a_pdf("f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf")

output_lines = []
output_lines.append(f"A-share unit: {doc.metadata.get('unit')}")
output_lines.append(f"A-share currency: {doc.metadata.get('currency')}")
output_lines.append(f"A-share tables: {len(doc.tables)}")
output_lines.append(f"A-share texts: {len(doc.texts)}")
output_lines.append("")

# Search ALL tables for total_assets
output_lines.append("=== ALL tables with '资产总计' / '总资产' / 'Total assets' ===")
found_in_tables = []
for table in doc.tables:
    for cell in table.cells:
        t = cell.text.strip()
        if "资产总计" in t or "总资产" in t or "Total assets" in t:
            found_in_tables.append(table)
            output_lines.append(f"\n[Table {table.table_id} p{table.page} r{cell.row} c{cell.col}] '{t}'")
            row_cells = [c for c in table.cells if c.row == cell.row]
            row_cells = sorted(row_cells, key=lambda c: c.col)
            output_lines.append(f"  Row: {' | '.join(c.text.strip() for c in row_cells)}")
            break  # one match per table

output_lines.append(f"\nFound in {len(found_in_tables)} tables")

# Search texts with more context
output_lines.append("\n=== Text segments with '资产总计' ===")
for seg in doc.texts:
    if "资产总计" in seg.text:
        output_lines.append(f"\nPage {seg.page} section={seg.section}:")
        # Extract lines containing 资产总计
        for line in seg.text.split('\n'):
            if "资产总计" in line:
                output_lines.append(f"  LINE: {line.strip()}")

# Also check what the matcher would extract
output_lines.append("\n=== Simulating matcher extraction ===")
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts

table_points = _extract_from_tables(doc)
output_lines.append(f"Table points: {len(table_points)}")
for p in table_points:
    if p.canonical_key == "total_assets":
        output_lines.append(f"  total_assets from TABLE: value={p.value} text='{p.value_text}' page={p.evidence.page} snippet={p.evidence.snippet}")

text_points = _extract_from_texts(doc)
output_lines.append(f"Text points: {len(text_points)}")
for p in text_points:
    if p.canonical_key == "total_assets":
        output_lines.append(f"  total_assets from TEXT: value={p.value} text='{p.value_text}' page={p.evidence.page} snippet={p.evidence.snippet[:100]}")

output = "\n".join(output_lines)
with open("storage/debug_a_total_assets_v2.log", "w", encoding="utf-8") as f:
    f.write(output)
print("Saved to storage/debug_a_total_assets_v2.log")
