"""Inspect specific H-share pages for missing metrics."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

# Inspect pages 20-25 and 36-42
for page in [20, 21, 22, 23, 24, 25, 36, 37, 38, 39, 40, 41, 42]:
    tables = [t for t in h_doc.tables if t.page == page]
    if not tables:
        continue
    print(f"\n=== Page {page} ===")
    for t in tables:
        print(f"\nTable {t.table_id}:")
        rows = {}
        for c in t.cells:
            rows.setdefault(c.row, []).append(c)
        for ri in sorted(rows):
            row_cells = sorted(rows[ri], key=lambda c: c.col)
            row_text = " | ".join(f"[{c.col}]={c.text}" for c in row_cells)
            print(f"  Row {ri}: {row_text}")
