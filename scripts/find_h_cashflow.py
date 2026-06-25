"""Find H-share cash flow and other missing metrics."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)

# List all tables by page
table_pages = {}
for t in h_doc.tables:
    table_pages.setdefault(t.page, []).append(t)

print("=== Tables by page ===")
for page in sorted(table_pages):
    print(f"  Page {page}: {len(table_pages[page])} tables")

# Search for cash flow, intangible, construction, provisions
search_terms = {
    "cashflow": ["现金", "現金", "流量", "經營活動"],
    "intangible": ["无形", "無形"],
    "construction": ["在建", "在建"],
    "provisions": ["準備", "准备", "撥備", "拨备", "预计负债", "預計負債"],
}

print("\n=== Searching for missing metrics ===")
for t in h_doc.tables:
    for c in t.cells:
        text = c.text.strip()
        simp = to_simplified(text)
        for category, terms in search_terms.items():
            for term in terms:
                if term in text or term in simp:
                    print(f"[{category}] Page {t.page} table {t.table_id} row={c.row} col={c.col}: raw='{text}' simp='{simp}'")
                    break
