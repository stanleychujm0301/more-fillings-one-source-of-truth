import sys, asyncio
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_keypoints

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
doc = parse_h_pdf(h_path)

async def main():
    points = await _extract_keypoints(doc)
    print(f"Total points: {len(points)}")
    for p in sorted(points, key=lambda x: x.canonical_key):
        print(f"  {p.canonical_key:25s} = {str(p.value):>15s}  (p{p.evidence.page}, conf={p.confidence}, src={'table' if p.confidence >= 0.9 else 'text/llm'})")

asyncio.run(main())
