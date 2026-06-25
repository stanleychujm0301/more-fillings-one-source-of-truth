"""Search for total_assets terms in all H-share text segments."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import to_simplified

doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

with open("storage/debug_h_total_assets.log", "w", encoding="utf-8") as f:
    for seg in doc.texts:
        text = to_simplified(seg.text)
        if "资产总额" in text or "资产总计" in text or "total assets" in text.lower():
            f.write(f"\nPage {seg.page} | Section: {seg.section}\n")
            f.write(f"Text:\n{text}\n")

print("Dumped to storage/debug_h_total_assets.log")
