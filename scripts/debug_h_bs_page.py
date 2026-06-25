"""Dump H-share BS page text to inspect cash line."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf

doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

lines = []
for seg in doc.texts:
    if seg.section == "bs":
        if "貨幣資金" in seg.text or "現金" in seg.text or "資產總計" in seg.text or "資產總額" in seg.text:
            lines.append(f"\n=== Page {seg.page} (bs) ===")
            lines.append(seg.text[:2000])
            lines.append("...")

with open("storage/debug_h_bs_page.log", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("Saved to storage/debug_h_bs_page.log")
