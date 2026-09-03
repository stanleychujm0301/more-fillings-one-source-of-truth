"""TableGrid — FinancialTable 的列坐标系运行时表示（纯 Pydantic，可序列化）。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ahcc.schemas import ColumnHeader, ColumnKey


class TableGrid(BaseModel):
    """一张表的列坐标系：表头行 + 各列结构化表头 + 数据行。"""

    table_id: str
    header_row_indices: list[int] = Field(default_factory=list)
    data_row_indices: list[int] = Field(default_factory=list)
    columns: list[ColumnHeader] = Field(default_factory=list, description="按 col 升序")
    period: Optional[str] = Field(None, description="表级主期间（首个带期间的数值列）")
    label_col: Optional[int] = Field(None, description="标签列（纵坐标），调用方已知时传入")

    def header_for(self, col: int) -> Optional[ColumnHeader]:
        for header in self.columns:
            if header.col == col:
                return header
        return None

    def key_for(self, col: int) -> Optional[ColumnKey]:
        header = self.header_for(col)
        return header.column_key if header else None

    def header_text_for(self, col: int) -> str:
        header = self.header_for(col)
        return header.merged_text if header else ""
