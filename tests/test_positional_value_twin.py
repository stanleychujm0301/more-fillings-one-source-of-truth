"""位置孪生比对（同文档 edit/swap 检出）单元测试。

用 fitz 现场生成小型 PDF：坐标配对是本模块的物理基础，必须用真实 PDF 验证，
不能用文本段 mock 替代。
"""
from __future__ import annotations

import fitz
import pytest

from ahcc.check.positional_value_twin import (
    RULE_ID,
    run_positional_value_twin_checks,
)
from ahcc.schemas import Language, ReportDocument, ReportSide

_BASE_NUMBERS = ["1,234,567", "2,345,678", "3,456,789", "4,567,890", "5,678,901"]


def _make_pdf(path, pages: list[list[str]]) -> None:
    """每页把数值词写在固定坐标 (100, 100+i*20)。"""
    doc = fitz.open()
    for numbers in pages:
        page = doc.new_page()
        for i, num in enumerate(numbers):
            page.insert_text(fitz.Point(100, 100 + i * 20), num, fontsize=10)
    doc.save(str(path))
    doc.close()


def _doc(path, side: ReportSide, total_pages: int) -> ReportDocument:
    return ReportDocument(
        doc_id=f"{side.value}-doc",
        side=side,
        file_path=str(path),
        total_pages=total_pages,
        primary_language=Language.ZH,
        texts=[],
    )


def test_edit_detected_at_same_position(tmp_path) -> None:
    tampered = tmp_path / "a.pdf"
    clean = tmp_path / "h.pdf"
    _make_pdf(tampered, [_BASE_NUMBERS[:2] + ["352,533"] + _BASE_NUMBERS[2:]])
    _make_pdf(clean, [_BASE_NUMBERS[:2] + ["352,532"] + _BASE_NUMBERS[2:]])
    diffs = run_positional_value_twin_checks(
        _doc(tampered, ReportSide.A_SHARE, 1), _doc(clean, ReportSide.H_SHARE, 1)
    )
    assert len(diffs) == 1
    d = diffs[0]
    assert d.rule_id == RULE_ID
    assert d.triage == "real"
    assert d.evidence[0].page == 1
    assert {round(d.a_value), round(d.h_value)} == {352533, 352532}
    assert d.diff_explanation is not None


def test_swap_merged_into_single_diff(tmp_path) -> None:
    tampered = tmp_path / "a.pdf"
    clean = tmp_path / "h.pdf"
    _make_pdf(tampered, [_BASE_NUMBERS[:2] + ["3,946,275", "3,999,448"] + _BASE_NUMBERS[2:]])
    _make_pdf(clean, [_BASE_NUMBERS[:2] + ["3,999,448", "3,946,275"] + _BASE_NUMBERS[2:]])
    diffs = run_positional_value_twin_checks(
        _doc(tampered, ReportSide.A_SHARE, 1), _doc(clean, ReportSide.H_SHARE, 1)
    )
    # 镜像两端合并为一条，避免一端命中、另一端成为 hard FP
    assert len(diffs) == 1
    assert {round(diffs[0].a_value), round(diffs[0].h_value)} == {3946275, 3999448}


def test_identical_documents_zero_diffs(tmp_path) -> None:
    a = tmp_path / "a.pdf"
    h = tmp_path / "h.pdf"
    _make_pdf(a, [_BASE_NUMBERS, ["6,789,012"]])
    _make_pdf(h, [_BASE_NUMBERS, ["6,789,012"]])
    assert run_positional_value_twin_checks(
        _doc(a, ReportSide.A_SHARE, 2), _doc(h, ReportSide.H_SHARE, 2)
    ) == []


def test_same_file_path_skipped(tmp_path) -> None:
    a = tmp_path / "same.pdf"
    _make_pdf(a, [_BASE_NUMBERS])
    doc = _doc(a, ReportSide.A_SHARE, 1)
    assert run_positional_value_twin_checks(doc, doc) == []


def test_different_page_count_skipped(tmp_path) -> None:
    a = tmp_path / "a.pdf"
    h = tmp_path / "h.pdf"
    _make_pdf(a, [_BASE_NUMBERS, ["1,111,111"]])
    _make_pdf(h, [_BASE_NUMBERS])
    assert run_positional_value_twin_checks(
        _doc(a, ReportSide.A_SHARE, 2), _doc(h, ReportSide.H_SHARE, 1)
    ) == []


def test_different_layout_not_compared(tmp_path) -> None:
    """真实跨报告对：版式完全不同 → 位置配对率趋零 → 整页跳过，不产生误报。"""
    a = tmp_path / "a.pdf"
    h = tmp_path / "h.pdf"
    _make_pdf(a, [_BASE_NUMBERS])
    # H 侧数值相同但全部写在另一组坐标上
    doc = fitz.open()
    page = doc.new_page()
    for i, num in enumerate(_BASE_NUMBERS):
        page.insert_text(fitz.Point(400, 500 + i * 30), num, fontsize=10)
    doc.save(str(h))
    doc.close()
    assert run_positional_value_twin_checks(
        _doc(a, ReportSide.A_SHARE, 1), _doc(h, ReportSide.H_SHARE, 1)
    ) == []


def test_overlay_shadow_not_reported(tmp_path) -> None:
    """overlay 形态：A 侧同位置残留原值 + 叠加新值 → 属 overlay 通道辖区，不报。"""
    a = tmp_path / "a.pdf"
    h = tmp_path / "h.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for i, num in enumerate(_BASE_NUMBERS):
        page.insert_text(fitz.Point(100, 100 + i * 20), num, fontsize=10)
    # 在第一个值上叠加新值（原值保留）
    page.insert_text(fitz.Point(100, 100), "9,999,999", fontsize=10)
    doc.save(str(a))
    doc.close()
    _make_pdf(h, [_BASE_NUMBERS])
    assert run_positional_value_twin_checks(
        _doc(a, ReportSide.A_SHARE, 1), _doc(h, ReportSide.H_SHARE, 1)
    ) == []


def test_years_and_short_numbers_ignored(tmp_path) -> None:
    a = tmp_path / "a.pdf"
    h = tmp_path / "h.pdf"
    _make_pdf(a, [["2025", "999"] + _BASE_NUMBERS])
    _make_pdf(h, [["2024", "998"] + _BASE_NUMBERS])
    # 年份与 <4 位数不比价，其余全同 → 无差异
    assert run_positional_value_twin_checks(
        _doc(a, ReportSide.A_SHARE, 1), _doc(h, ReportSide.H_SHARE, 1)
    ) == []


@pytest.mark.parametrize("page_count", [1])
def test_sparse_page_below_min_pairs_skipped(tmp_path, page_count) -> None:
    """配对数低于下限的稀疏页不比价（防低置信误判）。"""
    a = tmp_path / "a.pdf"
    h = tmp_path / "h.pdf"
    _make_pdf(a, [["1,234,567", "2,345,678", "3,000,000"]])
    _make_pdf(h, [["1,234,567", "2,345,678", "3,000,001"]])
    assert run_positional_value_twin_checks(
        _doc(a, ReportSide.A_SHARE, 1), _doc(h, ReportSide.H_SHARE, 1)
    ) == []
