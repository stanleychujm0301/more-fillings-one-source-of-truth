"""快速测试 H 股解析和数据提取（跳过 camelot/PPStructure）。"""

import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_keypoints

async def main():
    doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")
    print(f"H 股: {doc.total_pages} 页, {len(doc.tables)} 表, {len(doc.texts)} 文本段")
    print(f"语言: {doc.primary_language}")

    from collections import Counter
    sec_counts = Counter(t.section for t in doc.texts)
    print(f"\nSection 分布: {dict(sec_counts)}")

    points = await _extract_keypoints(doc)
    print(f"\n共提取 {len(points)} 个数据点")
    for p in sorted(points, key=lambda x: x.canonical_key):
        print(f"  {p.canonical_key:30s} value={p.value:>15.2f} text={p.value_text!r} conf={p.confidence:.2f} page={p.evidence.page}")

if __name__ == "__main__":
    asyncio.run(main())
