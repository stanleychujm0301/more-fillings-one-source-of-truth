"""Profile 模块数据模型 — 公司年报画像的三层结构：数值 + 叙述 + 结构。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ahcc.schemas import (
    Currency,
    DiffSeverity,
    Evidence,
    LocalizedString,
    ReportSide,
    TextSegment,
)


# ============================================================
# 数值画像
# ============================================================

class MetricItem(BaseModel):
    """数值画像项 — 从任意表格/文本中提取的带标签数字。"""

    canonical_key: str = Field(..., description="glossary映射后的key，未映射则用snake_case(label)")
    name: LocalizedString  # 原始标签（A股中文 / H股英文）
    value: Optional[float] = None
    value_text: Optional[str] = None  # 原始文本（保留格式）
    unit: Optional[str] = None
    currency: Optional[Currency] = None
    period: Optional[str] = None
    page: int
    evidence: Evidence
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    source: Literal["table", "text", "generic_pattern"] = "text"


class InternalInconsistency(BaseModel):
    """单报告内同一指标多次出现的值不一致。"""
    item_a: MetricItem
    item_b: MetricItem
    delta: float
    delta_pct: float


class MetricOccurrences(BaseModel):
    """同一指标的全部出现 — 保留多页/多章节出现，支持内部一致性检查。"""
    canonical_key: str
    name: LocalizedString
    primary: MetricItem                              # 最佳代表值（最大绝对值 / 最高 confidence）
    all_occurrences: list[MetricItem]                # 全量出现
    is_internally_consistent: bool = True            # 所有多现值是否一致
    internal_inconsistencies: list[InternalInconsistency] = Field(default_factory=list)


# ============================================================
# 叙述画像
# ============================================================

class NarrativeBlock(BaseModel):
    """叙述画像块。"""

    topic_label: str = Field(..., description="稳定主题名，如'mda_business_review'，未命中则 uncategorized")
    topic_key: str = Field(default="uncategorized")
    keywords: list[str] = Field(default_factory=list, description="该主题下的关键词（按TF排序）")
    segments: list[TextSegment] = Field(default_factory=list, description="属于该主题的所有原始段落")
    page_range: tuple[int, int] = Field((0, 0), description="该主题涉及的页码范围")
    word_count: int = 0  # 总字数（作为详略指标）
    summary: str = ""  # 前200字摘要（直接截取）
    evidence: list[Evidence] = Field(default_factory=list)
    detail_level: Literal["brief", "medium", "detailed"] = "brief"
    source_segments: list[str] = Field(default_factory=list)
    key_subtopics: list[str] = Field(default_factory=list)


# ============================================================
# 结构画像
# ============================================================

class ChapterNode(BaseModel):
    """章节节点 — 报告层级结构中的一节。"""

    title: LocalizedString
    section_code: Optional[str] = None  # 如 bs, pl, cf, mda, notes
    page_start: int = 0
    page_end: int = 0
    children: list["ChapterNode"] = Field(default_factory=list)
    level: int = 1  # 层级深度：1=章，2=节，3=小节
    presence_flag: bool = True


# ============================================================
# 报告画像（三层合一）
# ============================================================

class ReportProfile(BaseModel):
    """单份报告的完整画像。"""

    doc_id: str
    side: ReportSide
    total_pages: int
    metrics: list[MetricOccurrences] = Field(default_factory=list)
    narratives: list[NarrativeBlock] = Field(default_factory=list)
    structure: list[ChapterNode] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # 运行时引用原始 ReportDocument，不参与 JSON 序列化。
    # 用于披露检查等需要原始文本/表格的场景。
    source_doc: Optional[Any] = Field(default=None, exclude=True, repr=False)
    profile_summary: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# 画像比对中间结果
# ============================================================

class ProfileDiff(BaseModel):
    """画像比对中间结果 — 后续转换为 schemas.Diff。"""

    diff_type: Literal[
        "metric_mismatch",
        "metric_missing",
        "narrative_depth",
        "topic_missing",
        "structure_missing",
        "internal_inconsistency",
    ]
    severity: DiffSeverity
    triage: Literal["real", "expected", "unresolved"] = "real"
    topic: LocalizedString
    summary: LocalizedString
    canonical_key: Optional[str] = None
    topic_label: Optional[str] = Field(None, description="动态主题名（如'商誉减值测试'），非预定义")
    structure_code: Optional[str] = None
    a_pages: list[int] = Field(default_factory=list)
    h_pages: list[int] = Field(default_factory=list)
    a_value: Optional[float] = None
    h_value: Optional[float] = None
    a_word_count: Optional[int] = None
    h_word_count: Optional[int] = None
    evidence: list[Evidence] = Field(default_factory=list)
    expected: bool = False  # 是否为已知的合规差异
    rationale: Optional[str] = None  # 预期差异的解释
    source: Optional[str] = None
