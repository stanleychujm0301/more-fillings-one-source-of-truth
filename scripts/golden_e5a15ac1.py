"""Golden 回归 — 用 e5a15ac1（光大银行 2025 A/H）的两份原始 PDF 验证列维度升级。

验收标准（对应 Phase 1 计划）：
- 假差异消失：EXACT_liquidity_coverage_ratio / EXACT_interest_net /
  EXACT_total_assets / EXACT_net_profit（消失或降级为口径重述 LOW）
- 假内部不一致消失：EPS 0.58 vs 7.94（增减% 列）、负债合计 vs 未折现租赁负债合计
- 真实检出保留：40 条 branch_asset_scale_match 一条不少
- 耗时记录（基线 146.5s；h_pdf_v4 缓存重建会额外耗时一次）

用法：python scripts/golden_e5a15ac1.py [--full-diffs]
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahcc.orchestrator import Orchestrator  # noqa: E402

A_PDF = ROOT / "storage/uploads/e5a15ac1_A_A 中国光大银行股份有限公司2025年年度报告.pdf"
H_PDF = ROOT / "storage/uploads/e5a15ac1_H_H 中国光大银行股份有限公司2025年年度报告 2.pdf"


def main() -> None:
    show_all = "--full-diffs" in sys.argv
    started = time.perf_counter()
    job = asyncio.run(Orchestrator().run(str(A_PDF), str(H_PDF)))
    elapsed = time.perf_counter() - started

    diffs = job.diffs
    summary = job.comparison_summary or {}
    print(f"status={job.status} elapsed={elapsed:.1f}s diffs={len(diffs)}")
    print(f"summary: real={summary.get('real_diff_count')} "
          f"unresolved={summary.get('unresolved_diff_count')} "
          f"expected={summary.get('expected_diff_count')} "
          f"llm_review_downgrades={summary.get('llm_semantic_review_count')}")

    by_rule: dict[str, list] = {}
    for diff in diffs:
        by_rule.setdefault(diff.rule_id or "none", []).append(diff)

    print("\n=== 按规则统计 ===")
    for rule_id in sorted(by_rule):
        items = by_rule[rule_id]
        triages = {}
        for d in items:
            triages[d.triage] = triages.get(d.triage, 0) + 1
        print(f"  {rule_id}: {len(items)} {triages}")

    print("\n=== 验收断言 ===")
    exact_keys = {
        d.canonical_key: d for d in diffs if d.rule_id == "key_metric_exact_mismatch"
    }
    for key in (
        "liquidity_coverage_ratio",
        "interest_net",
        "total_assets",
        "net_profit",
        "net_profit_attributable",
    ):
        if key in exact_keys:
            d = exact_keys[key]
            print(f"  [FAIL] EXACT {key} 仍在: A={d.a_value} H={d.h_value} sev={d.severity.value}")
        else:
            print(f"  [PASS] EXACT {key} 消失")

    internal = [d for d in diffs if d.rule_id == "internal_value_conflict" or d.diff_type.value == "internal"]
    eps_bad = [d for d in internal if "每股收益" in (d.summary.zh or "")]
    # 实例 4 的特征：负债合计 与「未折现租赁负债」子串误配（值 11,458 量级）
    liab_bad = [
        d for d in internal
        if "负债合计" in (d.summary.zh or "") and "租赁" in (d.summary.zh or "")
    ]
    print(f"  [{'FAIL' if eps_bad else 'PASS'}] EPS 增减% 假不一致 {'仍存在' if eps_bad else '消失'}（内部不一致共 {len(internal)} 条）")
    print(f"  [{'FAIL' if liab_bad else 'PASS'}] 负债合计/租赁负债 子串假不一致 {'仍存在' if liab_bad else '消失'}")

    branch = by_rule.get("branch_asset_scale_match", [])
    print(f"  [{'PASS' if len(branch) >= 40 else 'FAIL'}] 分支机构检出 {len(branch)} 条（要求 ≥40）")

    if show_all:
        print("\n=== 全部差异 ===")
        for d in diffs:
            print(f"  [{d.triage}/{d.severity.value}] {d.rule_id} {d.canonical_key}: "
                  f"A={d.a_value} H={d.h_value} | {(d.summary.zh or '')[:80]}")


if __name__ == "__main__":
    main()
