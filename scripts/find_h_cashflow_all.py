"""Search all H-share pages for cash flow statement."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

# Search for cash flow related terms in all tables
cf_terms = ["经营", "經營", "现金流", "現金流", "流量", "现金", "現金"]

print("=== Tables with cash flow terms ===")
found_pages = set()
for t in h_doc.tables:
    for c in t.cells:
        text = c.text.strip()
        simp = to_simplified(text)
        for term in cf_terms:
            if term in text or term in simp:
                if t.page not in found_pages:
                    print(f"\nPage {t.page} table {t.table_id}:")
                    found_pages.add(t.page)
                print(f"  row={c.row} col={c.col}: '{text}'")
                break

print(f"\n=== Found {len(found_pages)} pages with cash flow terms ===")
print(sorted(found_pages))
