"""Debug H-share extraction to find why certain values are wrong."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts, _parse_number

import io
old_stdout = sys.stdout
sys.stdout = io.StringIO()

PDF_PATH = "f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf"

print("=" * 70)
print("Parsing H-share PDF...")
doc = parse_h_pdf(PDF_PATH)
print(f"Total pages: {doc.total_pages}")
print(f"Tables: {len(doc.tables)}")
print(f"Texts: {len(doc.texts)}")
print(f"Unit: {doc.metadata.get('unit')}, Currency: {doc.metadata.get('currency')}")

# Target keys to investigate
TARGET_KEYS = [
    "total_assets",
    "goodwill",
    "fixed_assets",
    "cash_equivalents",
    "long_term_investments",
    "total_liabilities",
    "total_equity",
]

print("\n" + "=" * 70)
print("SEARCHING TABLES for target keys...")
print("=" * 70)

for table in doc.tables:
    # Build row map
    rows = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    for row_idx, cells in rows.items():
        cells_sorted = sorted(cells, key=lambda c: c.col)
        if not cells_sorted:
            continue
        label_cell = cells_sorted[0]
        raw_label = label_cell.text.strip()
        label_text = to_simplified(raw_label)

        canonical = glossary.lookup(raw_label) or glossary.lookup(label_text)
        if canonical and canonical in TARGET_KEYS:
            # Extract values from rest of row
            values = []
            for cell in cells_sorted[1:]:
                v = _parse_number(cell.text)
                if v is not None:
                    values.append((cell.text.strip(), v))
            print(f"\n[Table {table.table_id} p{table.page}] key={canonical}")
            print(f"  label: '{raw_label}' -> '{label_text}'")
            print(f"  values: {values}")

print("\n" + "=" * 70)
print("SEARCHING TEXTS for target keys...")
print("=" * 70)

for seg in doc.texts:
    if seg.section not in ("bs", "pl", "cf", "equity", "financial_statements"):
        continue
    text_simplified = to_simplified(seg.text)
    text_lower = text_simplified.lower()

    for key in TARGET_KEYS:
        entry = glossary.get_entry(key)
        if not entry:
            continue
        matched = False
        matched_label = ""
        for form in [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]:
            if not form:
                continue
            if form in text_simplified or form.lower() in text_lower:
                matched = True
                matched_label = form
                break
        if matched:
            # Find numbers nearby
            from ahcc.align.matcher import _extract_number_near_label
            val, val_text = _extract_number_near_label(text_simplified, matched_label)
            if val is not None:
                print(f"\n[Text p{seg.page} section={seg.section}] key={key}")
                print(f"  label: '{matched_label}'")
                print(f"  value: {val_text} -> {val}")
                print(f"  snippet: {seg.text[:120]}")

print("\n" + "=" * 70)
print("FULL EXTRACTION RESULTS (from matcher functions)...")
print("=" * 70)

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)

all_points = {p.canonical_key: p for p in table_points + text_points}

for key in TARGET_KEYS:
    p = all_points.get(key)
    if p:
        print(f"\n{key}:")
        print(f"  value: {p.value} (text: {p.value_text})")
        print(f"  unit: {p.unit}, currency: {p.currency}")
        print(f"  page: {p.evidence.page}, section: {p.evidence.section}")
        print(f"  snippet: {p.evidence.snippet[:100]}")
    else:
        print(f"\n{key}: NOT FOUND")

print("\n" + "=" * 70)
print("Looking for 'Total assets' / '資產總額' / '資產總計' in raw table cells...")
print("=" * 70)
for table in doc.tables[:5]:
    for cell in table.cells:
        t = cell.text.strip()
        if "資產總" in t or "Total assets" in t or "TOTAL ASSETS" in t:
            print(f"  [Table {table.table_id} p{table.page} r{cell.row} c{cell.col}] '{t}'")

# Save output to file
output = sys.stdout.getvalue()
sys.stdout = old_stdout
with open("storage/debug_h_extraction.log", "w", encoding="utf-8") as f:
    f.write(output)
print("Output saved to storage/debug_h_extraction.log")
