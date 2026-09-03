"""列头语义归一化 — 把「横坐标表头」文本解析为结构化列键（ColumnKey）。

设计原则（对应「不许静默删检出」约束的算法层表达）：
- 解析不出来的字段一律留 None —— 列键明确才硬否决配对，列键缺失永远宽松回退；
- 中文简繁、英文、常见缩写统一处理（复用 align.glossary.to_simplified）；
- 「本期/上期」只给相对键 period_role，绝不猜绝对年份（锚定失败保持 None）。

词表吸收自 profile/extract_metrics._internal_value_role 与
check/key_metric_tamper._metric_value_role 的列头相关子集（两处原本就不一致，
此处统一为唯一事实源；章节/上下文类角色仍留在原函数作回退）。
"""

from __future__ import annotations

import re

from ahcc.align.glossary import to_simplified
from ahcc.schemas import ColumnKey, ValueKind

# ============================================================
# 期间解析
# ============================================================

# 「本期/上期」相对期间标记（简繁 + 中英）
_CURRENT_PERIOD_MARKERS = (
    "本期", "本年", "本年度", "当年", "报告期", "本期数",
    "current year", "current period", "reporting period", "currentyear", "currentperiod",
    "reportingyear",
)
_PRIOR_PERIOD_MARKERS = (
    "上期", "上年", "上年度", "同期", "上年数", "上期数",
    "prior year", "previous year", "comparative period", "prioryear", "previousyear",
    "priorperiod", "comparativeperiod", "lastyear",
)

_MONTH_NAMES = {
    "january": 1, "jan": 1, "一": 1, "1": 1,
    "february": 2, "feb": 2, "二": 2, "2": 2,
    "march": 3, "mar": 3, "三": 3, "3": 3,
    "april": 4, "apr": 4, "四": 4, "4": 4,
    "may": 5, "五": 5, "5": 5,
    "june": 6, "jun": 6, "六": 6, "6": 6,
    "july": 7, "jul": 7, "七": 7, "7": 7,
    "august": 8, "aug": 8, "八": 8, "8": 8,
    "september": 9, "sep": 9, "九": 9, "9": 9,
    "october": 10, "oct": 10, "十": 10, "10": 10,
    "november": 11, "nov": 11, "十一": 11, "11": 11,
    "december": 12, "dec": 12, "十二": 12, "12": 12,
}


def _year(text: str) -> str | None:
    match = re.search(r"(?:19|20)\d{2}", text)
    return match.group(0) if match else None


