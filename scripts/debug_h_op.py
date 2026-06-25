"""Debug H-share operating_profit extraction."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_texts, _extract_from_tables

h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

table_points = _extract_from_tables(h_doc)
text_points = _extract_from_texts(h_doc)

print(f"\n=== Table points for operating_profit ===")
for p in table_points:
    if p.canonical_key == "operating_profit":
        print(f"  page={p.evidence.page} section={p.evidence.section} value={p.value} text='{p.value_text}' unit={p.unit}")

print(f"\n=== Text points for operating_profit ===")
for p in text_points:
    if p.canonical_key == "operating_profit":
        print(f"  page={p.evidence.page} section={p.evidence.section} value={p.value} text='{p.value_text}' unit={p.unit}")

# Search all texts for 营业利润 / 經營利潤 / operating profit
for seg in h_doc.texts:
    if any(k in seg.text for k in ["經營利潤", "營業利潤", "Operating profit"]):
        for line in seg.text.split('\n'):
            if any(k in line for k in ["經營利潤", "營業利潤", "Operating profit"]):
                print(f"Page {seg.page} section={seg.section}: {line.strip()}")
