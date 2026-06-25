"""端到端测试脚本 — 验证 Parse → Align → Check → Report 全链路。

用法：
    python scripts/e2e_test.py A.pdf H.pdf

输出：
    - 各阶段耗时
    - 解析结果统计（页数/表格数/文本段数）
    - 对齐对数
    - 差异类型分布
    - 报告文件路径
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# 确保项目根目录在路径中
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger

from ahcc.config import settings
from ahcc.orchestrator import Orchestrator


async def main() -> None:
    if len(sys.argv) < 3:
        print("用法: python scripts/e2e_test.py <A股PDF> <H股PDF>")
        sys.exit(1)

    a_file = sys.argv[1]
    h_file = sys.argv[2]

    logger.info(f"开始端到端测试: A={a_file} H={h_file}")

    orchestrator = Orchestrator()

    start = time.perf_counter()
    job = await orchestrator.run(a_file, h_file)
    elapsed = time.perf_counter() - start

    # 输出结果
    print("\n" + "=" * 60)
    print("端到端测试报告")
    print("=" * 60)
    print(f"任务 ID: {job.job_id}")
    print(f"状态: {job.status.value}")
    print(f"总耗时: {elapsed:.1f}s")
    print(f"各阶段进度:")
    for p in job.progress:
        print(f"  {p.percent:>3}% [{p.stage.value:12s}] {p.message}")

    if job.error:
        print(f"\n错误: {job.error}")
        sys.exit(1)

    diffs = job.diffs
    print(f"\n差异统计:")
    print(f"  总差异数: {len(diffs)}")

    from collections import Counter

    type_counts = Counter(d.diff_type.value for d in diffs)
    sev_counts = Counter(d.severity.value for d in diffs)

    print(f"  按类型:")
    for dtype, cnt in sorted(type_counts.items()):
        print(f"    {dtype:12s}: {cnt}")
    print(f"  按严重度:")
    for sev, cnt in sorted(sev_counts.items(), key=lambda x: {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get(x[0], 0)):
        print(f"    {sev:12s}: {cnt}")

    # 检查是否有关键差异的 evidence
    no_evidence = [d for d in diffs if not d.evidence]
    if no_evidence:
        print(f"\n  ⚠️ {len(no_evidence)} 条差异缺少 evidence（需要修复）")
    else:
        print(f"\n  ✓ 所有差异都有 evidence")

    print(f"\n报告文件:")
    report_dir = settings.storage_dir / "jobs" / job.job_id
    for ext in ["xlsx", "pdf"]:
        path = report_dir / f"report.{ext}"
        if path.exists():
            print(f"  ✓ {path}")
        else:
            print(f"  ✗ {path} (未生成)")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
