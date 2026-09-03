"""表格列坐标系（ahcc.table）的单元测试。

覆盖验收关键路径：
- 表头行检测：数据行不混入列头（修复旧版「拼接上方所有行」的污染）
- 多级表头展开与 colspan 左继承
- 期间解析：到月日/季度/半年/英文日期/本期上期相对键
- 值种类：增减%/附注/占比/汇率（实例 3/6 的列头判定）
- is_header 污染免疫（数据行含 "2024"/"Total" 不被当表头）
- pairable 硬门槛矩阵（列键明确才否决，缺失永远宽松）
"""

from __future__ import annotations

from ahcc.schemas import (
    ColumnKey,
    FinancialTable,
    LocalizedString,
    TableCell,
    ValueKind,
)
from ahcc.table import (
    annotate_table,
    build_grid,
    detect_header_rows,
    header_text_for_column,
    narrative_value_kind,
    pairable,
    parse_column_key,
    parse_kind,
    parse_period,
    parse_scope,
)


def _cell(row: int, col: int, text: str, header: bool = False) -> TableCell:
    return TableCell(row=row, col=col, text=text, is_header=header)


def _table(tid: str, cells: list[TableCell], title_zh: str = "测试表") -> FinancialTable:
    return FinancialTable(
        table_id=tid,
        title=LocalizedString(zh=title_zh, en=title_zh),
        page=1,
        bbox=(0.0, 0.0, 100.0, 100.0),
        cells=cells,
    )


def _rows(cells: list[TableCell]) -> dict[int, list[TableCell]]:
    rows: dict[int, list[TableCell]] = {}
    for cell in cells:
        rows.setdefault(cell.row, []).append(cell)
    return rows


# ============================================================
# 期间解析
# ============================================================


def test_parse_period_full_date_formats():
    assert parse_period("2025年12月31日") == ("2025-12-31", None)
    assert parse_period("2025年6月30日") == ("2025-06-30", None)
    assert parse_period("2025-12-31") == ("2025-12-31", None)
    assert parse_period("2025.12.31") == ("2025-12-31", None)
    assert parse_period("31 December 2025") == ("2025-12-31", None)
    assert parse_period("December 31, 2025") == ("2025-12-31", None)
    assert parse_period("30 Jun 2025") == ("2025-06-30", None)


def test_parse_period_year_only_and_quarters():
    assert parse_period("2025年") == ("2025", None)
    assert parse_period("2025") == ("2025", None)
    # 季度/半年映射到期末日（期末口径）
    assert parse_period("2025年一季度") == ("2025-03-31", None)
    assert parse_period("二季度") == ("Q2", None)
    assert parse_period("2025Q4") == ("2025-12-31", None)
    assert parse_period("2025年上半年") == ("2025-06-30", None)


def test_parse_period_relative_roles():
    assert parse_period("本期") == (None, "current")
    assert parse_period("上期") == (None, "prior")
    assert parse_period("current year") == (None, "current")
    assert parse_period("prior year") == (None, "prior")
    # 相对标记可与绝对期间并存
    period, role = parse_period("本期(2025年12月31日)")
    assert period == "2025-12-31" and role == "current"


def test_parse_period_no_match_is_lenient():
    assert parse_period("金额") == (None, None)
    assert parse_period("") == (None, None)
    assert parse_period(None) == (None, None)


# ============================================================
# 值种类
# ============================================================


def test_parse_kind_change_pct_column():
    # 实例 3 的真实列头
    assert parse_kind("2025年比2024年增减(%)") == ValueKind.CHANGE_PCT
    assert parse_kind("增减（%）") == ValueKind.CHANGE_PCT
    assert parse_kind("同比增减") == ValueKind.CHANGE_PCT
    assert parse_kind("变动率") == ValueKind.CHANGE_PCT


def test_parse_kind_other_kinds():
    assert parse_kind("附注") == ValueKind.NOTE_REF
    assert parse_kind("Notes") == ValueKind.NOTE_REF
    assert parse_kind("折算汇率") == ValueKind.EXCHANGE_RATE
    assert parse_kind("占比") == ValueKind.RATIO
    assert parse_kind("Percentage of total") == ValueKind.RATIO
    assert parse_kind("增减额") == ValueKind.CHANGE_AMOUNT
    assert parse_kind("Change amount") == ValueKind.CHANGE_AMOUNT


