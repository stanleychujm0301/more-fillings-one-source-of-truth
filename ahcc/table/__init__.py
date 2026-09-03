"""表格坐标系模块 — 把「横坐标表头」（列维度）升级为与行维度同等的结构化一等公民。

四方共用（parser 产表 / profile 取值 / check 比对 / 报告呈现），因此独立成包：
- headers：表头行检测、多级展开、colspan 继承、TableGrid 构建
- semantics：列头语义归一化（期间/口径/值种类/单位 → ColumnKey）
- compat：列键兼容判定（pairable —— 所有比对通道的唯一门槛）
- models：TableGrid 运行时表示
"""

from ahcc.table.compat import is_column_key_informative, pairable, periods_pairable, same_kind
from ahcc.table.headers import (
    annotate_table,
    build_grid,
    detect_header_rows,
    grid_for,
    header_text_for_column,
)
from ahcc.table.models import TableGrid
from ahcc.table.semantics import (
    narrative_value_kind,
    parse_column_key,
    parse_kind,
    parse_period,
    parse_scope,
    parse_unit_hint,
)

__all__ = [
    "TableGrid",
    "annotate_table",
    "build_grid",
    "detect_header_rows",
    "grid_for",
    "header_text_for_column",
    "is_column_key_informative",
    "narrative_value_kind",
    "pairable",
    "parse_column_key",
    "parse_kind",
    "parse_period",
    "parse_scope",
    "parse_unit_hint",
    "periods_pairable",
    "same_kind",
]
