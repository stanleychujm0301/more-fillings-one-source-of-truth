"""表格表头检测与网格构建 — 把 FinancialTable 的扁平 cells 重建为列坐标系。

解决的两个历史问题：
1. 旧 `_header_text_for_column` 把当前行之上的**所有行**（含数据行）拼成列头，
   数据行数值污染列头（如 snippet 里的 "· 2025 2024 126,311 49,687…"）；
2. `TableCell.is_header` 布尔由各解析引擎按关键词打标，污染严重
   （含 "total"/"202" 即表头），只能作候选信号不能作判定。

检测算法：从首行开始收集「表头样」行（数值占比低 + 含表头信号词，或整行无数值），
遇到第一个数据行即停，最多 3 行 —— 表头永远是数据行上方的连续块。
"""

from __future__ import annotations

import re

from ahcc.schemas import ColumnHeader, ColumnKey, FinancialTable, TableCell
from ahcc.table.models import TableGrid
from ahcc.table.semantics import parse_column_key

# 表头强信号词（简繁统一后匹配）。注意：不能放会出现在数据值里的词 ——
# 单位词（"人民币"/"million"/"百万元"）在「人民币25亿元」「RMB100 million」
# 这类文本值里出现，放进信号表会把全文本数据行误判成表头（双语通道丢行）。
_HEADER_SIGNAL_MARKERS = (
    "项目", "科目", "附注", "单位", "期间", "年度", "本期", "上期", "本期数", "上期数",
    "金额", "占比", "增减", "变动", "比率", "余额", "账面价值", "公允价值",
    "本集团", "母公司", "合并",
    "item", "notes", "note", "unit", "period", "current", "prior", "amount",
    "balance", "percentage", "change", "year", "quarter", "december", "june",
    "march", "september", "consolidated", "parent", "group",
)

# 最多认定的表头行数（两级表头 + 单位行）
_MAX_HEADER_ROWS = 3
# 数值 cell 占比低于该值的行才有资格当表头（配合信号词）
_HEADER_NUMERIC_RATIO_MAX = 0.3

_NUMBER_RE = re.compile(r"^[（(\[\s]*-?[\d,，]+(?:\.\d+)?[）)\]%\s]*$")
# 期间单元格：裸年份/季度/半年 —— 是列头不是数值（完整日期「2025年12月31日」
# 与信号词同行出现，走信号词路径，无需在此穷举）
_PERIOD_CELL_RE = re.compile(
    r"^(?:19|20)\d{2}$|^[一二三四1-4]季度$|^第[一二三四1-4]季度$|^[上下]半年$|^q[1-4]$",
    re.IGNORECASE,
)


def _is_numeric_cell(text: str) -> bool:
    """单元格是否整体是一个数字（含千分位/括号负数/百分号）。"""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(_NUMBER_RE.match(stripped))


def _is_period_cell(text: str) -> bool:
    """单元格是否整体是一个期间（裸年/日期/季度）—— 列头而非数值。"""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(_PERIOD_CELL_RE.match(stripped))


def _row_value_ratio(cells: list[TableCell]) -> float:
    """数值（非期间）单元格占比 —— 表头行的判定基础。"""
    non_empty = [c for c in cells if (c.text or "").strip()]
    if not non_empty:
        return 0.0
    numeric = sum(
        1 for c in non_empty if _is_numeric_cell(c.text) and not _is_period_cell(c.text)
    )
    return numeric / len(non_empty)


def _row_has_header_signal(cells: list[TableCell]) -> bool:
    from ahcc.align.glossary import to_simplified

    text = to_simplified(" ".join((c.text or "") for c in cells)).lower()
    compact = re.sub(r"\s+", "", text)
    return any(marker in compact for marker in _HEADER_SIGNAL_MARKERS)


def _is_header_like(cells: list[TableCell]) -> bool:
    """行是否像表头。

    三条准入路径（任一）：
    1. 行内全部非空 cell 都是期间（"2024|2023|2022" 年份列头行）；
    2. 数值占比低且含表头信号词（"项目|2025年12月31日|附注"）；
    3. 数值占比低且解析引擎已全部标记 is_header（camelot 首行/HTML th）。
    全文本数据行（"公司债券|人民币25亿元"）无信号词不算表头 —— 双语通道
    的文本值表格不被误吞。
    """
    non_empty = [c for c in cells if (c.text or "").strip()]
    if not non_empty:
        return False
    # 1) 纯期间行
    if all(_is_period_cell(c.text) for c in non_empty):
        return True
    ratio = _row_value_ratio(cells)
    # 2) 信号词 + 低数值占比
    if ratio <= _HEADER_NUMERIC_RATIO_MAX and _row_has_header_signal(cells):
        return True
    # 3) 解析引擎标记 + 低数值占比
    if ratio <= _HEADER_NUMERIC_RATIO_MAX and all(c.is_header for c in non_empty):
        return True
    return False


def detect_header_rows(rows: dict[int, list[TableCell]]) -> list[int]:
    """检测表头行：数据行上方的连续表头样行块（最多 3 行）。

    无表头的表（快速路径重建前的历史产物）返回空列表 ——
    下游列头为空、列键 None，按宽松规则回退，不影响检出。
    """
    header_rows: list[int] = []
    for row_idx in sorted(rows):
        cells = sorted(rows[row_idx], key=lambda c: c.col)
        if not cells:
            continue
        if len(header_rows) >= _MAX_HEADER_ROWS:
            break
        if _is_header_like(cells):
            header_rows.append(row_idx)
        else:
            break
    return header_rows


