"""数据契约（Pydantic）— 所有模块的边界类型，P1 锁定后其他人才能并行开工。

设计原则：
- Parser 输出 ReportDocument；Aligner 输出 AlignedPair 列表；Checker 输出 Diff 列表
- 每个 Diff 必须有 evidence（页码 + bbox），无证据链则视为不合格
- 跨语言字段用 LocalizedString，避免到处写 if zh else en
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

CheckMode = Literal["ah", "h_bilingual"]


# ============================================================
# 基础类型
# ============================================================

class Language(str, Enum):
    ZH = "zh"
    EN = "en"
    BILINGUAL = "bilingual"


class ReportSide(str, Enum):
    A_SHARE = "A"  # A 股年报（CAS + 中国证监会披露规则）
    H_SHARE = "H"  # H 股年报（HKFRS + 港交所披露规则）


class Currency(str, Enum):
    CNY = "CNY"
    HKD = "HKD"
    USD = "USD"


class LocalizedString(BaseModel):
    """跨语言字段：同一概念的中英表达。"""
    zh: Optional[str] = None
    en: Optional[str] = None

    def best(self) -> str:
        return self.zh or self.en or ""


# ============================================================
# 证据链 — 任何 Diff 都必须能溯源到原 PDF 位置
# ============================================================

class Evidence(BaseModel):
    """单条证据：定位到 PDF 页/坐标，用于 UI 高亮和报告引用。"""
    side: ReportSide
    page: int = Field(..., ge=1, description="1-based PDF 页码")
    bbox: Optional[tuple[float, float, float, float]] = Field(
        None, description="左上(x0,y0)右下(x1,y1)，PDF 用户坐标"
    )
    snippet: str = Field(..., description="原文片段（≤200 字），UI 直接显示")
    section: Optional[str] = Field(None, description="所属章节，如 '合并资产负债表'")
    table_id: Optional[str] = Field(None, description="来源表格 id，如 'A_p045_t01'")
    cell_ref: Optional[tuple[int, int]] = Field(None, description="来源单元格 (row, col)，0-based")


# ============================================================
# 表格坐标系 — 列维度（横坐标表头）的结构化表示
# ============================================================

class ValueKind(str, Enum):
    """列的值种类 — 该列的数字在语义上是什么（金额还是占比/变动/附注号）。"""

    MAIN = "main_value"            # 金额/余额/发生额（默认，兼容旧 role "main_value"）
    CHANGE_PCT = "change_pct"      # 增减(%) / 同比增减 / 变动%
    CHANGE_AMOUNT = "change_amount"  # 增减额 / 变动额 / 调整金额
    RATIO = "ratio"                # 占比 / 比例 / % of total
    POINTS = "points"              # 百分点（叙述里的「上升1.53个百分点」）
    NOTE_REF = "note_reference"    # 附注号
    EXCHANGE_RATE = "exchange_rate"  # 折算汇率 / 外币折算


class ColumnKey(BaseModel):
    """列键 — 一个数值单元格在横坐标（列）上的结构化定位。

    解析不出来的字段一律 None（宽松）：列键明确才硬否决配对，
    列键缺失永远宽松回退，绝不因信息缺失而漏检。
    """

    period: Optional[str] = Field(None, description="期间，精确到月日，如 '2025-12-31'；仅年份时为 '2025'")
    period_role: Optional[Literal["current", "prior"]] = Field(
        None, description="相对期间：本期/上期（current year/prior year）。锚定失败时不猜绝对年份"
    )
    scope: Optional[Literal["consolidated", "parent", "segment"]] = Field(
        None, description="口径：合并/本集团、母公司、分部"
    )
    kind: ValueKind = ValueKind.MAIN
    unit_hint: Optional[str] = Field(None, description="列头/表级单位线索，如 '人民币百万元'")
    raw_header: str = Field("", description="归一化前的原始列头文本")

    def display(self) -> str:
        """人类可读的列口径描述，用于 snippet/报告/UI。"""
        parts: list[str] = []
        if self.period:
            parts.append(self.period)
        elif self.period_role:
            parts.append("本期" if self.period_role == "current" else "上期")
        if self.scope:
            parts.append({"consolidated": "合并", "parent": "母公司", "segment": "分部"}[self.scope])
        if self.kind != ValueKind.MAIN:
            parts.append(
                {
                    ValueKind.CHANGE_PCT: "增减%",
                    ValueKind.CHANGE_AMOUNT: "增减额",
                    ValueKind.RATIO: "占比",
                    ValueKind.POINTS: "百分点",
                    ValueKind.NOTE_REF: "附注号",
                    ValueKind.EXCHANGE_RATE: "汇率",
                }[self.kind]
            )
        if self.unit_hint:
            parts.append(self.unit_hint)
        return "·".join(parts) if parts else self.raw_header

    def is_precise_period(self) -> bool:
        """期间是否精确到月日/季度（可用于硬比较）。"""
        return bool(self.period) and len(str(self.period)) > 4


class ColumnHeader(BaseModel):
    """单列的表头结构（支持两级表头）。"""

    col: int
    level0_text: Optional[str] = Field(None, description="顶层表头（如 '2025年'），无多级时与 merged_text 一致")
    level1_text: Optional[str] = Field(None, description="次级表头（如 '金额'/'占比'）")
    merged_text: str = Field("", description="多级拼接后的完整列头文本")
    column_key: Optional[ColumnKey] = Field(None, description="归一化后的列键")


# ============================================================
# 解析层输出 — Parser 模块产出
# ============================================================

class TableCell(BaseModel):
    row: int
    col: int
    text: str
    is_header: bool = False
    bbox: Optional[tuple[float, float, float, float]] = Field(
        None, description="单元格坐标（左上x0,y0 右下x1,y1），解析引擎可得时填充"
    )


class FinancialTable(BaseModel):
    """财务表格的结构化表示。"""
    table_id: str  # 例 "A_p045_t01"
    title: LocalizedString
    page: int
    bbox: tuple[float, float, float, float]
    cells: list[TableCell]
    currency: Optional[Currency] = None
    unit: Optional[str] = Field(None, description="例 '人民币百万元'")
    period: Optional[str] = Field(None, description="例 '2024-12-31'")
    section: Optional[str] = Field(None, description="所属核心章节，如 'bs', 'pl', 'cf', 'equity'")
    column_headers: list[ColumnHeader] = Field(default_factory=list, description="各列的结构化表头")
    header_row_indices: list[int] = Field(default_factory=list, description="检测出的表头行号（0-based）")


class TextSegment(BaseModel):
    """文本段（如管理层讨论与分析中的陈述）。"""
    segment_id: str
    page: int
    bbox: tuple[float, float, float, float]
    text: str
    language: Language
    section: Optional[str] = None
    raw_text: Optional[str] = None  # 保留排版原文 — 未做 \s+ 压缩，供 LLM 对比使用。为空时回退到 text


class ChartRegion(BaseModel):
    """检测到的图表区域（饼图/柱图/趋势图）。"""
    chart_id: str
    page: int
    bbox: tuple[float, float, float, float]
    chart_type: Literal["pie", "bar", "line", "table", "unknown"] = "unknown"
    image_path: Optional[str] = None  # 截图后保存的文件路径


class ExtractionAudit(BaseModel):
    """Page-level extraction completeness audit for one source report."""

    total_pages: int = 0
    scanned_pages: list[int] = Field(default_factory=list)
    missing_pages: list[int] = Field(default_factory=list)
    blank_pages: list[int] = Field(default_factory=list)
    ocr_pages: list[int] = Field(default_factory=list)
    table_pages: list[int] = Field(default_factory=list)
    pages_with_zero_segments: list[int] = Field(default_factory=list)
    coverage_ratio: float = Field(0.0, ge=0.0, le=1.0)
    warning_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    engines: dict[str, Any] = Field(default_factory=dict)


class ReportDocument(BaseModel):
    """完整解析后的一份年报。"""
    doc_id: str
    side: ReportSide
    file_path: str
    total_pages: int
    primary_language: Language
    tables: list[FinancialTable] = []
    texts: list[TextSegment] = []
    charts: list[ChartRegion] = []
    metadata: dict[str, Any] = {}
    extraction_audit: Optional[ExtractionAudit] = None


# ============================================================
# 对齐层 — Aligner 模块产出
# ============================================================

class DataPoint(BaseModel):
    """单个关键数据点（从某份报告中抽取出的具名数值/事实）。"""
    name: LocalizedString  # 例 zh="总资产" en="Total assets"
    canonical_key: str = Field(..., description="规范化主键，用于跨报告对齐，如 'total_assets'")
    value: Optional[float] = None
    value_text: Optional[str] = None  # 原始文本，可能含千分位/单位
    unit: Optional[str] = None
    currency: Optional[Currency] = None
    period: Optional[str] = None
    column_key: Optional[ColumnKey] = Field(None, description="来源列的列键（横坐标定位）")
    row_label: Optional[str] = Field(None, description="来源行标签（纵坐标定位）")
    evidence: Evidence
    confidence: float = Field(1.0, ge=0.0, le=1.0)


class AlignedPair(BaseModel):
    """跨报告对齐：同一概念在 A/H 两份报告中的对应数据点。"""
    canonical_key: str
    topic_zh: str
    topic_en: str
    a_point: Optional[DataPoint] = None
    h_point: Optional[DataPoint] = None
    alignment_confidence: float = Field(1.0, ge=0.0, le=1.0)


# ============================================================
# 检查层 — Checker 模块产出
# ============================================================

class DiffSeverity(str, Enum):
    INFO = "info"        # 提示
    LOW = "low"          # 轻微差异（容差范围内、口径差）
    MEDIUM = "medium"    # 需关注
    HIGH = "high"        # 重大差异
    CRITICAL = "critical"  # 严重不一致，需立即追问


class DiffType(str, Enum):
    NUMERIC = "numeric"              # 模块 A：数值不等
    CROSS_CHECK = "cross_check"      # 模块 A：勾稽关系断裂
    STANDARD = "standard"            # 模块 B：准则差异（可能符合预期）
    DISCLOSURE = "disclosure"        # 模块 B：披露范围/格式差异
    CHART = "chart"                  # 模块 C：图表-表格-文本三方不一致
    INTERNAL = "internal"            # 单报告内部一致性：同一指标多次出现值不一致


class DiffScope(str, Enum):
    CROSS_REPORT = "cross_report"
    A_INTERNAL = "a_internal"
    H_INTERNAL = "h_internal"


class StandardCitation(BaseModel):
    """RAG 引用的准则条款。"""
    standard_code: str  # 例 "CAS 6" 或 "IAS 38"
    clause: str         # 例 "第 9 条" 或 "Paragraph 57"
    title: str
    snippet: str
    source: str         # 例 "kb/standards/02_rnd_capitalize.md"


class StandardReasoning(BaseModel):
    """准则差异的 AI 解读（亮点 1 核心）。"""
    expected: bool = Field(..., description="该差异是否符合 CAS↔IFRS 趋同差异预期")
    rationale: str = Field(..., description="LLM 推理理由，必须引用准则")
    citations: list[StandardCitation] = []
    confidence: float = Field(..., ge=0.0, le=1.0)
    llm_model: str  # 例 "deepseek-v4-pro"


class ChartCrossCheck(BaseModel):
    """图表三方核对结果（亮点 2 核心）。"""
    chart_value: Optional[float] = None
    table_value: Optional[float] = None
    text_value: Optional[float] = None
    chart_evidence: Optional[Evidence] = None
    table_evidence: Optional[Evidence] = None
    text_evidence: Optional[Evidence] = None
    inconsistency_count: int = Field(0, description="三者中不一致的对数")


class ReviewStatus(str, Enum):
    PENDING = "pending"      # 未审
    REVIEWED = "reviewed"    # 已审
    ACCEPTED = "accepted"    # 可接受
    FOLLOWUP = "followup"    # 需追问客户


class DiffExplanationItem(BaseModel):
    label: str
    role: Optional[str] = None
    a_value: Optional[Any] = None
    h_value: Optional[Any] = None
    delta: Optional[Any] = None
    a_page: Optional[int] = None
    h_page: Optional[int] = None
    a_snippet: Optional[str] = None
    h_snippet: Optional[str] = None


class DiffExplanation(BaseModel):
    headline: str
    issue: str
    location: str = ""
    items: list[DiffExplanationItem] = Field(default_factory=list)
    review_hint: Optional[str] = None


class Diff(BaseModel):
    """差异记录 — 报告的最小单位。"""
    diff_id: str
    diff_type: DiffType
    diff_scope: DiffScope = DiffScope.CROSS_REPORT
    severity: DiffSeverity
    triage: Literal["real", "expected", "unresolved"] = "real"
    canonical_key: Optional[str] = None
    topic: LocalizedString
    summary: LocalizedString = Field(..., description="一句话差异描述，UI 表格主列")
    diff_explanation: Optional[DiffExplanation] = None

    # 数值差异专属
    a_value: Optional[float] = None
    h_value: Optional[float] = None
    delta: Optional[float] = None
    tolerance: Optional[float] = None

    # 证据链
    evidence: list[Evidence] = []

    # 结构性分组（如「整列错乱」）：同组明细不删，仅折叠呈现
    structural_group_id: Optional[str] = Field(None, description="所属结构性分组 id")
    is_structural_detail: bool = Field(False, description="是否为结构性分组的行级明细")

    # 准则推理（仅 DiffType.STANDARD/DISCLOSURE）
    standard_reasoning: Optional[StandardReasoning] = None

    # 图表交叉（仅 DiffType.CHART）
    chart_cross: Optional[ChartCrossCheck] = None

    # 规则引用（数值/勾稽差异）
    rule_id: Optional[str] = None

    # 审计师覆盖
    review_status: ReviewStatus = ReviewStatus.PENDING
    review_note: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class DisclosureCoverageItem(BaseModel):
    """非差异披露覆盖项：记录 A/H 单边披露、跨页匹配和可回溯证据。"""

    coverage_id: str
    category: Literal["metric", "narrative", "structure", "event", "location", "depth_rule"]
    status: Literal["a_only", "h_only", "matched"]
    topic: LocalizedString
    canonical_key: Optional[str] = None
    a_pages: list[int] = Field(default_factory=list)
    h_pages: list[int] = Field(default_factory=list)
    a_evidence: list[Evidence] = Field(default_factory=list)
    h_evidence: list[Evidence] = Field(default_factory=list)
    match_confidence: float = Field(0.0, ge=0.0, le=1.0)
    note: str = ""
    source: Optional[str] = None


# ============================================================
# 任务编排 — Orchestrator 流水线
# ============================================================

class JobStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    PROFILING = "profiling"
    CHECKING = "checking"
    REPORTING = "reporting"
    DONE = "done"
    FAILED = "failed"

    @classmethod
    def _missing_(cls, value):
        if value == "aligning":
            return cls.PROFILING
        return None


class JobProgress(BaseModel):
    stage: JobStatus
    percent: int = Field(0, ge=0, le=100)
    message: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Job(BaseModel):
    job_id: str
    company_name: Optional[str] = Field(None, max_length=80)
    check_mode: CheckMode = "ah"
    owner_user_id: Optional[str] = None
    owner_display_name: Optional[str] = None
    project_group_id: Optional[str] = None
    project_group_name: Optional[str] = None
    a_file: str
    h_file: str
    status: JobStatus = JobStatus.PENDING
    progress: list[JobProgress] = []
    diffs: list[Diff] = []
    coverage_items: list[DisclosureCoverageItem] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    profile_a: Optional[dict[str, Any]] = None
    profile_h: Optional[dict[str, Any]] = None
    comparison_summary: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# 规则定义（YAML 反序列化目标）
# ============================================================

class RuleDef(BaseModel):
    """YAML 规则的内存表示。"""
    rule_id: str
    name: str
    description: str
    rule_type: Literal["numeric_equal", "cross_check", "disclosure", "tolerance"]
    targets: list[str] = Field(..., description="canonical_key 列表")
    expression: Optional[str] = Field(None, description="勾稽表达式，如 '总资产 == 流动资产 + 非流动资产'")
    tolerance: float = 0.0
    severity: DiffSeverity = DiffSeverity.MEDIUM
    enabled: bool = True
