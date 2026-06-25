"""Find the branch distribution table with asset scales - writes to file."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

def search_text_for_phrase(doc, phrases):
    results = []
    for seg in doc.texts:
        text = to_simplified(seg.text)
        for phrase in phrases:
            if phrase in text:
                results.append((seg.page, text))
                break
    return results

phrases = ["分支机构", "不含子公司", "具体情况见下表", "分支机构具体情况", "本行分支机构"]

out_path = "f:/毕马威黑客松/ah-consistency-checker/scripts/branch_distribution.txt"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("=== A-share branch tables ===\n")
    a_doc = parse_a_pdf("f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf")
    for page, text in search_text_for_phrase(a_doc, phrases):
        f.write(f"\n--- Page {page} ---\n{text}\n")

    f.write("\n\n=== H-share branch tables ===\n")
    h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")
    for page, text in search_text_for_phrase(h_doc, phrases):
        f.write(f"\n--- Page {page} ---\n{text}\n")

print(f"Output written to {out_path}")
