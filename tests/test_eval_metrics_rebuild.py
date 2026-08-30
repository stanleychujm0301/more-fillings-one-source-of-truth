"""评估口径重建后的钉子测试（Phase 0）。

这些断言存在的意义：旧的评估口径会系统性虚高指标，导致"准确率已达标"的判断无效。
每条测试都对应一个已被复现的虚高路径，改动评估逻辑时必须让它们保持绿色。
"""

from __future__ import annotations

import pytest

from ahcc.eval.matcher import (
    ExpectedDiff,
    _match_diff_to_expected,
    evaluate,
    is_visible_diff,
)
from ahcc.eval.probes import (
    bucket_diffs,
    fp_upper_bound,
    self_consistency_report,
    wilson_interval,
)
from ahcc.schemas import (
    Diff,
    DiffScope,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportSide,
)


def _diff(
    page: int,
    summary: str,
    *,
    a=None,
    h=None,
    snippet: str = "",
    triage: str = "real",
    severity: DiffSeverity = DiffSeverity.HIGH,
    rule_id: str = "numeric_mismatch",
    scope: DiffScope = DiffScope.CROSS_REPORT,
) -> Diff:
    return Diff(
        diff_id=f"D{page}_{rule_id}_{triage}",
        diff_type=DiffType.NUMERIC,
        diff_scope=scope,
        severity=severity,
        triage=triage,
        topic=LocalizedString(zh="测试指标"),
        summary=LocalizedString(zh=summary),
        a_value=a,
        h_value=h,
        evidence=[Evidence(side=ReportSide.A_SHARE, page=page, snippet=snippet)],
        rule_id=rule_id,
    )


_EXP = ExpectedDiff(page=17, original_value="126,311", tampered_value="126,411")


# ============================================================
# 1. snippet 不得参与命中判定
# ============================================================

def test_snippet_containing_answer_number_is_not_a_hit() -> None:
    """旧实现把整行 evidence.snippet 拼进检索串再做子串匹配，于是"同页任意一条
    无关差异"都会命中。snippet 是 PDF 原文整行，含该行所有数字。"""
    unrelated = _diff(
        19,
        "其他应收款: A股 1.00 vs H股 2.00",
        a=1.0,
        h=2.0,
        snippet="营业收入 126,311 126,000 手续费及佣金净收入 20,252",
    )
    level, _ = _match_diff_to_expected(unrelated, _EXP)
    assert level == ""


def test_longer_number_containing_answer_is_not_a_hit() -> None:
    """子串匹配会让 1,126,411 命中 126,411；现在按数值 token 精确相等。"""
    diff = _diff(17, "某指标: A股 1,126,411 vs H股 1,126,311", a=1126411.0, h=1126311.0)
    level, _ = _match_diff_to_expected(diff, _EXP)
    assert level == ""


# ============================================================
# 2. 只命中"正确值"不算发现篡改
# ============================================================

def test_original_value_only_is_weak_not_hit() -> None:
    diff = _diff(17, "每股净资产: A股 126,311 vs H股 126,000", a=126311.0, h=126000.0)
    level, _ = _match_diff_to_expected(diff, _EXP)
    assert level == "weak"

    report = evaluate([diff], [_EXP], pair_id="t")
    assert report.hit_count == 0
    assert report.weak_count == 1
    assert report.recall == 0.0


def test_tampered_value_hit_counts() -> None:
    diff = _diff(17, "可见值 126,411 覆盖原值 126,311", a=126411.0, h=126311.0)
    level, _ = _match_diff_to_expected(diff, _EXP)
    assert level == "exact"
    assert evaluate([diff], [_EXP], pair_id="t").hit_count == 1


# ============================================================
# 3. 召回按"用户可见性"定义
# ============================================================

@pytest.mark.parametrize(
    ("triage", "severity", "visible"),
    [
        ("real", DiffSeverity.CRITICAL, True),
        ("real", DiffSeverity.HIGH, True),
        ("real", DiffSeverity.MEDIUM, True),
        ("real", DiffSeverity.LOW, False),
        ("real", DiffSeverity.INFO, False),
        ("expected", DiffSeverity.CRITICAL, False),
        ("unresolved", DiffSeverity.HIGH, False),
    ],
)
def test_visibility_definition(triage: str, severity: DiffSeverity, visible: bool) -> None:
    assert is_visible_diff(_diff(1, "x", a=1.0, h=2.0, triage=triage, severity=severity)) is visible


def test_detected_but_suppressed_is_not_recall() -> None:
    """查到了、但被自己降级成 expected/INFO 的差异，审计师在界面上看不到，
    等同于漏检 —— 但必须单列计数，这是当前 FN 的主战场。"""
    suppressed = _diff(
        17, "可见值 126,411 覆盖原值 126,311", a=126411.0, h=126311.0,
        triage="expected", severity=DiffSeverity.INFO,
    )
    report = evaluate([suppressed], [_EXP], pair_id="t")
    assert report.hit_count == 0
    assert report.recall == 0.0
    assert report.detected_but_suppressed_count == 1


