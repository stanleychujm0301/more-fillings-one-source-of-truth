"""文本层叠加篡改检测单测。

用 fitz 在临时目录里构造合成 PDF（在同一/相近坐标插入两段数字文本，模拟"篡改值
叠加在原值上方"），验证护栏参数：矩形重叠率阈值、同长度逐位替换数上限、页内去重、
以及内容流顺序判定叠加层。这些护栏取值均来自对主办方 3 组样本共 45 处真实植入
错误 + 6 份干净年报 PDF 的实测（见 ahcc/check/text_overlay_tamper.py 模块文档）。
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from ahcc.check.text_overlay_tamper import (
    run_text_overlay_checks,
    scan_pdf_overlays,
    scan_side,
)
from ahcc.orchestrator import Orchestrator
from ahcc.schemas import (
    Diff,
    DiffScope,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportSide,
)


def _make_pdf(path: Path, pages: list[list[tuple[tuple[float, float], str, float]]]) -> None:
    """pages: 每页一组 (insert_point, text, fontsize) 插入指令。"""
    doc = fitz.open()
    for entries in pages:
        page = doc.new_page()
        for point, text, fontsize in entries:
            page.insert_text(point, text, fontsize=fontsize, fontname="helv")
    doc.save(str(path))
    doc.close()


def test_detects_overlaid_number_and_identifies_visible_value(tmp_path: Path) -> None:
    pdf = tmp_path / "overlay.pdf"
    _make_pdf(pdf, [[((100, 100), "126,311", 12), ((100, 100), "126,411", 12)]])

    hits = scan_pdf_overlays(str(pdf))

    assert len(hits) == 1
    hit = hits[0]
    assert hit.page == 1
    assert hit.order_confident is True
    # 后插入的 span 在内容流中更晚 -> 判定为可见（叠加）层
    assert hit.visible_value == "126,411"
    assert hit.hidden_value == "126,311"


def test_identical_values_are_not_reported(tmp_path: Path) -> None:
    pdf = tmp_path / "same.pdf"
    _make_pdf(pdf, [[((100, 100), "126,311", 12), ((100, 100), "126,311", 12)]])

    assert scan_pdf_overlays(str(pdf)) == []


def test_low_y_overlap_is_not_reported(tmp_path: Path) -> None:
    """两个数字块 y 方向仅小幅重叠（约 0.39，低于 0.60 阈值）——排版正常前后行，非叠加。"""
    pdf = tmp_path / "low_overlap.pdf"
    _make_pdf(pdf, [[((100, 100), "123456", 12), ((100, 110), "123457", 12)]])

    assert scan_pdf_overlays(str(pdf)) == []


def test_different_length_is_not_reported(tmp_path: Path) -> None:
    """完全重叠但规范化后位数不同——不是保长度篡改，是完全不同的两个数。"""
    pdf = tmp_path / "diff_length.pdf"
    _make_pdf(pdf, [[((100, 100), "100", 12), ((100, 100), "1000", 12)]])

    assert scan_pdf_overlays(str(pdf)) == []


def test_too_many_substitutions_is_not_reported(tmp_path: Path) -> None:
    """替换 3 位数字超过 MAX_SUBSTITUTIONS=2，判定为两个无关数字，不报告。"""
    pdf = tmp_path / "too_many_subs.pdf"
    _make_pdf(pdf, [[((100, 100), "123456", 12), ((100, 100), "123999", 12)]])

    assert scan_pdf_overlays(str(pdf)) == []


def test_single_digit_substitution_is_reported(tmp_path: Path) -> None:
    pdf = tmp_path / "one_sub.pdf"
    _make_pdf(pdf, [[((100, 100), "123456", 12), ((100, 100), "123457", 12)]])

    hits = scan_pdf_overlays(str(pdf))
    assert len(hits) == 1


@pytest.mark.parametrize(
    "original,tampered",
    [
        ("(36,426)", "(36,526)"),  # 括号负数，百位替换
        ("7.00", "7.10"),          # 短小数值，小数点后一位替换
        ("1.49", "4.49"),          # 个位替换
    ],
)
def test_parenthesis_and_decimal_forms_are_detected(tmp_path: Path, original: str, tampered: str) -> None:
    pdf = tmp_path / "forms.pdf"
    _make_pdf(pdf, [[((100, 100), original, 12), ((100, 100), tampered, 12)]])

    hits = scan_pdf_overlays(str(pdf))
    assert len(hits) == 1
    assert {hits[0].visible_value, hits[0].hidden_value} == {original, tampered}


def test_duplicate_pair_on_same_page_is_deduped(tmp_path: Path) -> None:
    """同一数值对在同页重复出现（如篡改文本被工具意外多次写入）只报一次。"""
    pdf = tmp_path / "dedupe.pdf"
    _make_pdf(
        pdf,
        [
            [
                ((100, 100), "123456", 12),
                ((100, 100), "123457", 12),
                ((100, 100), "123456", 12),
                ((100, 100), "123457", 12),
            ]
        ],
    )

    hits = scan_pdf_overlays(str(pdf))
    assert len(hits) == 1


def test_multi_page_reports_correct_1based_page_numbers(tmp_path: Path) -> None:
    pdf = tmp_path / "multi_page.pdf"
    _make_pdf(
        pdf,
        [
            [((100, 100), "111111", 12)],  # page 1：无叠加
            [((100, 100), "222222", 12), ((100, 100), "222223", 12)],  # page 2：叠加
        ],
    )

    hits = scan_pdf_overlays(str(pdf))
    assert len(hits) == 1
    assert hits[0].page == 2


def test_scan_side_skips_non_pdf_files() -> None:
    assert scan_side("report.html", ReportSide.A_SHARE) == []


def test_scan_side_produces_diff_with_expected_shape(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, [[((100, 100), "126,311", 12), ((100, 100), "126,411", 12)]])

    diffs = scan_side(str(pdf), ReportSide.A_SHARE)

    assert len(diffs) == 1
    diff = diffs[0]
    assert diff.diff_type == DiffType.INTERNAL
    assert diff.diff_scope == DiffScope.A_INTERNAL
    assert diff.severity.value == "high"
    assert diff.triage == "real"
    assert diff.rule_id == "text_overlay_tamper"
    assert len(diff.evidence) == 2
    assert all(ev.side == ReportSide.A_SHARE and ev.page == 1 for ev in diff.evidence)


def test_run_text_overlay_checks_scans_both_sides_independently(tmp_path: Path) -> None:
    a_pdf = tmp_path / "a.pdf"
    h_pdf = tmp_path / "h.pdf"
    _make_pdf(a_pdf, [[((100, 100), "126,311", 12), ((100, 100), "126,411", 12)]])
    _make_pdf(h_pdf, [[((100, 100), "126,311", 12)]])  # 无篡改

    diffs = run_text_overlay_checks(str(a_pdf), str(h_pdf))

    assert len(diffs) == 1
    assert diffs[0].diff_scope == DiffScope.A_INTERNAL


def _visual_shadow_diff(page: int, a_value: float, h_value: float) -> Diff:
    """构造一条会与 text_overlay_tamper 重复的旧路径 diff（视觉 OCR 复核产出）。"""
    return Diff(
        diff_id="VISUAL_A_revenue_17_ab12",
        diff_type=DiffType.INTERNAL,
        diff_scope=DiffScope.A_INTERNAL,
        severity=DiffSeverity.HIGH,
        triage="real",
        topic=LocalizedString(zh="A股报告视觉层数值"),
        summary=LocalizedString(zh="视觉层与文本层不一致"),
        a_value=a_value,
        h_value=h_value,
        evidence=[Evidence(side=ReportSide.A_SHARE, page=page, snippet="视觉层片段")],
        rule_id="visual_text_layer_mismatch",
    )


def _overlay_diff(page: int, a_value: float, h_value: float) -> Diff:
    return Diff(
        diff_id=f"OVERLAY_A_{page}_1",
        diff_type=DiffType.INTERNAL,
        diff_scope=DiffScope.A_INTERNAL,
        severity=DiffSeverity.HIGH,
        triage="real",
        topic=LocalizedString(zh="A股文本层叠加篡改"),
        summary=LocalizedString(zh="可见值覆盖原值"),
        a_value=a_value,
        h_value=h_value,
        evidence=[Evidence(side=ReportSide.A_SHARE, page=page, snippet="叠加片段")],
        rule_id="text_overlay_tamper",
    )


def test_dedupe_overlay_shadows_removes_duplicate_visual_diff() -> None:
    overlay = _overlay_diff(17, 126411.0, 126311.0)
    shadow = _visual_shadow_diff(17, 126411.0, 126311.0)
    unrelated = _visual_shadow_diff(99, 1.0, 2.0)
    unrelated.diff_id = "VISUAL_A_other_99_zz"

    result = Orchestrator._dedupe_overlay_shadows([overlay, shadow, unrelated])

    assert overlay in result
    assert shadow not in result
    assert unrelated in result


def test_dedupe_overlay_shadows_matches_power_of_ten_scaled_values() -> None:
    """视觉 OCR 复核把"百万元"换算成"元"（×1e6）报同一处篡改——也应识别为重复。"""
    overlay = _overlay_diff(17, 126411.0, 126311.0)
    scaled_shadow = _visual_shadow_diff(17, 126411000000.0, 126311000000.0)

    result = Orchestrator._dedupe_overlay_shadows([overlay, scaled_shadow])

    assert overlay in result
    assert scaled_shadow not in result


def test_dedupe_overlay_shadows_keeps_different_values_on_same_page() -> None:
    overlay = _overlay_diff(17, 126411.0, 126311.0)
    other = _visual_shadow_diff(17, 555.0, 777.0)
    other.diff_id = "VISUAL_A_other_17_yy"

    result = Orchestrator._dedupe_overlay_shadows([overlay, other])

    assert other in result


def test_dedupe_overlay_shadows_is_noop_without_overlay_diffs() -> None:
    shadow = _visual_shadow_diff(17, 126411.0, 126311.0)
    result = Orchestrator._dedupe_overlay_shadows([shadow])
    assert result == [shadow]
