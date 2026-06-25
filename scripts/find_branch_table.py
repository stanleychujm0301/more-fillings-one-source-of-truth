"""Find branch distribution table with asset scales in A and H reports."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

def find_branch_tables(doc, label):
    """Find tables containing branch-related terms."""
    matches = []
    for t in doc.tables:
        for c in t.cells:
            text = to_simplified(c.text)
            if "分支" in text or "机构" in text or "分行" in text or "网点" in text:
                # Get table preview
                rows = {}
                for cc in t.cells:
                    rows.setdefault(cc.row, []).append(cc)
                preview = []
                for r in sorted(rows.keys())[:10]:
                    row_text = " | ".join(cc.text[:30] for cc in sorted(rows[r], key=lambda x: x.col))
                    preview.append(f"  {row_text}")
                matches.append((t.page, "\n".join(preview)))
                break
    return matches

print("=== A-share branch tables ===")
a_doc = parse_a_pdf("f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf")
for page, preview in find_branch_tables(a_doc, "A"):
    print(f"\nPage {page}:")
    print(preview)

print("\n\n=== H-share branch tables ===")
h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")
for page, preview in find_branch_tables(h_doc, "H"):
    print(f"\nPage {page}:")
    print(preview)
