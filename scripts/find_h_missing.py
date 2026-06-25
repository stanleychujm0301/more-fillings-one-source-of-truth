"""Find missing H-share metrics in all tables."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

# Search terms we care about
search_terms = ["现金", "經營活動", "在建", "無形", "準備", "撥備", "减值", "減值", "权益", "權益"]

print("=== H-share tables containing search terms ===")
for t in h_doc.tables:
    found = False
    matches = []
    for c in t.cells:
        text = c.text.strip()
        simp = to_simplified(text)
        for term in search_terms:
            if term in text or term in simp:
                matches.append((c.row, c.col, text, simp))
                found = True
    if found:
        print(f"\nTable {t.table_id} page {t.page}:")
        for r, col, text, simp in sorted(set(matches)):
            print(f"  row={r} col={col}: raw='{text}' simp='{simp}'")
