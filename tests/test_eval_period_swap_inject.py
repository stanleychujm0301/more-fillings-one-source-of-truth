"""period_swap 注入方式（期间列互换，表头不动）的单元测试。

覆盖：
- 注入机制：互换发生在不同列（x 中心距离 ≥30pt），表头不动
- 答案清单：method=period_swap，原值/错值跨列互换
- 与 swap 的区分：swap 不要求跨列
"""

from __future__ import annotations

import fitz

from ahcc.eval.inject import inject_errors, records_to_expected


def _make_financial_pdf(path, rows: list[list[str]] | None = None) -> None:
    """单页财务表：表头 + 若干数据行，两列数值（x=250/450，列距足够大）。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(60, 100), "项目", fontsize=10, fontname="china-s")
    page.insert_text(fitz.Point(250, 100), "2025年12月31日", fontsize=10, fontname="china-s")
    page.insert_text(fitz.Point(450, 100), "2024年12月31日", fontsize=10, fontname="china-s")
    for r, (label, v1, v2) in enumerate(
        rows
        or [
            ("资产总额", "7,165,319", "6,959,021"),
            ("负债总额", "6,557,877", "6,120,000"),
            ("股东权益", "1,234,567", "1,100,000"),
            ("利润总额", "500,000", "480,000"),
        ]
    ):
        y = 130 + r * 20
        page.insert_text(fitz.Point(60, y), label, fontsize=10, fontname="china-s")
        page.insert_text(fitz.Point(250, y), v1, fontsize=10)
        page.insert_text(fitz.Point(450, y), v2, fontsize=10)
    doc.save(str(path))
    doc.close()


def test_period_swap_requires_cross_column_partner(tmp_path) -> None:
    src = tmp_path / "clean.pdf"
    out = tmp_path / "tampered.pdf"
    _make_financial_pdf(src)
    records = inject_errors(
        src, out, count=4, methods=("period_swap",), seed=3
    )
    assert records, "period_swap 应能找到跨列伙伴"
    for r in records:
        assert r.method == "period_swap"
        assert "期间列" in r.note
        # 原值与错值来自不同列（数据两列 250/450，值不相等即跨列互换）
        assert r.original_value != r.tampered_value


def test_period_swap_changes_visible_text(tmp_path) -> None:
    src = tmp_path / "clean.pdf"
    out = tmp_path / "tampered.pdf"
    _make_financial_pdf(src)
    records = inject_errors(src, out, count=4, methods=("period_swap",), seed=3)
    doc = fitz.open(str(out))
    page_text = doc[0].get_text()
    doc.close()
    for r in records:
        # 互换后：错值出现在原值位置（可见层）
        assert r.tampered_value in page_text


def test_records_to_expected_carries_method(tmp_path) -> None:
    src = tmp_path / "clean.pdf"
    out = tmp_path / "tampered.pdf"
    _make_financial_pdf(src)
    records = inject_errors(src, out, count=4, methods=("period_swap",), seed=3)
    expected = records_to_expected(records)
    assert len(expected) == len(records)
    assert all("[period_swap]" in e.description for e in expected)


def test_swap_still_works_without_column_constraint(tmp_path) -> None:
    """旧 swap 行为不受影响（不要求跨列）。"""
    src = tmp_path / "clean.pdf"
    out = tmp_path / "tampered.pdf"
    _make_financial_pdf(src)
    records = inject_errors(src, out, count=4, methods=("swap",), seed=3)
    assert records
    assert all(r.method == "swap" for r in records)
