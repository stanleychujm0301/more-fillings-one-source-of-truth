"""跨规则 real 去重的边界：只合并「不同规则命中同一处」，不吃掉同一规则的独立差异。

背景：排列型篡改（一列取值被整体打乱）下，同一规则产出的每条差异，其 h_value 必然
等于另一条的 a_value，页码又天然相邻。若同规则内部也按「同侧同页同值」合并，
光大银行分支机构的 40 条真实差异会被砍成 18 条。
"""

from __future__ import annotations

from ahcc.orchestrator import Orchestrator
from ahcc.schemas import (
    Diff,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportSide,
)


def _diff(
    diff_id: str,
    *,
    rule_id: str,
    a_value: float,
    h_value: float,
    a_page: int = 130,
    h_page: int = 129,
    severity: DiffSeverity = DiffSeverity.HIGH,
    triage: str = "real",
) -> Diff:
    return Diff(
        diff_id=diff_id,
        diff_type=DiffType.NUMERIC,
        severity=severity,
        triage=triage,
        topic=LocalizedString(zh=diff_id, en=diff_id),
        summary=LocalizedString(zh=diff_id, en=diff_id),
        a_value=a_value,
        h_value=h_value,
        rule_id=rule_id,
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=a_page, snippet=str(a_value)),
            Evidence(side=ReportSide.H_SHARE, page=h_page, snippet=str(h_value)),
        ],
    )


def test_same_rule_permutation_chain_survives_dedupe() -> None:
    """同一规则、页码相邻、首尾共享取值的一串差异必须全部保留。

    形态取自光大银行分支机构表：A[上海]=443188→H=39540，而 39540 正是 A[长春] 的值，
    A[长春]=39540→H=149069，如此环环相扣。
    """
    diffs = [
        _diff("BRANCH_上海分行", rule_id="branch_asset_scale_match", a_value=443188, h_value=39540),
        _diff("BRANCH_长春分行", rule_id="branch_asset_scale_match", a_value=39540, h_value=149069, a_page=131),
        _diff("BRANCH_天津分行", rule_id="branch_asset_scale_match", a_value=101325, h_value=59836),
        _diff("BRANCH_黑龙江分行", rule_id="branch_asset_scale_match", a_value=59836, h_value=24602, a_page=131),
    ]

    kept = Orchestrator._dedupe_cross_rule_real(diffs)

    assert len(kept) == 4, [d.diff_id for d in kept]
    assert {d.diff_id for d in kept} == {d.diff_id for d in diffs}


def test_cross_rule_duplicate_is_still_merged() -> None:
    """不同规则命中同一处篡改仍须合并 —— 这是本函数存在的理由，不能一起放开。"""
    diffs = [
        _diff("OVERLAY_1", rule_id="text_overlay_tamper", a_value=12345678, h_value=87654321),
        _diff("ROWTWIN_1", rule_id="table_row_value_conflict", a_value=12345678, h_value=87654321),
    ]

    kept = Orchestrator._dedupe_cross_rule_real(diffs)

    assert len(kept) == 1
    # 规则特异性更高的 overlay（带视觉证据）胜出
    assert kept[0].rule_id == "text_overlay_tamper"


def test_cross_rule_dedupe_respects_page_distance() -> None:
    """同值但页码相隔较远的不同规则条目不算重复。"""
    diffs = [
        _diff("OVERLAY_1", rule_id="text_overlay_tamper", a_value=12345678, h_value=87654321),
        _diff(
            "ROWTWIN_1",
            rule_id="table_row_value_conflict",
            a_value=12345678,
            h_value=87654321,
            a_page=200,
            h_page=201,
        ),
    ]

    kept = Orchestrator._dedupe_cross_rule_real(diffs)

    assert len(kept) == 2


def test_non_real_diffs_are_untouched() -> None:
    """unresolved/expected 不参与跨规则去重。"""
    diffs = [
        _diff("OVERLAY_1", rule_id="text_overlay_tamper", a_value=12345678, h_value=87654321),
        _diff(
            "ROWTWIN_1",
            rule_id="table_row_value_conflict",
            a_value=12345678,
            h_value=87654321,
            triage="unresolved",
        ),
    ]

    kept = Orchestrator._dedupe_cross_rule_real(diffs)

    assert len(kept) == 2
