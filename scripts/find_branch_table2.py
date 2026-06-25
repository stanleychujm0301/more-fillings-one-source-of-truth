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
    """Find tables containing branch-related terms with asset scales."""
    matches = []
    for t in doc.tables:
        table_text = " ".join(to_simplified(c.text) for c in t.cells)
        # Look for branch + asset scale indicators
        has_branch = any(k in table_text for k in ["分支", "分行", "网点", "机构"])
        has_asset = any(k in table_text for k in ["资产", "规模", "余额", "贷款", "存款"])
        if has_branch and has_asset:
            # Get full table content
            rows = {}
            for cc in t.cells:
                rows.setdefault(cc.row, []).append(cc)
            preview = []
            for r in sorted(rows.keys()):
                cells = sorted(rows[r], key=lambda x: x.col)
                row_text = " | ".join(cc.text for cc in cells)
                preview.append(f"  Row {r}: {row_text}")
            matches.append((t.page, "\n".join(preview)))
    return matches

out_path = "f:/毕马威黑客松/ah-consistency-checker/scripts/branch_tables.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("=== A-share branch tables ===\n")
    a_doc = parse_a_pdf("f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf")
    for page, preview in find_branch_tables(a_doc, "A"):
        f.write(f"\nPage {page}:\n{preview}\n")

    f.write("\n\n=== H-share branch tables ===\n")
    h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")
    for page, preview in find_branch_tables(h_doc, "H"):
        f.write(f"\nPage {page}:\n{preview}\n")

print(f"Output written to {out_path}")
