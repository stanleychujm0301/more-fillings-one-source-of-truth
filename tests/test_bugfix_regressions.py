"""P0/P1 缺陷修复回归测试。

覆盖本轮修复的关键点：
- chart._find_table_matches 命中时不再抛 NameError，且按原始 label 建键
- run_chart_checks 预算只计入有图像的图表
- pdf_a 权益表 section code 无前导空格
- numeric._CORE_KEYS 同时包含 equity 与 total_equity
"""

from __future__ import annotations

import ahcc.check.chart as chart_module
from ahcc.check.chart import _find_table_matches, run_chart_checks
from ahcc.check import numeric as numeric_module
from ahcc.parser.pdf_a import SECTION_KEYWORDS
from ahcc.schemas import (
    ChartRegion,
    FinancialTable,
    Language,
    LocalizedString,
    ReportDocument,
    ReportSide,
    TableCell,
)


def _doc_with_table(label: str, value: str, page: int = 5) -> ReportDocument:
    table = FinancialTable(
        table_id="t1",
        title=LocalizedString(zh="业务结构", en="Business mix"),
        page=page,
        bbox=(0, 0, 1, 1),
        cells=[
            TableCell(row=0, col=0, text=label),
            TableCell(row=0, col=1, text=value),
        ],
    )
    return ReportDocument(
        doc_id="a",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        tables=[table],
    )


def test_find_table_matches_no_nameerror_and_keys_by_label() -> None:
    """P0-2：命中表格行时不再因 dp 越界抛 NameError，且 key 用原始 label。"""
    doc = _doc_with_table("零售业务", "35")
    chart = ChartRegion(chart_id="c1", page=5, bbox=(0, 0, 1, 1))
    data_points = [{"label": "零售业务", "value": 35.0}]

    matches = _find_table_matches(doc, chart, data_points)

    assert matches == {"零售业务": 35.0}


def test_find_table_matches_skips_empty_label_cell() -> None:
    """空标签单元格不应误匹配所有图表标签。"""
    doc = _doc_with_table("", "35")
    chart = ChartRegion(chart_id="c1", page=5, bbox=(0, 0, 1, 1))
    data_points = [{"label": "零售业务", "value": 35.0}]

    assert _find_table_matches(doc, chart, data_points) == {}


async def test_run_chart_checks_budget_only_counts_charts_with_image(monkeypatch) -> None:
    """P0-3：无图像的图表不应消耗 max_charts 预算，也不应触发核对。"""
    calls: list[str] = []

    async def fake_check_one(doc, chart):
        calls.append(chart.chart_id)
        return None

    monkeypatch.setattr(chart_module, "_check_one_chart", fake_check_one)

    doc = ReportDocument(
        doc_id="a",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        charts=[
            ChartRegion(chart_id="img1", page=1, bbox=(0, 0, 1, 1), image_path="x1.png"),
            ChartRegion(chart_id="noimg", page=2, bbox=(0, 0, 1, 1)),
            ChartRegion(chart_id="img2", page=3, bbox=(0, 0, 1, 1), image_path="x2.png"),
        ],
    )
    empty = ReportDocument(
        doc_id="h",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
    )

    diffs = await run_chart_checks(doc, empty, max_charts=15)

    assert diffs == []
    # 只核对有 image_path 的两张，无图像的被跳过
    assert sorted(calls) == ["img1", "img2"]


def test_equity_section_code_has_no_leading_space() -> None:
    """P0-4：权益表 section code 必须是 'equity'（无前导空格）。"""
    assert SECTION_KEYWORDS["合并所有者权益变动表"] == "equity"
    assert SECTION_KEYWORDS["合并股东权益变动表"] == "equity"


def test_core_keys_contain_both_equity_variants() -> None:
    """P0-8：equity 与 total_equity 两种规范键都需在 _CORE_KEYS 中。"""
    assert "equity" in numeric_module._CORE_KEYS
    assert "total_equity" in numeric_module._CORE_KEYS


