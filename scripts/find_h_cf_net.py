"""Find H-share operating cash flow net amount."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf

h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")

# Search for CF net amount in pages 173-176
for t in h_doc.tables:
    if t.page in [173, 174, 175, 176]:
        rows = {}
        for c in t.cells:
            rows.setdefault(c.row, []).append(c)
        for r, cells in rows.items():
            row_text = " | ".join(c.text for c in sorted(cells, key=lambda x: x.col))
            if "現金流量淨額" in row_text or "现金流量净额" in row_text or "經營活動產生" in row_text:
                print(f"Page {t.page} Row {r}: {row_text}")
