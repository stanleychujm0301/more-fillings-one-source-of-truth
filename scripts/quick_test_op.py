"""Quick test for operating_profit extraction fix."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts

doc = parse_a_pdf("f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf")

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)

all_points = table_points + text_points
op_points = [p for p in all_points if p.canonical_key == "operating_profit"]

print(f"Found {len(op_points)} operating_profit candidates:")
for p in op_points:
    print(f"  page={p.evidence.page} section={p.evidence.section} value={p.value} text='{p.value_text}'")

if op_points:
    best = max(op_points, key=lambda p: p.value or 0)
    print(f"\nBest (max): page={best.evidence.page} value={best.value}")
    print("PASS" if best.value and best.value > 1e10 else "FAIL")
