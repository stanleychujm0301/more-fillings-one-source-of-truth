"""Search H-share text segments for cash flow statement."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

# Search text segments for cash flow statement
cf_terms = ["现金流量", "現金流量", "现金流", "現金流", "Cash flow statement", " cash from "]

print("=== Text segments with cash flow terms ===")
for txt in h_doc.texts:
    text = txt.text
    simp = to_simplified(text)
    found = False
    for term in cf_terms:
        if term in text or term in simp:
            found = True
            break
    if found:
        print(f"\nPage {txt.page}: {text[:300]}")

# Also check if there are any pages with "经营活动所得" or similar
print("\n=== Searching for specific cash flow phrases ===")
for txt in h_doc.texts:
    text = txt.text
    simp = to_simplified(text)
    if "经营" in simp and "现金" in simp and ("流量" in simp or "净额" in simp):
        print(f"Page {txt.page}: {text[:300]}")
