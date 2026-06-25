"""Run bilingual check on Shenwan Hongyuan 2020 H-share report and print summary."""

from __future__ import annotations

import os
import time
from collections import Counter

from ahcc.parser import parse_report
from ahcc.check import bilingual as bilingual_module
from ahcc.check.bilingual import run_bilingual_checks
from ahcc.schemas import ReportSide, ReportDocument

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZH_PATH = os.path.join(BASE, "storage/uploads/H_ZH_申万宏源：2020年度报告.pdf")
EN_PATH = os.path.join(BASE, "storage/uploads/H_EN_申万宏源：2020年度报告 英文.pdf")

MAX_PAGES = int(os.environ.get("MAX_PAGES", "0"))
SKIP_TABLE_ROWS = os.environ.get("SKIP_TABLE_ROWS", "1") == "1"


def _limit_pages(doc: ReportDocument, max_pages: int) -> ReportDocument:
    if max_pages <= 0:
        return doc
    return ReportDocument(
        doc_id=doc.doc_id,
        side=doc.side,
        file_path=doc.file_path,
        total_pages=min(doc.total_pages, max_pages),
        primary_language=doc.primary_language,
        texts=[t for t in doc.texts if t.page <= max_pages],
        tables=[tbl for tbl in doc.tables if tbl.page <= max_pages],
        charts=[c for c in doc.charts if c.page <= max_pages],
        metadata=doc.metadata,
        extraction_audit=doc.extraction_audit,
    )


def main():
    print("=" * 60)
    print("申万宏源 2020 H-share 中英文报告双语检查")
    if MAX_PAGES > 0:
        print(f"【限制模式】仅分析前 {MAX_PAGES} 页")
    if SKIP_TABLE_ROWS:
        print("【加速模式】跳过表格行配对")
    print("=" * 60)

    t0 = time.time()
    print(f"\n[1/4] 解析中文报告: {ZH_PATH}")
    t1 = time.time()
    zh_doc = parse_report(ZH_PATH, ReportSide.H_SHARE)
    if MAX_PAGES > 0:
        zh_doc = _limit_pages(zh_doc, MAX_PAGES)
    print(f"       页数: {zh_doc.total_pages}, 文本段: {len(zh_doc.texts)}, 表格: {len(zh_doc.tables)}  ({time.time()-t1:.1f}s)")

    print(f"\n[2/4] 解析英文报告: {EN_PATH}")
    t1 = time.time()
    en_doc = parse_report(EN_PATH, ReportSide.H_SHARE)
    if MAX_PAGES > 0:
        en_doc = _limit_pages(en_doc, MAX_PAGES)
    print(f"       页数: {en_doc.total_pages}, 文本段: {len(en_doc.texts)}, 表格: {len(en_doc.tables)}  ({time.time()-t1:.1f}s)")

    # 跳过表格行检查以加速
    if SKIP_TABLE_ROWS:
        original_table_row_diffs = bilingual_module._table_row_diffs
        def _noop_table_row_diffs(*args, **kwargs):
            return []
        bilingual_module._table_row_diffs = _noop_table_row_diffs

    print(f"\n[3/4] 运行双语检查...")
    t1 = time.time()
    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=None, enable_semantic=False)
    check_time = time.time() - t1
    elapsed = time.time() - t0

    if SKIP_TABLE_ROWS:
        bilingual_module._table_row_diffs = original_table_row_diffs

    print(f"\n{'=' * 60}")
    print("检查结果汇总")
    print(f"{'=' * 60}")
    print(f"总耗时: {elapsed:.1f}s (检查阶段: {check_time:.1f}s)")
    print(f"总差异数: {len(result.diffs)}")
    print(f"警告数: {len(result.warnings)}")

    print(f"\nStats:")
    for k, v in sorted(result.stats.items()):
        print(f"  {k}: {v}")

    print(f"\n差异按 rule_id 分布:")
    rule_counts = Counter(d.rule_id for d in result.diffs)
    for rule, count in rule_counts.most_common():
        print(f"  {rule}: {count}")

    print(f"\n差异按严重度分布:")
    sev_counts = Counter(str(d.severity) for d in result.diffs)
    for sev, count in sev_counts.most_common():
        print(f"  {sev}: {count}")

    # 打印差异详情
    print(f"\n{'=' * 60}")
    print(f"差异详情 (共 {len(result.diffs)} 条):")
    print(f"{'=' * 60}")
    for i, diff in enumerate(result.diffs, 1):
        exp = diff.diff_explanation
        loc = exp.location if exp else "N/A"
        headline = exp.headline if exp else diff.summary.zh
        print(f"\n  {i}. [{diff.severity}] {diff.rule_id}")
        print(f"     标题: {headline}")
        print(f"     位置: {loc}")
        if exp and exp.items:
            for item in exp.items:
                a_val = str(item.a_value)[:60] if item.a_value else "N/A"
                h_val = str(item.h_value)[:60] if item.h_value else "N/A"
                print(f"     {item.role}: A={a_val}, H={h_val}")


if __name__ == "__main__":
    main()