def _merged_header_text(
    rows: dict[int, list[TableCell]],
    header_row_indices: list[int],
    col: int,
) -> tuple[str | None, str | None]:
    """拼出第 col 列的两级表头文本（level0, level1），colspan 空 cell 左继承。

    返回 (level0, level1)：单级表头时 level1 为 None；
    完全没有表头文本时 (None, None)。
    """
    texts_by_level: list[str] = []
    for header_row in header_row_indices:
        cells = sorted(rows.get(header_row, []), key=lambda c: c.col)
        cell_at_col = next((c for c in cells if c.col == col), None)
        text = (cell_at_col.text or "").strip() if cell_at_col else ""
        if not text:
            # colspan：继承同表头行左侧最近的非空 cell（父表头横向跨列）
            left = [c for c in cells if c.col < col and (c.text or "").strip()]
            # 仅当左侧 cell 明显跨列（右侧还有其他列）时继承，避免误吞行标签
            if left and cell_at_col is not None:
                text = (left[-1].text or "").strip()
        if text:
            texts_by_level.append(text)
    if not texts_by_level:
        return None, None
    if len(texts_by_level) == 1:
        return texts_by_level[0], None
    return texts_by_level[0], " ".join(texts_by_level[1:])


def build_grid(table: FinancialTable, label_col: int | None = None) -> TableGrid:
    """把 FinancialTable 重建为列坐标系（TableGrid）。

    纯函数：不修改入参。label_col 可由调用方传入（extract_metrics 已按行
    检测过标签列）；缺省时用首个数据行的标签列检测结果。
    """
    rows: dict[int, list[TableCell]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    header_row_indices = detect_header_rows(rows)
    data_row_indices = [r for r in sorted(rows) if r not in set(header_row_indices)]

    columns: list[ColumnHeader] = []
    all_cols = sorted({c.col for c in table.cells})
    table_context = " ".join(
        part for part in (table.title.zh, table.title.en, table.unit) if part
    )
    # 锚定年：来自表级期间（解析器/上游已填时），仅供季度/半年列补全年份
    anchor_year = None
    if table.period:
        anchor_match = re.search(r"((?:19|20)\d{2})", str(table.period))
        anchor_year = anchor_match.group(1) if anchor_match else None
    for col in all_cols:
        level0, level1 = _merged_header_text(rows, header_row_indices, col)
        if level0 is None and level1 is None:
            merged = ""
        elif level1 is None:
            merged = level0 or ""
        else:
            merged = f"{level0} {level1}"
        column_key = parse_column_key(merged, table_context, anchor_year=anchor_year) if merged else None
        columns.append(
            ColumnHeader(
                col=col,
                level0_text=level0,
                level1_text=level1,
                merged_text=merged,
                column_key=column_key,
            )
        )

    # 表级主期间：首个带期间的数值列（跳过标签列 0）
    table_period: str | None = None
    for header in columns:
        if header.col == (label_col if label_col is not None else 0):
            continue
        if header.column_key and header.column_key.period and len(header.column_key.period) > 4:
            table_period = header.column_key.period
            break
    if table_period is None:
        for header in columns:
            if header.column_key and header.column_key.period:
                table_period = header.column_key.period
                break

    return TableGrid(
        table_id=table.table_id,
        header_row_indices=header_row_indices,
        data_row_indices=data_row_indices,
        columns=columns,
        period=table_period,
        label_col=label_col,
    )


def annotate_table(table: FinancialTable, label_col: int | None = None) -> TableGrid:
    """把列头检测结果写回 FinancialTable（column_headers / header_row_indices / period）。

    幂等：重复调用结果一致。parser 层统一在 parse_report 后对每张表调用。
    """
    grid = build_grid(table, label_col=label_col)
    table.column_headers = grid.columns
    table.header_row_indices = grid.header_row_indices
    if grid.period:
        table.period = grid.period
    return grid


def grid_for(table: FinancialTable, label_col: int | None = None) -> TableGrid:
    """取表的列坐标系：优先用已注解的列头（parse_report 出口统一注解），
    未注解（测试 fixture / 旧缓存）时现场构建。"""
    if table.column_headers or table.header_row_indices:
        return TableGrid(
            table_id=table.table_id,
            header_row_indices=table.header_row_indices,
            columns=table.column_headers,
            period=table.period,
            label_col=label_col,
        )
    return build_grid(table, label_col=label_col)


def header_text_for_column(
    rows: dict[int, list[TableCell]],
    row_idx: int,
    col: int,
    header_row_indices: list[int] | None = None,
) -> str:
    """指定列的列头文本（供 snippet 与旧回退路径使用）。

    与旧版（extract_metrics._header_text_for_column）的区别：只拼接**检测出的
    表头行**，不再把当前行之上的数据行拼进列头（修复数据行污染）。
    header_row_indices 缺省时现场检测一次。
    """
    if header_row_indices is None:
        header_row_indices = detect_header_rows(rows)
    usable = [r for r in header_row_indices if r < row_idx]
    parts: list[str] = []
    for header_row in sorted(usable):
        cells = sorted(rows.get(header_row, []), key=lambda c: c.col)
        cell_at_col = next((c for c in cells if c.col == col), None)
        text = (cell_at_col.text or "").strip() if cell_at_col else ""
        if not text:
            left = [c for c in cells if c.col < col and (c.text or "").strip()]
            if left and cell_at_col is not None:
                text = (left[-1].text or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)
