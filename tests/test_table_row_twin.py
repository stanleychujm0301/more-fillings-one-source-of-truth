"""table_row_twin 无标签行孪生比对的单元测试。

覆盖验收关键路径：
- 同文档对（注入场景）：edit/swap 命中，页码与篡改值进证据
- 真实 A/H 对（版式不同）：标签 Jaccard 过低不产出
- 单位因子：千元 vs 百万元被解释，不产生噪声
- 熔断：超量差异的表整表放弃
"""

from __future__ import annotations

from ahcc.check.table_row_twin import (
    _MAX_DIFFS_PER_TABLE,
    _best_factor,
    _build_table_rows,
    run_table_row_twin_checks,
)
from ahcc.schemas import (
    FinancialTable,
    Language,
    ReportDocument,
    ReportSide,
    TableCell,
)


def _cell(row: int, col: int, text: str, header: bool = False) -> TableCell:
    return TableCell(row=row, col=col, text=text, is_header=header)


def _table(tid: str, page: int, rows: list[list[str]], header: list[str]) -> FinancialTable:
    """按行文本构造 FinancialTable（第 0 行为表头）。"""
    cells: list[TableCell] = [_cell(0, c, t, header=True) for c, t in enumerate(header)]
    for r, row in enumerate(rows, start=1):
        cells.extend(_cell(r, c, t) for c, t in enumerate(row))
    return FinancialTable(
        table_id=tid,
        title={"zh": tid, "en": tid},
        page=page,
        bbox=(0.0, 0.0, 100.0, 100.0),
        cells=cells,
    )


def _doc(side: ReportSide, tables: list[FinancialTable]) -> ReportDocument:
    return ReportDocument(
        doc_id=f"doc_{side.value}",
        side=side,
        file_path="dummy.pdf",
        total_pages=300,
        primary_language=Language.ZH,
        tables=tables,
    )


# 10 行锚点数据（满足 _MIN_MATCHED_KEYS=8 与 Jaccard 门槛）
_BASE_ROWS = [
    ["营业收入", "100,000", "95,000"],
    ["营业成本", "60,000", "58,000"],
    ["销售费用", "5,000", "4,800"],
    ["管理费用", "6,000", "5,900"],
    ["研发费用", "3,000", "2,800"],
    ["财务费用", "1,200", "1,100"],
    ["营业利润", "24,800", "23,400"],
    ["利润总额", "25,000", "23,600"],
    ["所得税费用", "5,000", "4,720"],
    ["净利润", "20,000", "18,880"],
]
_HEADER = ["项目", "2024年", "2023年"]


def _clone_rows(rows: list[list[str]]) -> list[list[str]]:
    return [list(r) for r in rows]


def test_edit_tamper_detected_with_page_and_value():
    """edit 注入（改一个数字）必须命中，且证据页码=篡改页、claimed values 含篡改值。"""
    clean = _clone_rows(_BASE_ROWS)
    dirty = _clone_rows(_BASE_ROWS)
    dirty[1][1] = "65,000"  # 营业成本 2024年：60,000 -> 65,000
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, clean, _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p010_t01", 10, dirty, _HEADER)])

    diffs, stats = run_table_row_twin_checks(doc_a, doc_h)

    assert stats.paired_tables == 1
    assert len(diffs) == 1
    d = diffs[0]
    assert d.rule_id == "table_row_value_conflict"
    assert d.triage == "real"
    assert d.a_value == 60000.0 and d.h_value == 65000.0
    pages = {e.page for e in d.evidence}
    assert pages == {10}
    assert d.diff_explanation is not None


def test_swap_tamper_detected_as_two_role_mismatches():
    """swap 注入（同行两列互换）在两个列角色上各产生一条命中。"""
    clean = _clone_rows(_BASE_ROWS)
    dirty = _clone_rows(_BASE_ROWS)
    # 净利润行 本期/上期 互换
    dirty[9][1], dirty[9][2] = dirty[9][2], dirty[9][1]
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, clean, _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p010_t01", 10, dirty, _HEADER)])

    diffs, _ = run_table_row_twin_checks(doc_a, doc_h)

    assert len(diffs) == 2
    values = {(d.a_value, d.h_value) for d in diffs}
    assert (20000.0, 18880.0) in values
    assert (18880.0, 20000.0) in values


def test_clean_identical_pair_produces_zero_diffs():
    """同文档干净对：零差异（self-check 闸）。"""
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, _clone_rows(_BASE_ROWS), _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p010_t01", 10, _clone_rows(_BASE_ROWS), _HEADER)])
    diffs, stats = run_table_row_twin_checks(doc_a, doc_h)
    assert diffs == []
    assert stats.paired_tables == 1


