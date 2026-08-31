"""P2-E2 证据链解释完备性断言。

所有 triage="real" 且 severity ∈ {high, critical} 的差异必须携带非空 diff_explanation
（页码双侧、离群值/佐证措辞、表格行列定位），否则证据链呈现不完整，无法支撑
gate (d) ≥95% 的可信度。

这里对每个「会产生 real/high」的规则路径做最小构造，断言 diff_explanation 非空。
"""
from __future__ import annotations

from ahcc.check.table_row_twin import _make_diff, _RowValue
from ahcc.check.text_overlay_tamper import _hit_to_diff, OverlayHit
from ahcc.schemas import DiffSeverity, ReportSide

_REAL_HIGH = {DiffSeverity.HIGH, DiffSeverity.CRITICAL}


def test_table_row_value_conflict_carries_explanation() -> None:
    a_row = _RowValue(label="净利润", role="2025", value=1000.0, raw="1,000", page=50, snippet="净利润 1,000")
    h_row = _RowValue(label="净利润", role="2025", value=900.0, raw="900", page=40, snippet="Net profit 900")
    diff = _make_diff(a_row, h_row, 1.0)
    assert diff.rule_id == "table_row_value_conflict"
    assert diff.triage == "real"
    assert diff.severity in _REAL_HIGH
    assert diff.diff_explanation is not None
    assert diff.diff_explanation.items


def test_text_overlay_tamper_carries_explanation() -> None:
    hit = OverlayHit(
        page=12,
        visible_value="1,000",
        hidden_value="900",
        visible_rect=(0, 0, 10, 10),
        hidden_rect=(0, 0, 10, 10),
        order_confident=True,
        row_label="净利润",
        line_text="净利润 1,000",
    )
    diff = _hit_to_diff(hit, ReportSide.A_SHARE, seq=1)
    assert diff.rule_id == "text_overlay_tamper"
    assert diff.triage == "real"
    assert diff.severity in _REAL_HIGH
    assert diff.diff_explanation is not None
    assert diff.diff_explanation.items
