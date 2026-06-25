"""Debug operating_profit extraction."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.matcher import _extract_from_texts

doc = parse_a_pdf("f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf")

text_points = _extract_from_texts(doc)

for p in text_points:
    if p.canonical_key == "operating_profit":
        print(f"page={p.evidence.page} section={p.evidence.section} value={p.value} text='{p.value_text}'")
        print(f"  snippet={p.evidence.snippet[:200]}")
        print()

# Also search all texts for 营业利润
for seg in doc.texts:
    if "营业利润" in seg.text:
        for line in seg.text.split('\n'):
            if "营业利润" in line:
                print(f"Page {seg.page} section={seg.section}: {line.strip()}")
