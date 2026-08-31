"""chart_textlayer 文本层图表抽取的单元测试（合成 PDF，无需真实文件）。"""

from __future__ import annotations

import fitz
import pytest

from ahcc.check.chart_textlayer import extract_chart_textlayer_data
from ahcc.schemas import ChartRegion


def _chart(page: int = 1, bbox=(80, 80, 500, 460)) -> ChartRegion:
    return ChartRegion(chart_id="test_chart", page=page, bbox=bbox)


def test_bar_chart_values_pair_with_axis_labels_below():
    """柱状图：数值在柱顶，类别标签在柱底正下方。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 95), "Revenue by Segment", fontsize=12)
    # 三根柱：柱顶数值、柱底标签
    bars = [(150, "1,234"), (250, "2,345"), (350, "3,456")]
    base_y = 400
    for x, val in bars:
        page.draw_rect(fitz.Rect(x, 250, x + 60, base_y), fill=(0.2, 0.4, 0.8))
        page.insert_text((x + 5, 240), val, fontsize=10)
    for x, _ in bars:
        page.insert_text((x - 5, base_y + 18), f"Segment{chr(65 + bars.index((x, _)))}", fontsize=9)
    try:
        result = extract_chart_textlayer_data(page, _chart())
    finally:
        doc.close()
    points = {p["label"]: p["value"] for p in result.get("data_points", [])}
    assert len(points) >= 3
    assert points.get("SegmentA") == 1234.0
    assert points.get("SegmentB") == 2345.0
    assert points.get("SegmentC") == 3456.0
    assert result.get("title") == "Revenue by Segment"


def test_pie_style_same_line_label_value_with_percent():
    """饼图/条形标签同行：标签在左、数值在右，单位识别为 %。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    rows = [("Retail", "36.2%"), ("Corporate", "45.5%"), ("Others", "18.3%")]
    y = 150
    for label, val in rows:
        page.insert_text((120, y), label, fontsize=10)
        page.insert_text((260, y), val, fontsize=10)
        y += 24
    try:
        result = extract_chart_textlayer_data(page, _chart())
    finally:
        doc.close()
    points = {p["label"]: p["value"] for p in result.get("data_points", [])}
    assert points.get("Retail") == 36.2
    assert points.get("Corporate") == 45.5
    assert result.get("unit") == "%"


def test_chinese_labels_via_builtin_cjk_font():
    """中文标签（内置 CJK 字体 china-s）同行配对。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    rows = [("零售业务", "36.2"), ("对公业务", "45.5"), ("资金业务", "18.3")]
    y = 150
    for label, val in rows:
        page.insert_text((120, y), label, fontsize=10, fontname="china-s")
        page.insert_text((260, y), val, fontsize=10)
        y += 24
    try:
        result = extract_chart_textlayer_data(page, _chart())
    finally:
        doc.close()
    points = {p["label"]: p["value"] for p in result.get("data_points", [])}
    assert points.get("零售业务") == 36.2
    assert points.get("对公业务") == 45.5


def test_empty_chart_area_returns_empty():
    """纯位图/无文本区域返回 {}（调用方据此记预警，不静默）。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(100, 100, 400, 400), fill=(0.5, 0.5, 0.5))
    try:
        result = extract_chart_textlayer_data(page, _chart())
    finally:
        doc.close()
    assert result == {}


def test_year_axis_ticks_excluded():
    """坐标轴年份刻度（2020-2024）不应成为数据点。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((120, 150), "Revenue 1,234", fontsize=10)
    page.insert_text((120, 174), "Profit 5,678", fontsize=10)
    for i, year in enumerate(["2020", "2021", "2022", "2023", "2024"]):
        page.insert_text((100 + i * 70, 430), year, fontsize=9)
    try:
        result = extract_chart_textlayer_data(page, _chart())
    finally:
        doc.close()
    values = [p["value"] for p in result.get("data_points", [])]
    assert 1234.0 in values and 5678.0 in values
    assert all(not (2019 < v < 2026 and float(v).is_integer()) for v in values)