def parse_period(text: str | None, anchor_year: str | None = None) -> tuple[str | None, str | None]:
    """从列头文本解析期间。

    返回 (period, period_role)：
    - period 精确到月日（"2025-12-31"），季度/半年映射到期末日（Q4→12-31），
      仅年份时返回 "2025"（年粒度，不参与月日级硬比较）；
    - period_role 为 current/prior 相对键，仅当出现「本期/上期」类标记；
    - 都解析不出时 (None, None)。
    两者可并存（如「本期(2025年12月31日)」），互不否定。

    anchor_year：表级锚定年（来自 FinancialTable.period），仅供**季度/半年**列
    补全年份（「二季度」+ 2025 → 2025-06-30）；「本期/上期」绝不锚定
    （错锚会造成大面积假不匹配 → 漏报）。无锚定时季度返回裸键 "Q2"。
    """
    if not text:
        return None, None
    simplified = to_simplified(text)
    normalized = simplified.lower()
    compact = re.sub(r"\s+", "", normalized)

    period: str | None = None
    own_year = _year(normalized)
    # 季度/半年可用的年（自身年份优先，其次表级锚定年）
    anchor = own_year or (anchor_year or None)

    # 1) ISO/中文完整日期：2025-12-31 / 2025年12月31日 / 2025.12.31 / 2025/12/31
    match = re.search(r"((?:19|20)\d{2})[年\-\.//]\s*(\d{1,2})\s*[月\-\.//]\s*(\d{1,2})", compact)
    if match:
        y, m, d = match.group(1), int(match.group(2)), int(match.group(3))
        if 1 <= m <= 12 and 1 <= d <= 31:
            period = f"{y}-{m:02d}-{d:02d}"

    # 2) 英文日期：31 December 2025 / December 31, 2025 / 31 Dec 2025
    if period is None:
        match = re.search(
            r"(?:(\d{1,2})\s*([a-z]+)\s*,?\s*((?:19|20)\d{2})|([a-z]+)\s*(\d{1,2})\s*,?\s*((?:19|20)\d{2}))",
            normalized,
        )
        if match:
            if match.group(1):  # 31 December 2025
                day, mon_name, y = int(match.group(1)), match.group(2), match.group(3)
            else:  # December 31, 2025
                day, mon_name, y = int(match.group(5)), match.group(4), match.group(6)
            month = _MONTH_NAMES.get(mon_name)
            if month and 1 <= day <= 31:
                period = f"{y}-{month:02d}-{day:02d}"

    # 3) 年+月（无日）：2025年6月 / June 2025 —— 期末日不明确，保留到月
    if period is None and own_year:
        match = re.search(r"((?:19|20)\d{2})年(\d{1,2})月", compact)
        if not match:
            match = re.search(r"([a-z]{3,})\s*((?:19|20)\d{2})", normalized)
            if match and match.group(1) in _MONTH_NAMES:
                period = f"{match.group(2)}-{_MONTH_NAMES[match.group(1)]:02d}"
                match = None  # 已处理，防止下面再当纯年
        if match:
            m = int(match.group(2))
            if 1 <= m <= 12:
                period = f"{own_year}-{m:02d}"

    # 4) 季度/半年：一季度/第1季度/Q1/上半年/H1 → 期末日（有年用年，无年用锚定年，
    #    都没有则裸键 Q2/H1 —— 只用于同粒度比较，跨粒度宽松）
    if period is None:
        quarter_match = re.search(r"第?([一二三四1-4])季度|(?<![a-z])q([1-4])(?![a-z0-9])", compact)
        if quarter_match:
            q_raw = quarter_match.group(1) or quarter_match.group(2)
            quarter = {"一": 1, "二": 2, "三": 3, "四": 4}.get(q_raw) or int(q_raw)
            month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[quarter]
            period = f"{anchor}-{month_day}" if anchor else f"Q{quarter}"
        else:
            half_match = re.search(r"上半年|下半年|(?<![a-z0-9])(h[12])(?![a-z0-9])|(?<![a-z0-9])([12]h)(?![a-z0-9])", compact)
            if half_match:
                is_first_half = "上半年" in compact or "h1" in compact or "1h" in compact
                month_day = "06-30" if is_first_half else "12-31"
                period = f"{anchor}-{month_day}" if anchor else ("H1" if is_first_half else "H2")
            elif own_year:
                period = own_year  # 仅年份（锚定年不外溢：无期间信息的列头保持 None）

    # 相对期间标记（可与绝对期间并存）
    period_role: str | None = None
    if any(marker in compact for marker in _CURRENT_PERIOD_MARKERS):
        period_role = "current"
    elif any(marker in compact for marker in _PRIOR_PERIOD_MARKERS):
        period_role = "prior"

    return period, period_role


# ============================================================
# 值种类（kind）解析
# ============================================================

_KIND_MARKERS: list[tuple[ValueKind, tuple[str, ...]]] = [
    # 顺序即优先级：附注号 > 汇率 > 增减% > 增减额 > 占比 > 期限 > 平均余额
    (
        ValueKind.NOTE_REF,
        ("附注编号", "附注号", "附注", "noteno", "notenumber", "note no", "notes", "note"),
    ),
    (
        ValueKind.EXCHANGE_RATE,
        ("折算汇率", "折算人民币", "折算人民幣", "汇率", "匯率", "exchangerate", "exchange rate",
         "foreigncurrency", "foreign currency", "外币", "外幣"),
    ),
    (
        ValueKind.CHANGE_PCT,
        ("增减幅度", "增减幅", "增减百分比", "增减%", "增减率",
         "同比增减", "较上年增减", "较上年度增减", "变动率", "变动比例", "变动%", "涨跌幅",
         "变化率", "百分比变动", "change%", "change(%", "change in %",
         "%change", "percentchange", "percentagechange", "yoy"),
    ),
    (
        ValueKind.CHANGE_AMOUNT,
        ("增减额", "增加额", "减少额", "变动额", "调整金额", "调整额",
         "changeamount", "change amount", "movement in", "variance"),
    ),
    (
        ValueKind.RATIO,
        ("占比", "佔比", "比例", "所占比重", "占总", "佔總", "占比",
         "percentageoftotal", "percentage of total", "percentof", "%of", "% of",
         "percentage", "proportion", "composition"),
    ),
    (
        ValueKind.POINTS,
        ("百分点", "個百分點", "percentagepoints", "percentage points", "percentage point", "pp"),
    ),
]

# 期限/平均余额类列头：值不是科目金额而是结构性桶或均值 —— 单列 kind 无法表达
# 旧 role 的全部语义，这里只吸收与「值种类」直接相关的部分；maturity/average
# 仍由旧 role 逻辑兜底（fallback），不在此硬编码。


