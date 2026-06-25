"""Test branch disclosure check on Everbright Bank A+H reports."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.check.branch_disclosure import compare_branch_tables

a_doc = parse_a_pdf("f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf")
h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf")

diffs = compare_branch_tables(a_doc, h_doc)

print(f"Found {len(diffs)} branch asset scale discrepancies:")
for d in sorted(diffs, key=lambda x: abs(x.delta or 0), reverse=True):
    print(f"  [{d.severity.value}] {d.topic.zh}")
    print(f"    A = {d.a_value:>12,.0f} 百万元")
    print(f"    H = {d.h_value:>12,.0f} 百万元")
    print(f"    delta = {d.delta:+,.0f} ({d.delta/d.a_value*100 if d.a_value else 0:+.1f}%)")
    print(f"    A evidence: page {d.evidence[0].page}, snippet: {d.evidence[0].snippet[:60]}")
    print(f"    H evidence: page {d.evidence[1].page}, snippet: {d.evidence[1].snippet[:60]}")
    print()
