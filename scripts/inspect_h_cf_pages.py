"""Inspect H-share cash flow statement pages."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")

out_path = "f:/毕马威黑客松/ah-consistency-checker/scripts/h_cf_pages.txt"
with open(out_path, "w", encoding="utf-8") as f:
    for t in h_doc.tables:
        if t.page in [164, 165, 171, 172, 173]:
            title = t.title.zh or t.title.en or ""
            f.write(f"\n=== Page {t.page}: {title} ({len(t.cells)} cells) ===\n")
            rows = {}
            for c in t.cells:
                rows.setdefault(c.row, []).append(c)
            for r in sorted(rows.keys()):
                cells = sorted(rows[r], key=lambda x: x.col)
                row_text = " | ".join(c.text for c in cells)
                f.write(f"  Row {r}: {row_text}\n")

# Also search for CF-related terms in all table cells
    cf_terms = ["现金", "現金", "经营", "經營", "流量", "净额", "淨額"]
    f.write("\n=== All CF-related rows across all tables ===\n")
    for t in h_doc.tables:
        rows = {}
        for c in t.cells:
            rows.setdefault(c.row, []).append(c)
        for r, cells in rows.items():
            for c in cells:
                simp = to_simplified(c.text)
                if any(term in simp for term in cf_terms):
                    row_text = " | ".join(cc.text for cc in sorted(cells, key=lambda x: x.col))
                    f.write(f"Page {t.page} Row {r}: {row_text}\n")
                    break

print(f"Output written to {out_path}")