def test_real_pair_different_layout_produces_nothing():
    """真实 A/H 对（行项目完全不同）：标签 Jaccard 过低，不配对、不产出。"""
    h_rows = [
        ["客戶貸款及墊款", "999,000", "888,000"],
        ["投資證券", "111,000", "222,000"],
        ["同業存放款項", "333,000", "444,000"],
        ["衍生金融資產", "12,000", "13,000"],
        ["物業及設備", "21,000", "22,000"],
        ["遞延稅項資產", "31,000", "32,000"],
        ["其他資產", "41,000", "42,000"],
        ["資產總計", "1,558,000", "1,664,000"],
    ]
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, _clone_rows(_BASE_ROWS), _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p210_t01", 210, h_rows, _HEADER)])
    diffs, stats = run_table_row_twin_checks(doc_a, doc_h)
    assert diffs == []
    assert stats.paired_tables == 0


def test_unit_factor_explains_thousand_vs_million():
    """A 侧千元 vs H 侧百万元：因子 1000 解释全部行，零差异。"""
    a_rows = _clone_rows(_BASE_ROWS)
    h_rows = [[r[0], f"{float(r[1].replace(',', ''))/1000:,.3f}", f"{float(r[2].replace(',', ''))/1000:,.3f}"] for r in _BASE_ROWS]
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, a_rows, _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p210_t01", 210, h_rows, _HEADER)])
    diffs, stats = run_table_row_twin_checks(doc_a, doc_h)
    assert diffs == []
    assert stats.paired_tables == 1


def test_unit_factor_still_catches_real_conflict():
    """单位因子解释大多数行之后，真被篡改的行仍然暴露。"""
    a_rows = _clone_rows(_BASE_ROWS)
    h_rows = [[r[0], f"{float(r[1].replace(',', ''))/1000:,.3f}", f"{float(r[2].replace(',', ''))/1000:,.3f}"] for r in _BASE_ROWS]
    # 把 H 侧净利润本期值改到因子解释不了的位置（20.000 -> 22.000 百万）
    h_rows[9][1] = "22.000"
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, a_rows, _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p210_t01", 210, h_rows, _HEADER)])
    diffs, _ = run_table_row_twin_checks(doc_a, doc_h)
    assert len(diffs) == 1
    assert diffs[0].h_value == 22.0


def test_overflow_table_dropped_as_bogus_pairing():
    """差异行超过单表上限 => 判定配对错误，整表放弃（熔断）。

    注意与锚点门槛的交互：锚点率 ≥0.9 先行否决高错位表，因此熔断只在
    「大表 + 稀疏超量差异」场景触发 —— 构造 60 行表、篡改 6 行
    （锚点率 114/120 = 0.95 达标，差异 6 > 单表上限 5）。
    """
    big_rows = [[f"业务明细项目{i:02d}", f"{10000 + i * 1000:,}", f"{9000 + i * 1000:,}"] for i in range(60)]
    a_rows = _clone_rows(big_rows)
    dirty = _clone_rows(big_rows)
    for i in range(_MAX_DIFFS_PER_TABLE + 1):
        dirty[i][1] = f"{(10000 + i * 1000) * 2:,}"
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, a_rows, _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p010_t01", 10, dirty, _HEADER)])
    diffs, stats = run_table_row_twin_checks(doc_a, doc_h)
    assert diffs == []
    assert stats.dropped_overflow_tables == 1


def test_generic_labels_and_short_values_excluded():
    """泛化标签（合计）与短数字（附注号）不参与配对。"""
    rows = [["合计", "1,000", "2,000"], ["注", "5", "6"]] + _clone_rows(_BASE_ROWS)[2:]
    doc_a = _doc(ReportSide.A_SHARE, [_table("A_p010_t01", 10, _clone_rows(_BASE_ROWS), _HEADER)])
    doc_h = _doc(ReportSide.H_SHARE, [_table("H_p010_t01", 10, rows, _HEADER)])
    diffs, _ = run_table_row_twin_checks(doc_a, doc_h)
    # 「合计」行数值不同也不报（标签被排除）
    assert all(d.topic.zh != "合计" for d in diffs)


def test_best_factor_prefers_identity():
    pairs = [(100.0, 100.0), (200.0, 200.0)]
    factor, hits = _best_factor(pairs)
    assert factor == 1.0 and hits == 2


def test_build_table_rows_roles_from_header():
    t = _table("A_p010_t01", 10, _clone_rows(_BASE_ROWS), _HEADER)
    tr = _build_table_rows(t)
    assert ("营业收入", "2024") in tr.rows
    assert ("营业收入", "2023") in tr.rows
    assert tr.rows[("营业收入", "2024")].value == 100000.0
