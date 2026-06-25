"""快速测试脚本 — 只验证 Parse + Align，跳过图表检测和 LLM 检查。

用法：
    python scripts/quick_test.py A.pdf H.pdf
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import align_documents


async def main():
    if len(sys.argv) < 3:
        print("用法: python scripts/quick_test.py <A股PDF> <H股PDF>")
        sys.exit(1)

    a_file = sys.argv[1]
    h_file = sys.argv[2]

    print("=" * 60)
    print("快速测试：Parse + Align")
    print("=" * 60)

    # Parse A
    t0 = time.perf_counter()
    doc_a = parse_a_pdf(a_file)
    t1 = time.perf_counter()
    print(f"\n[A股解析] {t1-t0:.1f}s")
    print(f"  页数: {doc_a.total_pages}")
    print(f"  表格: {len(doc_a.tables)}")
    print(f"  文本段: {len(doc_a.texts)}")

    # 打印前10个表格的标题和页码
    print("  前10个表格:")
    for t in doc_a.tables[:10]:
        title = t.title.zh or t.title.en or ""
        print(f"    P{t.page:3d} {t.table_id}: {title[:40]}")

    # Parse H
    t0 = time.perf_counter()
    doc_h = parse_h_pdf(h_file)
    t1 = time.perf_counter()
    print(f"\n[H股解析] {t1-t0:.1f}s")
    print(f"  页数: {doc_h.total_pages}")
    print(f"  表格: {len(doc_h.tables)}")
    print(f"  文本段: {len(doc_h.texts)}")
    print(f"  语言: {doc_h.primary_language.value}")

    # 打印前10个表格的标题和页码
    print("  前10个表格:")
    for t in doc_h.tables[:10]:
        title = t.title.zh or t.title.en or ""
        print(f"    P{t.page:3d} {t.table_id}: {title[:40]}")

    # Align
    t0 = time.perf_counter()
    pairs = await align_documents(doc_a, doc_h)
    t1 = time.perf_counter()
    print(f"\n[对齐] {t1-t0:.1f}s")
    print(f"  对齐对数: {len(pairs)}")

    # 统计
    both = sum(1 for p in pairs if p.a_point and p.h_point)
    only_a = sum(1 for p in pairs if p.a_point and not p.h_point)
    only_h = sum(1 for p in pairs if p.h_point and not p.a_point)
    print(f"  双侧有值: {both}")
    print(f"  仅A股: {only_a}")
    print(f"  仅H股: {only_h}")

    # 打印前20对
    print("\n  前20对对齐结果:")
    for p in pairs[:20]:
        a_val = f"{p.a_point.value:,.2f}" if p.a_point and p.a_point.value is not None else "—"
        h_val = f"{p.h_point.value:,.2f}" if p.h_point and p.h_point.value is not None else "—"
        print(f"    {p.canonical_key:25s} A={a_val:>18s} H={h_val:>18s} ({p.topic_zh})")

    print("\n" + "=" * 60)
    print("快速测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
