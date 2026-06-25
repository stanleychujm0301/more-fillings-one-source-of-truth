import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)

seen = set()
for p in table_points:
    seen.add(p.canonical_key)
for p in text_points:
    seen.add(p.canonical_key)

_CORE_KEYS = {
    "total_assets", "total_liabilities", "equity",
    "revenue", "total_profit", "net_profit", "operating_profit", "income_tax",
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "share_capital", "eps_basic",
}

print(f"Table points: {len(table_points)}")
print(f"Text points: {len(text_points)}")
print(f"Unique keys: {len(seen)}")
print(f"\nMissing core: {[k for k in _CORE_KEYS if k not in seen]}")
print(f"\n--- Table points ---")
for p in sorted(table_points, key=lambda x: x.canonical_key):
    print(f"  {p.canonical_key:25s} = {str(p.value):>15s}  (p{p.evidence.page}, conf={p.confidence})")
print(f"\n--- Text points ---")
for p in sorted(text_points, key=lambda x: x.canonical_key):
    print(f"  {p.canonical_key:25s} = {str(p.value):>15s}  (p{p.evidence.page}, conf={p.confidence})")