def parse_kind(text: str | None) -> ValueKind:
    """从列头文本判断值种类。未命中返回 MAIN（宽松默认，绝不误伤金额列）。"""
    if not text:
        return ValueKind.MAIN
    simplified = to_simplified(text)
    normalized = simplified.lower()
    compact = re.sub(r"[\s（）()]+", "", normalized).replace("％", "%")
    for kind, markers in _KIND_MARKERS:
        for marker in markers:
            marker_compact = re.sub(r"[\s（）()]+", "", marker).replace("％", "%")
            if marker_compact and marker_compact in compact:
                return kind
    return ValueKind.MAIN


# ============================================================
# 口径（scope）解析
# ============================================================

_PARENT_SCOPE_MARKERS = ("母公司", "parentcompany", "parent company", "母公司口径")
_CONSOLIDATED_SCOPE_MARKERS = ("本集团", "本集團", "合并", "合併", "consolidated", "group", "集团")


def parse_scope(text: str | None) -> str | None:
    """从列头/表标题判断口径。无法确定时 None（宽松）。

    注意「母公司」优先于「合并」检查 —— 实务中「合并/母公司」并列时
    列头只会是其一；「本行及本集团」类表述按合并处理。
    """
    if not text:
        return None
    simplified = to_simplified(text)
    normalized = simplified.lower()
    compact = re.sub(r"\s+", "", normalized)
    if any(marker in compact for marker in _PARENT_SCOPE_MARKERS):
        return "parent"
    if any(marker in compact for marker in _CONSOLIDATED_SCOPE_MARKERS):
        return "consolidated"
    return None


# ============================================================
# 单位线索
# ============================================================

_UNIT_PATTERNS = (
    "人民币百万元", "人民币千元", "人民币元", "人民币万元", "百万元", "千元", "万元", "亿元",
    "人民币千元", "rmb'm", "rmb", "hk$", "us$", "港元", "美元",
    "百萬元", "千港元", "人民幣",
)


def parse_unit_hint(text: str | None) -> str | None:
    """提取列头/表标题里的单位线索（原文保留，不换算）。"""
    if not text:
        return None
    simplified = to_simplified(text)
    normalized = simplified.lower()
    compact = re.sub(r"\s+", "", normalized)
    match = re.search(r"单位[：:](.+?)(?:$|表格|列)", compact)
    if match and match.group(1).strip():
        return match.group(1).strip()[:24]
    for pattern in _UNIT_PATTERNS:
        if pattern in compact:
            return pattern
    return None


# ============================================================
# 组合入口
# ============================================================

def parse_column_key(
    header_text: str | None,
    table_context: str | None = None,
    anchor_year: str | None = None,
) -> ColumnKey:
    """把列头文本（含多级拼接）解析为完整列键。

    table_context（表标题/表级单位）用于补充 scope 与单位线索；
    anchor_year（表级锚定年，来自 FinancialTable.period）仅供季度/半年列
    补全年份；「本期/上期」绝不锚定（防错锚导致漏报）。
    """
    raw = (header_text or "").strip()
    context = (table_context or "").strip()
    period, period_role = parse_period(raw, anchor_year=anchor_year)
    kind = parse_kind(raw)
    # 口径与单位优先取列头，缺失时从表上下文补充
    scope = parse_scope(raw) or parse_scope(context)
    unit_hint = parse_unit_hint(raw) or parse_unit_hint(context)
    return ColumnKey(
        period=period,
        period_role=period_role,
        scope=scope,
        kind=kind,
        unit_hint=unit_hint,
        raw_header=raw[:80],
    )


def narrative_value_kind(prefix_text: str | None) -> ValueKind:
    """叙述文本中数值的值种类（如「同比上升1.53个百分点」→ POINTS）。

    用于 P0-2 的叙述值标记：item 照常产出（不删检出），仅打 kind 标记，
    由配对侧的列键比较决定是否可比。
    """
    if not prefix_text:
        return ValueKind.MAIN
    simplified = to_simplified(prefix_text)
    normalized = simplified.lower()
    compact = re.sub(r"\s+", "", normalized)
    if any(m in compact for m in ("个百分点", "個百分點", "percentagepoints", "percentage points")):
        return ValueKind.POINTS
    if any(
        m in compact
        for m in ("同比上升", "同比下降", "同比增减", "同比增加", "同比减少", "较上年增减",
                  "较上年上升", "较上年下降", "yoychange", "同比变动", "较上年变动")
    ):
        return ValueKind.CHANGE_PCT
    return ValueKind.MAIN