def test_parse_kind_main_value_lenient():
    # 金额列绝不误伤
    assert parse_kind("2025年12月31日") == ValueKind.MAIN
    assert parse_kind("本年年末数") == ValueKind.MAIN
    assert parse_kind("金额") == ValueKind.MAIN
    assert parse_kind(None) == ValueKind.MAIN


def test_narrative_value_kind_points():
    # 实例 6：「同比上升1.53个百分点」
    assert narrative_value_kind("利息净收入占比72.92%，同比上升") == ValueKind.CHANGE_PCT
    assert narrative_value_kind("上升1.53个百分点，主要由于") == ValueKind.POINTS
    assert narrative_value_kind("营业收入为人民币921.01亿元") == ValueKind.MAIN


def test_parse_scope():
    assert parse_scope("母公司") == "parent"
    assert parse_scope("本集团") == "consolidated"
    assert parse_scope("合并") == "consolidated"
    assert parse_scope("Parent Company") == "parent"
    assert parse_scope("金额") is None


# ============================================================
# 表头行检测
# ============================================================


def test_detect_header_rows_basic():
    cells = [
        _cell(0, 0, "项目", header=True), _cell(0, 1, "2025年12月31日", header=True),
        _cell(0, 2, "2024年12月31日", header=True),
        _cell(1, 0, "资产总额"), _cell(1, 1, "7,165,319"), _cell(1, 2, "6,959,021"),
        _cell(2, 0, "负债总额"), _cell(2, 1, "6,557,877"), _cell(2, 2, "6,120,000"),
    ]
    rows = _rows(cells)
    assert detect_header_rows(rows) == [0]


def test_detect_header_rows_multilevel():
    # 两级表头：父行「2025年」跨列，子行「金额/占比」
    cells = [
        _cell(0, 0, "项目"), _cell(0, 1, "2025年"), _cell(0, 2, ""),
        _cell(1, 0, ""), _cell(1, 1, "金额"), _cell(1, 2, "占比"),
        _cell(2, 0, "营业收入"), _cell(2, 1, "100,000"), _cell(2, 2, "35.5"),
        _cell(3, 0, "营业成本"), _cell(3, 1, "60,000"), _cell(3, 2, "21.3"),
    ]
    rows = _rows(cells)
    header_rows = detect_header_rows(rows)
    assert 0 in header_rows and 1 in header_rows
    grid = build_grid(_table("T", cells))
    # colspan 左继承：col2 的 level0 继承「2025年」
    col2 = grid.header_for(2)
    assert col2 is not None
    assert col2.level0_text == "2025年"
    assert col2.level1_text == "占比"
    assert col2.column_key is not None
    assert col2.column_key.kind == ValueKind.RATIO
    # col1 = 2025年 + 金额 → MAIN
    col1 = grid.header_for(1)
    assert col1 is not None
    assert col1.column_key is not None
    assert col1.column_key.kind == ValueKind.MAIN


def test_header_detection_immune_to_is_header_pollution():
    # 数据行含 "2024"/"Total" 不应被当表头（解析引擎的关键词打标污染）
    cells = [
        _cell(0, 0, "资产总额"), _cell(0, 1, "7,165,319"),
        _cell(1, 0, "Total assets 2024"), _cell(1, 1, "6,959,021"),
        _cell(2, 0, "负债总额"), _cell(2, 1, "6,557,877"),
    ]
    rows = _rows(cells)
    assert detect_header_rows(rows) == []


def test_header_text_excludes_data_rows_above():
    # 旧 bug 修复：列头只拼表头行，不拼当前行之上的数据行
    cells = [
        _cell(0, 0, "项目", header=True), _cell(0, 1, "2025年12月31日", header=True),
        _cell(1, 0, "资产总额"), _cell(1, 1, "7,165,319"),
        _cell(2, 0, "负债总额"), _cell(2, 1, "6,557,877"),
    ]
    rows = _rows(cells)
    header = header_text_for_column(rows, 2, 1)
    assert header == "2025年12月31日"
    assert "6,557,877" not in header


