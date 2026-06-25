"""核心链路端到端测试 — 跳过图表检测以聚焦 Parse→Align→Numeric→Report。

用法:
    python scripts/core_e2e_test.py A.pdf H.pdf

输出:
    - 各阶段耗时
    - 解析/对齐/差异统计
    - 报告文件路径
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
from ahcc.check.numeric import run_numeric_checks
from ahcc.report.excel import export_excel
from ahcc.schemas import Job, JobStatus


async def main() -> None:
    if len(sys.argv) < 3:
        print("用法: python scripts/core_e2e_test.py <A股PDF> <H股PDF>")
        sys.exit(1)

    a_file = sys.argv[1]
    h_file = sys.argv[2]

    logger.info(f"开始核心链路测试: A={a_file} H={h_file}")

    total_start = time.perf_counter()
    job = Job(job_id="core-test", a_file=a_file, h_file=h_file)

    # ---------- Parse A ----------
    t0 = time.perf_counter()
    logger.info("解析 A 股...")
    doc_a = parse_a_pdf(a_file)
    t_parse_a = time.perf_counter() - t0
    logger.info(f"A 股完成: {doc_a.total_pages} 页, {len(doc_a.tables)} 表, {len(doc_a.texts)} 文本段")

    # ---------- Parse H ----------
    t0 = time.perf_counter()
    logger.info("解析 H 股...")
    doc_h = parse_h_pdf(h_file)
    t_parse_h = time.perf_counter() - t0
    logger.info(f"H 股完成: {doc_h.total_pages} 页, {len(doc_h.tables)} 表, {len(doc_h.texts)} 文本段")

    # ---------- Align ----------
    t0 = time.perf_counter()
    logger.info("对齐...")
    pairs = await align_documents(doc_a, doc_h)
    t_align = time.perf_counter() - t0
    bilateral = sum(1 for p in pairs if p.a_point and p.h_point)
    logger.info(f"对齐完成: {len(pairs)} 对, {bilateral} 双边匹配")

    # Show sample keys
    for p in pairs[:10]:
        a_val = p.a_point.value if p.a_point else None
        h_val = p.h_point.value if p.h_point else None
        logger.info(f"  {p.canonical_key}: A={a_val} H={h_val}")

    # ---------- Numeric Check ----------
    t0 = time.perf_counter()
    logger.info("数值检查...")
    numeric_diffs = run_numeric_checks(pairs)
    t_numeric = time.perf_counter() - t0
    logger.info(f"数值检查完成: {len(numeric_diffs)} 条差异")

    for d in numeric_diffs[:10]:
        logger.info(f"  [{d.severity.value}] {d.topic.best()}: delta={d.delta}")

    job.diffs = numeric_diffs
    job.status = JobStatus.DONE

    # ---------- Report ----------
    t0 = time.perf_counter()
    out_dir = Path("./storage/jobs") / job.job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    export_excel(job, out_dir / "report.xlsx")
    t_report = time.perf_counter() - t0
    logger.info(f"报告已导出: {out_dir / 'report.xlsx'}")

    total_elapsed = time.perf_counter() - total_start

    # ---------- Summary ----------
    print("\n" + "=" * 60)
    print("核心链路测试报告")
    print("=" * 60)
    print(f"A 股解析:  {t_parse_a:.1f}s  ({doc_a.total_pages} 页, {len(doc_a.tables)} 表, {len(doc_a.texts)} 段)")
    print(f"H 股解析:  {t_parse_h:.1f}s  ({doc_h.total_pages} 页, {len(doc_h.tables)} 表, {len(doc_h.texts)} 段)")
    print(f"对齐:      {t_align:.1f}s  ({len(pairs)} 对, {bilateral} 双边)")
    print(f"数值检查:  {t_numeric:.1f}s  ({len(numeric_diffs)} 条差异)")
    print(f"报告生成:  {t_report:.1f}s")
    print(f"总耗时:    {total_elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
