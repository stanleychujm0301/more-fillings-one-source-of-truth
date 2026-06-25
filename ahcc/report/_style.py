"""报告共享样式 — KPMG 配色、严重度/分流/类型分级映射、分布统计。

供 ahcc/report/pdf.py 与 ahcc/report/excel.py 复用，避免重复定义。
颜色统一用 6 位十六进制字符串（不带 #），方便 openpyxl；reportlab 侧自行加 # 前缀。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

# ============================================================
# KPMG 调色板
# ============================================================
KPMG_BLUE = "00338D"
KPMG_MEDIUM_BLUE = "005EB8"
KPMG_LIGHT_BLUE = "0091DA"
KPMG_PURPLE = "470A68"
KPMG_GREEN = "00A3A1"

LIGHT_BG = "F5F7FB"      # 浅蓝灰底色（隔行/卡片底）
GRID_LINE = "DBE3EF"     # 浅网格线
CARD_BORDER = "C9D5E8"
TEXT_DARK = "1A1A1A"
TEXT_MUTED = "5A6473"

# ============================================================
# 克制调色板（高级感重设计）— 海军蓝 + 中性灰 + 单一暗红强调
# ============================================================
INK = "1A1A1A"           # 正文
INK_SOFT = "5A6473"      # 次要文字 / 说明
HAIRLINE = "E6EAF2"      # 极浅分隔线（替代偏重的 GRID_LINE，用于横向细线）
STRIPE = "FAFBFD"        # 近白隔行（替代偏重的 LIGHT_BG）
PANEL = "F4F6FA"         # 标签列 / 分区浅底
ALERT = "9C2A2A"         # 唯一彩色强调（暗红/oxblood）——只给最高级别与「真实」
NEUTRAL = "9AA4B2"       # 低级别灰点

# 海军蓝深→浅单色阶：图表柱、有序单色场景
MONO_RAMP = ["00338D", "27508F", "4A6FA5", "7C97BE", "AFC0D9"]

# ============================================================
# Apple + 专业金融风格新增常量
# ============================================================
FONT_FAMILY = "Microsoft YaHei"      # PDF/Excel 统一主字体
FONT_FALLBACK = "SimHei"             # PDF TTC 加载失败时回退
HEADER_BG = "FFFFFF"                 # 表头/标题背景（白底替代蓝底）
HEADER_BOTTOM = "00338D"             # 表头底部海军蓝强调线
DASHBOARD_CARD_BG = "F4F6FA"         # 仪表盘卡片背景（同 PANEL）
CHART_TITLE_COLOR = "1A1A1A"         # 图表标题墨色
FOOTER_TEXT = "8A93A3"               # 页脚柔和灰

# 严重度 / 分流的「强调色」——取代整列实色填充，仅用于文字着色 / 细色条
SEVERITY_ACCENT = {
    "critical": ALERT,
    "high": ALERT,
    "medium": INK_SOFT,
    "low": NEUTRAL,
    "info": NEUTRAL,
}

TRIAGE_ACCENT = {
    "real": ALERT,
    "expected": INK_SOFT,
    "unresolved": INK_SOFT,
}

# ============================================================
# 严重度 / 分流 / 类型 分级
# ============================================================
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

SEVERITY_COLORS = {
    "critical": "C0192B",   # 深红
    "high": "E0301E",       # 红
    "medium": "F2A900",     # 琥珀
    "low": "0091DA",        # 浅蓝
    "info": "7F7F7F",       # 灰
}

# 严重度配色对应的文字颜色（深底用白字，浅底用深字）
SEVERITY_TEXT = {
    "critical": "FFFFFF",
    "high": "FFFFFF",
    "medium": "1A1A1A",
    "low": "FFFFFF",
    "info": "FFFFFF",
}

SEVERITY_LABELS_ZH = {
    "critical": "严重",
    "high": "重大",
    "medium": "关注",
    "low": "轻微",
    "info": "提示",
}

TRIAGE_ORDER = ["real", "expected", "unresolved"]

TRIAGE_COLORS = {
    "real": "C0192B",        # 真实差异 — 红
    "expected": "00A3A1",    # 预期差异 — 青绿
    "unresolved": "F2A900",  # 待判断 — 琥珀
}

TRIAGE_TEXT = {
    "real": "FFFFFF",
    "expected": "FFFFFF",
    "unresolved": "1A1A1A",
}

TRIAGE_LABELS_ZH = {
    "real": "真实差异",
    "expected": "预期差异",
    "unresolved": "待判断",
}

DIFF_TYPE_LABELS_ZH = {
    "numeric": "数值差异",
    "cross_check": "勾稽断裂",
    "standard": "准则差异",
    "disclosure": "披露差异",
    "chart": "图表核对",
    "internal": "内部不一致",
}


def _norm(value) -> str:
    """把枚举/对象规整为小写字符串 key。"""
    return str(getattr(value, "value", value) or "").lower()


def severity_label_zh(severity) -> str:
    key = _norm(severity)
    return SEVERITY_LABELS_ZH.get(key, key)


def triage_label_zh(triage) -> str:
    key = _norm(triage)
    return TRIAGE_LABELS_ZH.get(key, key)


def diff_type_label_zh(diff_type) -> str:
    key = _norm(diff_type)
    return DIFF_TYPE_LABELS_ZH.get(key, key)


def severity_rank(severity) -> int:
    """critical=4 … info=0；未知归 -1，用于降序排序。"""
    key = _norm(severity)
    try:
        return len(SEVERITY_ORDER) - 1 - SEVERITY_ORDER.index(key)
    except ValueError:
        return -1


def severity_color(severity) -> str:
    return SEVERITY_COLORS.get(_norm(severity), "7F7F7F")


def severity_text_color(severity) -> str:
    return SEVERITY_TEXT.get(_norm(severity), "FFFFFF")


def triage_color(triage) -> str:
    return TRIAGE_COLORS.get(_norm(triage), "7F7F7F")


def triage_text_color(triage) -> str:
    return TRIAGE_TEXT.get(_norm(triage), "FFFFFF")


# ============================================================
# 克制强调助手（重设计）— 文字着色 / 细色条 / 单色阶
# ============================================================
def severity_accent(severity) -> str:
    """严重度强调色（文字/细条用）：critical/high 暗红，medium 灰，low/info 浅灰。"""
    return SEVERITY_ACCENT.get(_norm(severity), INK_SOFT)


def severity_is_high(severity) -> bool:
    """critical/high 为真 —— 决定是否画左侧暗红细色条。"""
    return _norm(severity) in ("critical", "high")


def severity_border_width(severity) -> float:
    """左侧强调条粗细（pt）：critical/high 2.5，medium 1.5，低级别 0。"""
    key = _norm(severity)
    if key in ("critical", "high"):
        return 2.5
    if key == "medium":
        return 1.5
    return 0.0


def triage_accent(triage) -> str:
    """分流强调色：real 暗红，其余灰。"""
    return TRIAGE_ACCENT.get(_norm(triage), INK_SOFT)


def triage_is_real(triage) -> bool:
    return _norm(triage) == "real"


def mono_color(index: int) -> str:
    """海军蓝单色阶取色，越靠前越深；越界回退最浅。"""
    if index < 0:
        index = 0
    return MONO_RAMP[min(index, len(MONO_RAMP) - 1)]


# ============================================================
# 分布统计
# ============================================================
def severity_distribution(diffs: Iterable) -> "OrderedDict[str, int]":
    """返回按 SEVERITY_ORDER 排列、计数 > 0 的严重度分布（中文标签）。"""
    counts = {key: 0 for key in SEVERITY_ORDER}
    for d in diffs:
        key = _norm(getattr(d, "severity", None))
        if key in counts:
            counts[key] += 1
    return OrderedDict(
        (SEVERITY_LABELS_ZH[key], counts[key]) for key in SEVERITY_ORDER if counts[key] > 0
    )


def type_distribution(diffs: Iterable) -> "OrderedDict[str, int]":
    """返回差异类型分布（中文标签），按计数降序。"""
    counts: dict[str, int] = {}
    for d in diffs:
        key = _norm(getattr(d, "diff_type", None))
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return OrderedDict(
        (DIFF_TYPE_LABELS_ZH.get(key, key), count) for key, count in ordered if count > 0
    )


def standard_citation_text(diff) -> str:
    """把 standard_reasoning.citations 拼成可读的准则引用文本。"""
    reasoning = getattr(diff, "standard_reasoning", None)
    if not reasoning or not getattr(reasoning, "citations", None):
        return ""
    lines: list[str] = []
    for cite in reasoning.citations:
        code = getattr(cite, "standard_code", "") or ""
        clause = getattr(cite, "clause", "") or ""
        title = getattr(cite, "title", "") or ""
        head = " ".join(part for part in (code, clause) if part)
        line = f"{head} — {title}" if title else head
        if line:
            lines.append(line)
    return "\n".join(lines)
