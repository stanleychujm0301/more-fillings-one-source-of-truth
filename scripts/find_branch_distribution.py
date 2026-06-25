"""Find the branch distribution table with asset scales."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

def search_text_for_phrase(doc, phrases, label):
    """Search text segments for specific phrases."""
    results = []
    for seg in doc.texts:
        text = to_simplified(seg.text)
        for phrase in phrases:
            if phrase in text:
                results.append((seg.page, text[:500]))
                break
    return results

# Search for branch distribution table captions
phrases = ["分支机构", "不含子公司", "具体情况见下表", "分支机构具体情况"]

print("=== A-share text search ===")
a_doc = parse_a_pdf("f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf")
for page, text in search_text_for_phrase(a_doc, phrases, "A"):
    print(f"\nPage {page}: {text}")

print("\n\n=== H-share text search ===")
h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")
for page, text in search_text_for_phrase(h_doc, phrases, "H"):
    print(f"\nPage {page}: {text}")
