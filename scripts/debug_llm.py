import sys, asyncio
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts, _extract_with_llm

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)
seen = set()
for p in table_points:
    seen.add(p.canonical_key)
for p in text_points:
    seen.add(p.canonical_key)

async def main():
    print(f"Seen keys: {len(seen)}")
    try:
        llm_points = await _extract_with_llm(doc, seen)
        print(f"LLM extracted: {len(llm_points)} points")
        for p in llm_points:
            print(f"  {p.canonical_key:25s} = {str(p.value):>15s}")
    except Exception as e:
        print(f"LLM failed: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
