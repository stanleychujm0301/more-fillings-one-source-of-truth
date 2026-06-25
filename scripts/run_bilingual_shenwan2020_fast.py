"""快速验证：只跑文本段（清空表格），跳过耗时瓶颈。"""

from __future__ import annotations

import os
import time
from collections import Counter

from ahcc.parser import parse_report
from ahcc.check.bilingual import run_bilingual_checks
from ahcc.schemas import ReportSide, ReportDocument

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZH_PATH = os.path.join(BASE, "storage/uploads/H_ZH_申万宏源：2020年度报告.pdf")
EN_PATH = os.path.join(BASE, "storage/uploads/H_EN_申万宏源：2020年度报告 英文.pdf")

MAX_PAGES = int(os.environ.get("MAX_PAGES", "150"))


def _limit_to_text_only(doc: ReportDocument, max_pages: int) -> ReportDocument:
    """只保留指定页数范围内的文本段，清空表格以加速配对。"""
    return ReportDocument(
        doc_id=doc.doc_id,
        side=doc.side,
        file_path=doc.file_path,
        total_pages=min(doc.total_pages, max_pages),
        primary_language=doc.primary_language,
        texts=[t for t in doc.texts if t.page <= max_pages],
        tables=[],  # 清空表格，跳过表格行配对
        charts=[],
        metadata=doc.metadata,
        extraction_audit=doc.extraction_audit,
    )


def main():
    print("=" * 60)
    print("申万宏源 2020 H-share 快速验证（仅文本段，前 {} 页）".format(MAX_PAGES))
    print("=" * 60)

    t0 = time.time()
    print(f"\n[1/3] 解析中文报告...")
    t1 = time.time()
    zh_doc_raw = parse_report(ZH_PATH, ReportSide.H_SHARE)
    zh_doc = _limit_to_text_only(zh_doc_raw, MAX_PAGES)
    print(f"       原始: {zh_doc_raw.total_pages}页/{len(zh_doc_raw.texts)}段/{len(zh_doc_raw.tables)}表")
    print(f"       过滤后: {zh_doc.total_pages}页/{len(zh_doc.texts)}段/0表 ({time.time()-t1:.1f}s)")

    print(f"\n[2/3] 解析英文报告...")
    t1 = time.time()
    en_doc_raw = parse_report(EN_PATH, ReportSide.H_SHARE)
    en_doc = _limit_to_text_only(en_doc_raw, MAX_PAGES)
    print(f"       原始: {en_doc_raw.total_pages}页/{len(en_doc_raw.texts)}段/{len(en_doc_raw.tables)}表")
    print(f"       过滤后: {en_doc.total_pages}页/{len(en_doc.texts)}段/0表 ({time.time()-t1:.1f}s)")

    print(f"\n[3/3] 运行双语检查...")
    t1 = time.time()
    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=None, enable_semantic=False)
    check_time = time.time() - t1
    elapsed = time.time() - t0

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

    # 打印前30个差异详情
    limit = min(30, len(result.diffs))
    print(f"\n前{limit}个差异详情:")
    for i, diff in enumerate(result.diffs[:limit], 1):
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