def test_headerless_table_is_lenient():
    # 无表头的表（历史快速路径产物）：列头为空、列键 None
    cells = [
        _cell(0, 0, "资产总额"), _cell(0, 1, "7,165,319"),
        _cell(1, 0, "负债总额"), _cell(1, 1, "6,557,877"),
        _cell(2, 0, "股东权益"), _cell(2, 1, "1,234,567"),
        _cell(3, 0, "利润总额"), _cell(3, 1, "500,000"),
    ]
    grid = build_grid(_table("T", cells))
    assert grid.header_row_indices == []
    assert grid.key_for(1) is None
    assert grid.header_text_for(1) == ""


# ============================================================
# annotate_table 写回
# ============================================================


def test_annotate_table_populates_schema_fields():
    cells = [
        _cell(0, 0, "项目"), _cell(0, 1, "2025年12月31日"), _cell(0, 2, "2024年12月31日"),
        _cell(1, 0, "资产总额"), _cell(1, 1, "7,165,319"), _cell(1, 2, "6,959,021"),
        _cell(2, 0, "负债总额"), _cell(2, 1, "6,557,877"), _cell(2, 2, "6,120,000"),
    ]
    table = _table("A_p017_t01", cells, title_zh="主要会计数据")
    annotate_table(table)
    assert table.header_row_indices == [0]
    assert len(table.column_headers) == 3
    # 期间真正填充（此前 FinancialTable.period 从未被填充）
    assert table.period == "2025-12-31"
    col1 = next(h for h in table.column_headers if h.col == 1)
    assert col1.column_key is not None
    assert col1.column_key.period == "2025-12-31"


def test_column_key_display():
    key = parse_column_key("2025年比2024年增减(%)")
    assert "增减%" in key.display()
    key2 = parse_column_key("本期")
    assert "本期" in key2.display()


# ============================================================
# pairable 硬门槛矩阵
# ============================================================


def test_pairable_period_mismatch():
    # 实例 2：同一指标 Q4 列 vs Q2 列
    a = ColumnKey(period="2025-12-31")
    b = ColumnKey(period="2025-06-30")
    assert pairable(a, b) is False


def test_pairable_period_same():
    a = ColumnKey(period="2025-12-31")
    b = ColumnKey(period="2025-12-31")
    assert pairable(a, b) is True


def test_pairable_period_year_granularity_lenient():
    # 一侧年粒度一侧月日：比年份，无法证伪则宽松
    a = ColumnKey(period="2025")
    b = ColumnKey(period="2025-12-31")
    assert pairable(a, b) is True
    a2 = ColumnKey(period="2024")
    assert pairable(a2, b) is False


def test_pairable_kind_mismatch():
    # 实例 3：金额 vs 增减%；实例 6：金额 vs 百分点
    main = ColumnKey(kind=ValueKind.MAIN)
    pct = ColumnKey(kind=ValueKind.CHANGE_PCT)
    points = ColumnKey(kind=ValueKind.POINTS)
    note = ColumnKey(kind=ValueKind.NOTE_REF)
    ratio = ColumnKey(kind=ValueKind.RATIO)
    assert pairable(main, pct) is False
    assert pairable(main, points) is False
    assert pairable(main, note) is False
    assert pairable(main, ratio) is False
    assert pairable(main, main) is True


def test_pairable_period_role_swap():
    # 本期/上期互换（period_swap 注入场景）
    current = ColumnKey(period_role="current")
    prior = ColumnKey(period_role="prior")
    assert pairable(current, prior) is False
    assert pairable(current, current) is True


def test_pairable_scope_mismatch():
    consolidated = ColumnKey(scope="consolidated")
    parent = ColumnKey(scope="parent")
    assert pairable(consolidated, parent) is False


def test_pairable_missing_is_lenient():
    # 核心安全原则：列键缺失永远宽松（绝不因信息缺失而漏检）
    b = ColumnKey(period="2025-06-30")
    assert pairable(None, b) is True
    assert pairable(b, None) is True
    empty = ColumnKey()
    assert pairable(empty, b) is True


def test_parse_column_key_combined():
    key = parse_column_key("2025年6月30日", table_context="单位：人民币百万元")
    assert key.period == "2025-06-30"
    assert key.unit_hint is not None
    assert "百万" in key.unit_hint
