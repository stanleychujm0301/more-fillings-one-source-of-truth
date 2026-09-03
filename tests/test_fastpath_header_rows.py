"""A 股快速路径（文本层重建表格）表头行保留的单元测试。

背景：旧版 `_reconstruct_textlayer_tables` 注释写「保留表头行」，代码实际
只收数值行、表头行整行丢弃 —— 产出的表无列头，下游列键全空，
流动性覆盖率跨期假差异（e5a15ac1 实例 2）的 A 侧根因。

用 fitz 现场生成小型 PDF 验证（坐标归列是物理基础，mock 无法替代）。
"""

from __future__ import annotations

import fitz

from ahcc.config import settings
from ahcc.parser.pdf_a import _reconstruct_textlayer_tables
from ahcc.table import annotate_table


def _make_pdf(path, header: list[tuple[float, str]], rows: list[list[tuple[float, str]]],
              extra_lines: list[tuple[float, str]] | None = None) -> None:
    """在单页上排一张表：header 行 + 数据行，词按 (x, text) 落位；y 间距 20pt。"""
    doc = fitz.open()
    page = doc.new_page()
    for x, text in header:
        page.insert_text(fitz.Point(x, 100), text, fontsize=10, fontname="china-s")
    for r, row in enumerate(rows):
        y = 130 + r * 20
        for x, text in row:
            page.insert_text(fitz.Point(x, y), text, fontsize=10, fontname="china-s")
    for y, text in extra_lines or []:
        page.insert_text(fitz.Point(60, y), text, fontsize=10, fontname="china-s")
    doc.save(str(path))
    doc.close()


_HEADER = [(60, "项目"), (250, "2025年12月31日"), (450, "2024年12月31日")]
_ROWS = [
    [(60, "资产总额"), (250, "7,165,319"), (450, "6,959,021")],
    [(60, "负债总额"), (250, "6,557,877"), (450, "6,120,000")],
    [(60, "股东权益"), (250, "1,234,567"), (450, "1,100,000")],
    [(60, "利润总额"), (250, "500,000"), (450, "480,000")],
]
_PAGE_TEXT = {1: "主要会计数据 资产负债表 单位：人民币百万元"}


def test_header_band_retained_and_assigned_to_columns(tmp_path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, _HEADER, _ROWS)
    tables, pages = _reconstruct_textlayer_tables(str(pdf), _PAGE_TEXT)
    assert pages == {1}
    assert len(tables) == 1
    table = tables[0]
    cells_by_row: dict[int, dict[int, str]] = {}
    for cell in table.cells:
        cells_by_row.setdefault(cell.row, {})[cell.col] = cell.text
    # 表头行保留且 is_header
    assert 0 in cells_by_row
    header_cells = {c.col: c for c in table.cells if c.row == 0}
    assert header_cells[0].text == "项目"
    assert header_cells[0].is_header is True
    assert header_cells[1].text == "2025年12月31日"
    assert header_cells[2].text == "2024年12月31日"
    # 数据行紧随其后（行号顺延）
    assert cells_by_row[1][0] == "资产总额"
    assert cells_by_row[1][1] == "7,165,319"
    assert len(cells_by_row) == 5

    # 列头注解后获得结构化列键（期间到月日）
    annotate_table(table)
    assert table.header_row_indices == [0]
    key_2025 = next(h.column_key for h in table.column_headers if h.col == 1)
    assert key_2025 is not None and key_2025.period == "2025-12-31"
    assert table.period == "2025-12-31"


def test_paragraph_far_above_not_collected(tmp_path) -> None:
    """与表体脱节的段落行（距离 > 1.8×行距）不混入表头。"""
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, _HEADER, _ROWS, extra_lines=[(30, "本行始终坚持稳健经营理念")])
    tables, _ = _reconstruct_textlayer_tables(str(pdf), _PAGE_TEXT)
    assert len(tables) == 1
    table = tables[0]
    header_rows = {c.row for c in table.cells if c.is_header}
    assert header_rows == {0}
    texts = {c.text for c in table.cells if c.is_header}
    assert "本行始终坚持稳健经营理念" not in texts


def test_switch_off_restores_old_behavior(tmp_path, monkeypatch) -> None:
    """fast_path_header_rows=False 回退旧行为（无表头行）。"""
    monkeypatch.setattr(settings, "fast_path_header_rows", False)
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, _HEADER, _ROWS)
    tables, _ = _reconstruct_textlayer_tables(str(pdf), _PAGE_TEXT)
    assert len(tables) == 1
    table = tables[0]
    assert all(c.is_header is False for c in table.cells)
    assert {c.row for c in table.cells} == {0, 1, 2, 3}


def test_numeric_band_above_table_not_header(tmp_path) -> None:
    """首个数据行之上的「多数值行」不是表头（是另一张表/摘要数据）。"""
    pdf = tmp_path / "a.pdf"
    # y=100 处放两个纯数值词（无表头信号）→ 不收
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(250, 100), "111,111", fontsize=10)
    page.insert_text(fitz.Point(450, 100), "222,222", fontsize=10)
    for r, row in enumerate(_ROWS):
        y = 130 + r * 20
        for x, text in row:
            page.insert_text(fitz.Point(x, y), text, fontsize=10, fontname="china-s")
    doc.save(str(pdf))
    doc.close()
    tables, _ = _reconstruct_textlayer_tables(str(pdf), _PAGE_TEXT)
    assert len(tables) == 1
    # 数值行被并入数据行（旧逻辑：有数值即数据行），表头为空
    table = tables[0]
    assert all(c.is_header is False for c in table.cells)
