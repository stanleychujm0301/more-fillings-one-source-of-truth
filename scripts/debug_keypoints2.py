import sys, asyncio
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts
from ahcc.align.glossary import glossary

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)

points = []
seen = set()
for p in table_points:
    if p.canonical_key not in seen:
        points.append(p)
        seen.add(p.canonical_key)
for p in text_points:
    if p.canonical_key not in seen:
        points.append(p)
        seen.add(p.canonical_key)

_CORE_KEYS = {
    "total_assets", "total_liabilities", "equity",
    "revenue", "total_profit", "net_profit", "operating_profit", "income_tax",
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "share_capital", "eps_basic",
}
missing_core = [k for k in _CORE_KEYS if k not in seen]
should_llm = len(points) < 8 or len(missing_core) >= 3

print(f"Points before LLM: {len(points)}")
print(f"Missing core: {missing_core}")
print(f"Should LLM: {should_llm}")