# ============================================================
# 4. 误报分档
# ============================================================

def test_false_positive_is_split_into_three_tiers() -> None:
    hit = _diff(17, "可见值 126,411 覆盖原值 126,311", a=126411.0, h=126311.0)
    noise = [
        _diff(50, "噪声", a=1.0, h=2.0, triage="real"),
        _diff(51, "噪声", a=1.0, h=2.0, triage="unresolved"),
        _diff(52, "噪声", a=1.0, h=2.0, triage="expected"),
    ]
    report = evaluate([hit, *noise], [_EXP], pair_id="t")
    assert report.hit_count == 1
    assert report.hard_fp_count == 1
    assert report.soft_fp_count == 1
    assert report.suppressed_count == 1
    # 精确率分母只含 hard 误报
    assert report.precision == 0.5


# ============================================================
# 5. 分配必须与答案行顺序无关
# ============================================================

def test_assignment_is_order_independent() -> None:
    """旧实现按 expected 顺序贪心且不回溯，指标随答案行顺序波动、不可复现。"""
    exp_a = ExpectedDiff(page=17, original_value="100,000", tampered_value="100,100")
    exp_b = ExpectedDiff(page=17, original_value="200,000", tampered_value="200,200")
    d_a = _diff(17, "甲: 100,100 vs 100,000", a=100100.0, h=100000.0)
    d_b = _diff(17, "乙: 200,200 vs 200,000", a=200200.0, h=200000.0)

    forward = evaluate([d_a, d_b], [exp_a, exp_b], pair_id="t")
    reversed_expected = evaluate([d_a, d_b], [exp_b, exp_a], pair_id="t")
    reversed_diffs = evaluate([d_b, d_a], [exp_a, exp_b], pair_id="t")

    assert forward.hit_count == 2
    assert reversed_expected.hit_count == 2
    assert reversed_diffs.hit_count == 2


# ============================================================
# 6. A=H 自一致性探针
# ============================================================

def test_self_consistency_probe_flags_cross_report_diffs() -> None:
    """A 与 H 是同一份文件时，任何跨报告差异在定义上都是纯误报。"""
    diffs = [
        _diff(10, "跨报告噪声", a=1.0, h=2.0, scope=DiffScope.CROSS_REPORT, rule_id="numeric_mismatch"),
        _diff(11, "单侧内部差异", a=1.0, h=2.0, scope=DiffScope.A_INTERNAL, rule_id="text_overlay_tamper"),
    ]
    report = self_consistency_report(diffs, pair_id="probe")
    assert report.cross_report_count == 1
    assert report.passed is False
    assert report.by_rule_id == {"numeric_mismatch": 1}


def test_self_consistency_probe_passes_with_only_internal_diffs() -> None:
    diffs = [_diff(11, "单侧内部差异", a=1.0, h=2.0, scope=DiffScope.A_INTERNAL)]
    assert self_consistency_report(diffs, pair_id="probe").passed is True


# ============================================================
# 7. FP 上界与分桶
# ============================================================

def test_fp_upper_bound_counts_by_scope_and_triage() -> None:
    diffs = [
        _diff(1, "x", a=1.0, h=2.0, triage="real"),
        _diff(2, "x", a=1.0, h=2.0, triage="real"),
        _diff(3, "x", a=1.0, h=2.0, triage="unresolved"),
        _diff(4, "x", a=1.0, h=2.0, triage="expected"),
        _diff(5, "x", a=1.0, h=2.0, scope=DiffScope.A_INTERNAL),
    ]
    bounds = fp_upper_bound(diffs)
    assert bounds["cross_report_real"] == 2
    assert bounds["cross_report_unresolved"] == 1
    assert bounds["cross_report_expected"] == 1
    assert bounds["internal"] == 1


def test_bucket_diffs_groups_by_rule_triage_severity_scope() -> None:
    diffs = [
        _diff(1, "x", a=1.0, h=2.0, rule_id="r1"),
        _diff(2, "x", a=1.0, h=2.0, rule_id="r1"),
        _diff(3, "x", a=1.0, h=2.0, rule_id="r2"),
    ]
    buckets = bucket_diffs(diffs)
    assert len(buckets) == 2
    assert buckets[0].rule_id == "r1"
    assert len(buckets[0].diffs) == 2


def test_wilson_interval_bounds() -> None:
    lo, hi = wilson_interval(0, 0)
    assert (lo, hi) == (0.0, 1.0)

    lo, hi = wilson_interval(20, 20)
    assert lo > 0.8 and hi == pytest.approx(1.0)

    lo, hi = wilson_interval(10, 20)
    assert lo < 0.5 < hi
