"""Trace where wrong H-share values come from."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts
from ahcc.align.glossary import glossary, to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

print("=== Tracing specific wrong values ===\n")

# Extract all table points
table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)
all_points = table_points + text_points

# Show all extracted points
print("--- All extracted table points ---")
for p in sorted(table_points, key=lambda x: x.canonical_key):
    print(f"  {p.canonical_key:25s} = {str(p.value):>15s}  (p{p.evidence.page}, unit={p.unit}, conf={p.confidence})")

print("\n--- All extracted text points ---")
for p in sorted(text_points, key=lambda x: x.canonical_key):
    print(f"  {p.canonical_key:25s} = {str(p.value):>15s}  (p{p.evidence.page}, unit={p.unit}, conf={p.confidence})")

# Trace specific wrong values
targets = {
    "net_profit": [41696],
    "cash_equivalents": [111155],
    "long_term_investments": [49687],
    "financing_cash_flow": [71578],
    "share_capital": [54032],
}

print("\n=== Tracing wrong values to source tables ===")
for table in doc.tables:
    rows = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    for row_idx, cells in sorted(rows.items()):
        cells_sorted = sorted(cells, key=lambda c: c.col)
        if not cells_sorted:
            continue
        label = to_simplified(cells_sorted[0].text.strip())
        if len(label) < 2:
            continue

        # Check if this row contains any target value
        for cell in cells_sorted[1:]:
            text = cell.text.strip().replace(",", "").replace(" ", "")
            try:
                val = float(text)
                for key, wrong_vals in targets.items():
                    if abs(val - wrong_vals[0]) < 0.1:
                        print(f"\n  FOUND {key}={val} in {table.table_id} (p{table.page})")
                        print(f"    Label: '{label}'")
                        print(f"    Row: {' | '.join(c.text.strip()[:40] for c in cells_sorted)}")
            except ValueError:
                pass
