"""Debug why revenue is not extracted from H-share tables."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified
from ahcc.align.matcher import _extract_from_tables, _find_first_number_in_row

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

print(f"Total tables: {len(doc.tables)}")
print()

# Find tables that contain "营业收入"
for table in doc.tables:
    if "p19" not in table.table_id and "p20" not in table.table_id and "p21" not in table.table_id:
        continue

    print(f"\n=== {table.table_id} (p{table.page}) ===")

    rows = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    for row_idx, cells in sorted(rows.items()):
        cells_sorted = sorted(cells, key=lambda c: c.col)
        if not cells_sorted:
            continue

        raw_label = cells_sorted[0].text.strip()
        label_text = to_simplified(raw_label)

        # Check if this row matches revenue or other key terms
        canonical = glossary.lookup(raw_label) or glossary.lookup(label_text)
        if canonical:
            val, val_text = _find_first_number_in_row(cells_sorted[1:])
            print(f"  MATCH [{canonical:20s}] label='{label_text}' raw='{raw_label}' -> val={val} (text='{val_text}')")
        elif "营业" in label_text or "收入" in label_text or "利润" in label_text:
            val, val_text = _find_first_number_in_row(cells_sorted[1:])
            print(f"  NO_MATCH              label='{label_text}' raw='{raw_label}' -> val={val} (text='{val_text}')")
