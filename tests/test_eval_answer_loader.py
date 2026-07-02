"""官方错误清单 loader 与值匹配逻辑的单测。

官方答案格式（主办方 sample/*_错误清单_15处.xlsx）：
    序号 | PDF页码 | 描述 | 原始数字 | 错误数字 | 差异额 | 变动说明
与内部格式（pair_id/expected_rule_id/topic/...）完全不同，load_answer_key 需按表头嗅探分流。
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from ahcc.eval.matcher import (
    ExpectedDiff,
    _match_diff_to_expected,
    evaluate,
    load_answer_key,
    load_official_answer_key,
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


def _write_official_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "错误清单"
    ws.append(["序号", "PDF页码", "描述", "原始数字", "错误数字", "差异额", "变动说明"])
    ws.append([1, 17, "营业收入 2025", "126,311", "126,411", 100, "百位 3→4"])
    ws.append([2, 174, "手续费及佣金净收入", "20,252", "20,352", 100, "百位 2→3"])
    ws.append([3, 290, "经营活动现金流量净额", "(103,900)", "(103,990)", 90, "十位 0→9"])
    wb.save(path)


def _write_legacy_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["pair_id", "company", "expected_rule_id", "topic", "expected_severity", "a_page", "h_page", "note"])
    ws.append(["p1", "测试公司", "BS-001", "资产总计", "critical", 100, 90, ""])
    wb.save(path)


def _overlay_diff(page: int, visible: str, hidden: str) -> Diff:
    return Diff(
        diff_id=f"OVERLAY_A_{page}_1",
        diff_type=DiffType.INTERNAL,
        diff_scope=DiffScope.A_INTERNAL,
        severity=DiffSeverity.HIGH,
        triage="real",
        topic=LocalizedString(zh="A股文本层叠加篡改"),
        summary=LocalizedString(zh=f"A股第{page}页文本层叠加异常：可见值 {visible} 覆盖原值 {hidden}，疑似篡改"),
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=page, snippet=f"可见:{visible} 底层:{hidden}"),
        ],
        rule_id="text_overlay_tamper",
    )


def test_load_official_answer_key_parses_all_fields(tmp_path: Path) -> None:
    xlsx = tmp_path / "answers.xlsx"
    _write_official_xlsx(xlsx)

    expected = load_official_answer_key(xlsx)

    assert len(expected) == 3
    first = expected[0]
    assert first.page == 17
    assert first.a_page == 17
    assert first.original_value == "126,311"
    assert first.tampered_value == "126,411"
    assert "营业收入" in first.description


def test_load_answer_key_sniffs_official_header(tmp_path: Path) -> None:
    xlsx = tmp_path / "answers.xlsx"
    _write_official_xlsx(xlsx)

    expected = load_answer_key(xlsx)

    assert len(expected) == 3
    assert expected[0].tampered_value == "126,411"


def test_load_answer_key_still_reads_legacy_format(tmp_path: Path) -> None:
    xlsx = tmp_path / "legacy.xlsx"
    _write_legacy_xlsx(xlsx)

    expected = load_answer_key(xlsx)

    assert len(expected) == 1
    assert expected[0].expected_rule_id == "BS-001"
    assert expected[0].tampered_value == ""


def test_value_match_both_values_hit_is_exact() -> None:
    diff = _overlay_diff(17, "126,411", "126,311")
    exp = ExpectedDiff(page=17, original_value="126,311", tampered_value="126,411")

    level, reason = _match_diff_to_expected(diff, exp)

    assert level == "exact"


def test_value_match_rejects_far_page() -> None:
    diff = _overlay_diff(17, "126,411", "126,311")
    exp = ExpectedDiff(page=25, original_value="126,311", tampered_value="126,411")

    level, _ = _match_diff_to_expected(diff, exp)

    assert level == ""


def test_value_match_handles_parenthesis_negatives() -> None:
    diff = _overlay_diff(290, "(103,990)", "(103,900)")
    exp = ExpectedDiff(page=290, original_value="(103,900)", tampered_value="(103,990)")

    level, _ = _match_diff_to_expected(diff, exp)

    assert level == "exact"


def test_evaluate_official_answers_end_to_end(tmp_path: Path) -> None:
    xlsx = tmp_path / "answers.xlsx"
    _write_official_xlsx(xlsx)
    expected = load_answer_key(xlsx)
    diffs = [
        _overlay_diff(17, "126,411", "126,311"),
        _overlay_diff(174, "20,352", "20,252"),
        _overlay_diff(290, "(103,990)", "(103,900)"),
    ]

    report = evaluate(diffs, expected, pair_id="unit")

    assert report.hit_count == 3
    assert report.false_positive_count == 0
    assert report.recall == 1.0
