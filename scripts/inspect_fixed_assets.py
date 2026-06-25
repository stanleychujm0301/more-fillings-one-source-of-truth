"""Inspect fixed_assets extraction for both A and H shares."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts
from ahcc.align.glossary import resolve_by_zh

def lookup_canonical(text):
    return resolve_by_zh(text)

a_path = "f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf"
h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

print("=== A-share: all fixed_assets candidates ===")
a_doc = parse_a_pdf(a_path)
for t in a_doc.tables:
    for cell in t.cells:
        key = lookup_canonical(cell.text)
        if key == "fixed_assets":
            row_cells = [c for c in t.cells if c.row == cell.row]
            row_text = " | ".join(sorted(set(f"[{c.col}]={c.text}" for c in row_cells), key=lambda x: int(x.split("=")[0].strip("[]"))))
            print(f"  Table {t.table_id} page {t.page}: {row_text}")

for txt in a_doc.texts:
    key = lookup_canonical(txt.text[:40])
    if key == "fixed_assets":
        print(f"  Text {txt.segment_id} page {txt.page}: {txt.text[:100]}")

print("\n=== H-share: all fixed_assets candidates ===")
h_doc = parse_h_pdf(h_path)
for t in h_doc.tables:
    for cell in t.cells:
        key = lookup_canonical(cell.text)
        if key == "fixed_assets":
            row_cells = [c for c in t.cells if c.row == cell.row]
            row_text = " | ".join(sorted(set(f"[{c.col}]={c.text}" for c in row_cells), key=lambda x: int(x.split("=")[0].strip("[]"))))
            print(f"  Table {t.table_id} page {t.page}: {row_text}")

for txt in h_doc.texts:
    key = lookup_canonical(txt.text[:40])
    if key == "fixed_assets":
        print(f"  Text {txt.segment_id} page {txt.page}: {txt.text[:100]}")
