"""全量数值提取 — 从 ReportDocument 中提取所有带标签的数字。

提取策略：
1. 表格扫描：遍历所有表格的所有行，第一列作为标签，后续列提取数字
2. 文本扫描：遍历所有文本段落，不限页码、不限章节，提取"标签+数字"模式
3. glossary映射：优先用 glossary.lookup 映射 canonical_key，未匹配则用 snake_case(label)
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from loguru import logger

from ahcc.align.glossary import glossary, to_simplified
from ahcc.config import settings
from ahcc.parser.audit import add_audit_warning
from ahcc.profile.models import MetricItem
from ahcc.schemas import Currency, Evidence, FinancialTable, LocalizedString, ReportDocument, ReportSide, TextSegment


# ============================================================
# 通用数值解析（复用 matcher.py 逻辑）
# ============================================================

_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|"  # 千分位格式
    r"-?\d{1,15}(?:\.\d+)?"                   # 纯数字格式
)


def _parse_number(text: str) -> Optional[float]:
    """从文本中解析数值。"""
    if not text:
        return None
    text = text.strip()
    if text in ("—", "-", "–", "", "N/A", "n/a", "不适用"):
        return None

    # 括号负数
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]
    elif text.startswith("（") and text.endswith("）"):
        is_negative = True
        text = text[1:-1]

    cleaned = text.replace(",", "").replace(" ", "").replace("'", "")
    match = _NUMBER_RE.match(cleaned)
    if not match:
        return None
    try:
        val = float(match.group())
        return -val if is_negative else val
    except ValueError:
        return None


def _find_first_number(text: str, min_abs: float = 1000.0) -> tuple[Optional[float], Optional[str]]:
    """在文本中找到第一个有效的财务金额。

    跳过：
    - 年份 (1990-2035)
    - 小整数 (1-50，可能是附注编号)
    - 百分比（数字后面紧跟 %）
    """
    for match in _NUMBER_RE.finditer(text):
        raw = match.group()
        val = _parse_number(raw)
        if val is None:
            continue
        # 跳过年份
        if 1990 <= val <= 2035 and "." not in raw:
            continue
        # 跳过小整数（附注编号）
        if 1 <= val <= 50 and "," not in raw and "." not in raw:
            continue
        # 跳过百分比
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos] == "%":
            continue
        # 优先选含逗号或大数值
        if "," in raw or abs(val) >= min_abs:
            return val, raw
    # 如果都太小，返回第一个找到的数字
    for match in _NUMBER_RE.finditer(text):
        raw = match.group()
        val = _parse_number(raw)
        if val is not None:
            return val, raw
    return None, None


def _to_snake_case(text: str) -> str:
    """将中文/英文标签转为 snake_case key。"""
    # 去除标点、空格，转为小写
    cleaned = re.sub(r"[^\w一-鿿]", "_", text.strip().lower())
    # 连续下划线合并
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown_metric"


# ============================================================
# 表格数值提取
# ============================================================

def _looks_like_label(text: str) -> bool:
    """判断文本是否像是一个财务标签（而非数字或空文本）。"""
    text = text.strip()
    if not text or len(text) < 2:
        return False
    # 如果整段都是数字/逗号/括号，不是标签
    if re.match(r"^[\d,\(\)\(\)\s\-%]+$", text):
        return False
    # 如果包含至少一个中文字符或3个以上英文字母，视为标签
    has_chinese = bool(re.search(r"[一-龥]", text))
    has_english_word = len(re.findall(r"[a-zA-Z]{3,}", text)) > 0
    return has_chinese or has_english_word


def _find_label_column(cells: list) -> tuple[int, str] | None:
    """在一行的cells中找到标签列（第一列看起来像标签的列）。

    对于H股英文表格，标签通常在col=0，但如果col=0是行号/空白，
    则向后搜索第一个看起来像标签的列。
    """
    for cell in sorted(cells, key=lambda c: c.col):
        if _looks_like_label(cell.text):
            return cell.col, cell.text.strip()
    return None


def _quarter_from_text(text: str | None) -> int | None:
    if not text:
        return None
    normalized = to_simplified(text).lower()
    compact = re.sub(r"\s+", "", normalized)
    quarter_markers = (
        ("第一季度", 1), ("一季度", 1), ("第1季度", 1), ("1季度", 1),
        ("第二季度", 2), ("二季度", 2), ("第2季度", 2), ("2季度", 2),
        ("第三季度", 3), ("三季度", 3), ("第3季度", 3), ("3季度", 3),
        ("第四季度", 4), ("四季度", 4), ("第4季度", 4), ("4季度", 4),
        ("firstquarter", 1), ("1stquarter", 1),
        ("secondquarter", 2), ("2ndquarter", 2),
        ("thirdquarter", 3), ("3rdquarter", 3),
        ("fourthquarter", 4), ("4thquarter", 4),
    )
    for marker, quarter in quarter_markers:
        if marker in compact:
            return quarter
    match = re.search(r"\bq([1-4])\b|([1-4])q\b", normalized)
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _year_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"(?:19|20)\d{2}", str(text))
    return match.group(0) if match else None


def _header_text_for_column(rows: dict[int, list], row_idx: int, col: int) -> str:
    parts: list[str] = []
    for header_row in sorted(r for r in rows if r < row_idx):
        for cell in sorted(rows[header_row], key=lambda c: c.col):
            if cell.col == col and cell.text.strip():
                parts.append(cell.text.strip())
    return " ".join(parts)


def _period_for_table_cell(table: FinancialTable, header_text: str) -> str | None:
    quarter = _quarter_from_text(header_text)
    if quarter:
        year = _year_from_text(table.period) or _year_from_text(header_text)
        return f"{year}-Q{quarter}" if year else f"Q{quarter}"
    return _year_from_text(header_text) or table.period


def _table_snippet(label: str, val_text: str | None, header_text: str) -> str:
    if header_text:
        return f"[{label} · {header_text}] {val_text}"
    return f"[{label}] {val_text}"


def _extract_from_table_legacy(table: FinancialTable, side: ReportSide) -> list[MetricItem]:
    """从单个表格中提取所有带标签的数值。"""
    items: list[MetricItem] = []
    if not table.cells:
        return items

    # 按行分组
    rows: dict[int, list] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    prev_key: Optional[str] = None
    prev_label: str = ""

    for row_idx in sorted(rows.keys()):
        cells = sorted(rows[row_idx], key=lambda c: c.col)
        if not cells:
            continue

        # 找到标签列（不一定是第一列）
        label_info = _find_label_column(cells)
        if label_info is None:
            # 没有标签，但可能有数字：如果前一行有有效key，尝试继承
            if prev_key and len(cells) > 0:
                for cell in cells:
                    val, val_text = _find_first_number(cell.text)
                    if val is None:
                        continue
                    header_text = _header_text_for_column(rows, row_idx, cell.col)
                    cell_period = _period_for_table_cell(table, header_text)
                    items.append(
                        MetricItem(
                            canonical_key=prev_key,
                            name=LocalizedString(zh=prev_label, en=prev_label),
                            value=val,
                            value_text=val_text,
                            unit=table.unit,
                            currency=table.currency,
                            period=cell_period,
                            page=table.page,
                            evidence=Evidence(
                                side=side,
                                page=table.page,
                                bbox=table.bbox,
                                snippet=_table_snippet(prev_label, val_text, header_text),
                                section=table.title.zh or table.title.en,
                            ),
                            confidence=0.9,
                            source="table",
                        )
                    )
            continue

        label_col, label_text = label_info

        # 用 glossary 映射 canonical_key（支持中英文、繁体）
        label_simplified = to_simplified(label_text)
        canonical_key = glossary.lookup(label_text) or glossary.lookup(label_simplified)

        # 如果直接lookup失败，尝试去除常见前缀/后缀再lookup
        if not canonical_key:
            cleaned_label = re.sub(r"^[\s\d\-–—\.\*]+", "", label_text).strip()
            canonical_key = glossary.lookup(cleaned_label) or glossary.lookup(to_simplified(cleaned_label))

        if not canonical_key:
            # 未匹配到glossary，用通用模式
            canonical_key = _to_snake_case(label_simplified)
            if not canonical_key or canonical_key == "unknown_metric":
                prev_key = None
                prev_label = ""
                continue

        # 从该行的标签列之后的所有列提取数值
        val, val_text = None, None
        for cell in cells:
            if cell.col <= label_col:
                continue
            val, val_text = _find_first_number(cell.text)
            if val is not None:
                break

        if val is None:
            prev_key = canonical_key
            prev_label = label_text
            continue

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label_text,
            en=entry.en if entry else "",
        )

        # 判断confidence：glossary匹配=0.9，通用模式=0.5
        conf = 0.9 if (glossary.lookup(label_text) or glossary.lookup(label_simplified)) else 0.5

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=val_text,
                unit=table.unit,
                currency=table.currency,
                period=table.period,
                page=table.page,
                evidence=Evidence(
                    side=side,
                    page=table.page,
                    bbox=table.bbox,
                    snippet=f"[{label_text}] {val_text}",
                    section=table.title.zh or table.title.en,
                ),
                confidence=conf,
                source="table" if conf >= 0.9 else "generic_pattern",
            )
        )
        prev_key = canonical_key
        prev_label = label_text

    return items


def _extract_from_table(table: FinancialTable, side: ReportSide) -> list[MetricItem]:
    """Extract table metrics with column-level period context preserved."""
    items: list[MetricItem] = []
    if not table.cells:
        return items

    rows: dict[int, list] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    prev_key: Optional[str] = None
    prev_label = ""

    def append_item(
        *,
        canonical_key: str,
        label_text: str,
        value: float,
        value_text: str | None,
        cell_col: int,
        confidence: float,
        source: str,
        row_idx: int,
        name: LocalizedString | None = None,
    ) -> None:
        header_text = _header_text_for_column(rows, row_idx, cell_col)
        resolved_name = name or LocalizedString(zh=label_text, en=label_text)
        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=resolved_name,
                value=value,
                value_text=value_text,
                unit=table.unit,
                currency=table.currency,
                period=_period_for_table_cell(table, header_text),
                page=table.page,
                evidence=Evidence(
                    side=side,
                    page=table.page,
                    bbox=table.bbox,
                    snippet=_table_snippet(label_text, value_text, header_text),
                    section=table.title.zh or table.title.en,
                ),
                confidence=confidence,
                source=source,  # type: ignore[arg-type]
            )
        )

    for row_idx in sorted(rows.keys()):
        cells = sorted(rows[row_idx], key=lambda c: c.col)
        if not cells:
            continue

        label_info = _find_label_column(cells)
        if label_info is None:
            if prev_key and cells:
                for cell in cells:
                    val, val_text = _find_first_number(cell.text)
                    if val is not None:
                        append_item(
                            canonical_key=prev_key,
                            label_text=prev_label,
                            value=val,
                            value_text=val_text,
                            cell_col=cell.col,
                            confidence=0.9,
                            source="table",
                            row_idx=row_idx,
                        )
            continue

        label_col, label_text = label_info
        label_simplified = to_simplified(label_text)
        canonical_key = glossary.lookup(label_text) or glossary.lookup(label_simplified)
        direct_glossary_match = bool(canonical_key)

        if not canonical_key:
            cleaned_label = re.sub(r"^[\s\d\-–—.＊*]+", "", label_text).strip()
            canonical_key = glossary.lookup(cleaned_label) or glossary.lookup(to_simplified(cleaned_label))
            direct_glossary_match = bool(canonical_key)

        if not canonical_key:
            canonical_key = _to_snake_case(label_simplified)
            if not canonical_key or canonical_key == "unknown_metric":
                prev_key = None
                prev_label = ""
                continue

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label_text,
            en=entry.en if entry else "",
        )
        conf = 0.9 if direct_glossary_match else 0.5
        source = "table" if conf >= 0.9 else "generic_pattern"

        extracted_any = False
        for cell in cells:
            if cell.col <= label_col:
                continue
            val, val_text = _find_first_number(cell.text)
            if val is None:
                continue
            append_item(
                canonical_key=canonical_key,
                label_text=label_text,
                value=val,
                value_text=val_text,
                cell_col=cell.col,
                confidence=conf,
                source=source,
                row_idx=row_idx,
                name=name,
            )
            extracted_any = True

        prev_key = canonical_key
        prev_label = label_text
        if not extracted_any:
            continue

    return items


# ============================================================
# 文本数值提取
# ============================================================

# 通用"标签: 数字"模式 — 支持中英文
_TEXT_METRIC_RE = re.compile(
    r"([一-龥_a-zA-Z][一-龥_a-zA-Z\s\(\)（）,]*[一-龥_a-zA-Z])"
    r"[\s:：]"
    r"(-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|-?\d{1,15}(?:\.\d+)?)"
    r"([一-龥_a-zA-Z%\s]*)"
)

# 英文财务标签+数字模式（H股年报常见："Revenue   1,234,567" 带多个空格）
_ENGLISH_METRIC_RE = re.compile(
    r"\b([A-Z][a-zA-Z\s\(\)]{2,50}[a-zA-Z\)])"  # 英文标签（首字母大写）
    r"\s{2,}"  # 至少2个空格（表格列间距）
    r"(-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|-?\d{1,15}(?:\.\d+)?)"  # 数字
    r"(?:\s*([a-zA-Z%\s]+))?"  # 可选单位后缀
)


def _line_rich_segment_text(seg: TextSegment) -> str:
    raw_text = (seg.raw_text or "").strip()
    text = (seg.text or "").strip()
    if text and text.count("\n") > raw_text.count("\n"):
        return text
    return raw_text or text


def _visual_overlay_tail_numbers(seg: TextSegment) -> list[tuple[float, str]]:
    raw_text = _line_rich_segment_text(seg).strip()
    if not raw_text:
        return []
    match = re.search(
        r"(?:^|\n|\s)\d{1,4}\s*/\s*\d{1,4}\s*(?P<tail>(?:\n\s*[\(\-]?\d[\d,]*(?:\.\d+)?\)?\s*){1,12})\s*$",
        raw_text,
    )
    if not match:
        match = re.search(
            r"(?:^|\s)\d{1,4}\s*/\s*\d{1,4}\s+(?P<tail>(?:[\(\-]?\d[\d,]*(?:\.\d+)?\)?\s*){1,12})\s*$",
            raw_text,
        )
    if not match:
        return _visual_overlay_trailing_standalone_numbers(raw_text)

    numbers: list[tuple[float, str]] = []
    for number_match in _NUMBER_RE.finditer(match.group("tail")):
        raw = number_match.group()
        value = _parse_number(raw)
        if value is None:
            continue
        if 1990 <= value <= 2035 and "." not in raw:
            continue
        numbers.append((value, raw))
    return numbers[:12]


def _visual_overlay_trailing_standalone_numbers(raw_text: str) -> list[tuple[float, str]]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return []

    tail_lines: list[str] = []
    for line in reversed(lines):
        cleaned = line.replace(" ", "")
        if re.fullmatch(r"[\(\uff08-]?\d[\d,]*(?:\.\d+)?[\)\uff09]?", cleaned):
            tail_lines.append(line)
            if len(tail_lines) >= 12:
                break
            continue
        break
    if not tail_lines:
        return []

    context_lines = lines[: max(len(lines) - len(tail_lines), 0)]
    if not _looks_like_standalone_visual_overlay_context(context_lines):
        return []

    numbers: list[tuple[float, str]] = []
    for line in reversed(tail_lines):
        raw = line.strip()
        value = _parse_number(raw)
        if value is None:
            continue
        if 1990 <= value <= 2035 and "." not in raw:
            continue
        numbers.append((value, raw))
    return numbers[:12]


def _looks_like_standalone_visual_overlay_context(context_lines: list[str]) -> bool:
    context = "\n".join(context_lines)
    simplified = to_simplified(context)
    compact = re.sub(r"\s+", "", simplified).lower()
    if not compact:
        return False
    if any(marker in compact for marker in ("净资产收益率及每股收益", "每股收益计算表")):
        return False
    if any(marker in compact for marker in ("账面余额坏账准备账面价值", "按组合计提坏账准备", "按单项计提坏账准备")):
        return False
    if any(marker in compact for marker in ("产销量情况分析表", "生产量销售量", "库存量比上年增减", "主营业务分行业")):
        return False
    return any(
        marker in compact
        for marker in (
            "主要会计数据",
            "主要财务指标",
            "季度数据与已披露定期报告数据差异说明",
            "资产负债表",
            "利润表",
            "现金流量表",
            "财务报表",
            "附注为财务报表的组成部分",
        )
    )


_RAW_TABLE_OVERLAY_MAX_RELATIVE_DELTA = 0.03
_SIGNED_ABS_OVERLAY_KEYS = {
    "credit_impairment_loss",
    "investing_cash_flow",
    "operating_cash_flow",
    "financing_cash_flow",
}


def _is_small_scale_overlay_candidate(key: str) -> bool:
    return key in {
        "eps_basic",
        "eps_diluted",
        "basic_eps",
        "diluted_eps",
        "net_asset_per_share",
        "operating_cash_per_share",
        "weighted_average_roe",
        "fully_diluted_roe",
        "average_total_asset_return",
        "non_performing_loan_ratio",
        "net_interest_spread",
        "net_interest_margin",
        "cost_to_income_ratio",
    }


def _overlay_candidate_value(value: float, item: MetricItem) -> float:
    if (
        item.canonical_key in _SIGNED_ABS_OVERLAY_KEYS
        and item.value is not None
        and value < 0 < item.value
    ):
        return abs(value)
    return value


def _overlay_candidate_score(value: float, item: MetricItem) -> tuple[float, float, float] | None:
    if item.value is None:
        return None
    comparable_value = _overlay_candidate_value(value, item)
    delta = abs(comparable_value - item.value)
    if delta <= 1e-9:
        return None
    base = max(abs(comparable_value), abs(item.value), 1.0)
    ratio = delta / base
    section = ((item.evidence.section if item.evidence else "") or "").strip().lower()
    if ratio <= _RAW_TABLE_OVERLAY_MAX_RELATIVE_DELTA:
        return (ratio, delta, comparable_value)
    if section != "notes" and ratio <= 0.10:
        return (ratio, delta, comparable_value)
    if _is_small_scale_overlay_candidate(item.canonical_key) and delta <= 5.0:
        return (ratio, delta, comparable_value)
    return None


def _raw_table_cells(seg: TextSegment) -> list[str]:
    raw_text = _line_rich_segment_text(seg).strip()
    if not raw_text:
        return []
    cells = [cell.strip() for cell in re.split(r"[\r\n]+", raw_text) if cell.strip()]
    footer_markers = [
        idx for idx, cell in enumerate(cells)
        if idx >= max(4, len(cells) // 2)
        and re.fullmatch(r"\d{1,4}\s*/\s*\d{1,4}", cell)
    ]
    if footer_markers:
        return cells[:footer_markers[-1]]
    return cells


def _clean_raw_table_label(text: str) -> str:
    label = re.sub(r"\s+", " ", text or "").strip()
    label = re.sub(r"^[\(\uff08]?\d+[\)\uff09、.．]\s*", "", label).strip()
    label = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", label).strip()
    return label


def _raw_label_key(label: str, note_topic_key: str | None = None) -> str | None:
    label = _clean_raw_table_label(label)
    if not label:
        return None
    simplified = to_simplified(label)
    compact = re.sub(r"\s+", "", simplified)
    lowered = compact.lower()
    label_token = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", lowered)

    if compact.endswith("东的净资产") or "上市公司股东的净资产" in compact:
        return "parent_equity"

    if note_topic_key and compact in {"合计", "账面价值合计", "小计"}:
        return note_topic_key

    pattern_keys = (
        ("net_profit_attributable", ("归属于上市公司股东的净利润", "归属于母公司股东的净利润", "归属本行股东净利润")),
        ("revenue", ("营业总收入", "营业收入")),
        ("operating_cash_flow", ("经营活动产生的现金流量净额", "经营活动现金流量净额")),
        ("taxes_and_surcharges", ("税金及附加",)),
        ("eps_basic", ("基本每股收益",)),
        ("eps_diluted", ("稀释每股收益",)),
        ("weighted_average_roe", ("加权平均净资产收益率", "加权平均roe", "加权平均ROE")),
        ("operating_profit", ("营业利润",)),
        ("cash_equivalents_end", ("年末现金及现金等价物余额", "现金及现金等价物的年末余额")),
        ("cash_equivalents", ("货币资金", "现金及现金等价物", "现金及存放中央银行款项")),
        ("receivables", ("应收账款",)),
        ("inventory", ("存货",)),
        ("interest_net", ("利息净收入",)),
        ("commission_net", ("手续费及佣金净收入",)),
        ("credit_impairment_loss", ("信用减值损失",)),
        (
            "investing_cash_flow",
            (
                "投资活动现金流量净额",
                "投资活动产生的现金流量净额",
                "投资活动所用的现金流量净额",
                "投资活动所用产生的现金流量净额",
                "投资活动(所用)产生的现金流量净额",
            ),
        ),
        ("customer_loans", ("发放贷款和垫款",)),
        ("financial_investments", ("金融投资",)),
        ("customer_deposits", ("吸收存款",)),
        ("parent_equity", ("归属于上市公司股东的净资产", "归属净资产")),
        ("total_assets", ("总资产", "资产总计")),
        ("cost_of_revenue", ("营业成本",)),
    )
    for key, labels in pattern_keys:
        if any(
            token.lower() in lowered
            or re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", token.lower()) in label_token
            for token in labels
        ):
            return key

    return glossary.lookup(label) or glossary.lookup(simplified)


def _raw_note_topic_key(cells: list[str]) -> str | None:
    for cell in cells[:50]:
        label = _clean_raw_table_label(cell)
        if not label or label in {"项目", "人民币元"}:
            continue
        key = _raw_label_key(label)
        if key in {
            "cash_equivalents",
            "receivables",
            "inventory",
            "taxes_and_surcharges",
            "cash_equivalents_end",
            "interest_net",
            "commission_net",
            "credit_impairment_loss",
            "investing_cash_flow",
            "customer_loans",
            "financial_investments",
            "customer_deposits",
        }:
            return key
    return None


def _raw_table_value_tokens(cells: list[str], start_idx: int, note_topic_key: str | None) -> list[tuple[float, str]]:
    values: list[tuple[float, str]] = []
    for cell in cells[start_idx + 1 : min(len(cells), start_idx + 18)]:
        cleaned = cell.strip()
        if not cleaned:
            continue
        if values and _raw_label_key(cleaned, note_topic_key):
            break
        if re.fullmatch(r"[\(\uff08]?[一二三四五六七八九十0-9]+[\)\uff09]?", cleaned):
            continue
        if re.fullmatch(r"[\(\uff08]?[A-Za-z]\d*[\)\uff09]?", cleaned):
            continue
        for number_match in _NUMBER_RE.finditer(cleaned):
            raw = number_match.group()
            value = _parse_number(raw)
            if value is None:
                continue
            if 1990 <= value <= 2035 and "." not in raw:
                continue
            values.append((value, raw))
    return values


def _extract_from_raw_table_text(
    seg: TextSegment,
    side: ReportSide,
    doc_unit: Optional[str],
    doc_currency: Optional[Currency],
) -> list[MetricItem]:
    cells = _raw_table_cells(seg)
    if len(cells) < 4:
        return []

    note_topic_key = _raw_note_topic_key(cells) if (seg.section or "") == "notes" else None
    items: list[MetricItem] = []
    for idx, cell in enumerate(cells):
        label = _clean_raw_table_label(cell)
        key = _raw_label_key(label, note_topic_key)
        if not key:
            continue
        values = _raw_table_value_tokens(cells, idx, note_topic_key)
        if not values:
            continue
        entry = glossary.get_entry(key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label,
            en=entry.en if entry else "",
        )
        for value, raw in values:
            items.append(
                MetricItem(
                    canonical_key=key,
                    name=name,
                    value=value,
                    value_text=raw,
                    unit=doc_unit,
                    currency=doc_currency,
                    page=seg.page,
                    evidence=Evidence(
                        side=side,
                        page=seg.page,
                        bbox=seg.bbox,
                        snippet=f"[{label}] {raw}",
                        section=seg.section,
                    ),
                    confidence=0.82,
                    source="text",
                )
            )
    return items


def _overlay_label(item: MetricItem) -> str:
    if item.name.zh:
        return item.name.zh
    if item.name.en:
        return item.name.en
    return item.canonical_key


def _overlay_candidate_specificity(item: MetricItem) -> int:
    if item.canonical_key == "non_performing_loan_ratio":
        return 4
    if item.canonical_key == "weighted_average_roe":
        return 3
    if item.canonical_key == "parent_equity":
        return 2
    return 0


def _append_visual_overlay_items(
    items: list[MetricItem],
    seg: TextSegment,
    side: ReportSide,
    overlay_numbers: list[tuple[float, str]],
    doc_unit: Optional[str],
    doc_currency: Optional[Currency],
) -> None:
    if not overlay_numbers:
        return
    candidates = [
        item
        for item in items
        if item.value is not None
        and item.confidence >= 0.7
        and "visual overlay" not in (item.evidence.snippet or "").lower()
    ]
    used: set[int] = set()
    selected_keys: set[str] = set()
    for value, raw in reversed(overlay_numbers):
        ranked: list[tuple[float, float, float, int, int, MetricItem]] = []
        for idx, candidate in enumerate(candidates):
            if idx in used:
                continue
            if candidate.canonical_key in selected_keys:
                continue
            score = _overlay_candidate_score(value, candidate)
            if score is None:
                continue
            ratio, delta, comparable_value = score
            similarity = _number_text_similarity(raw, candidate.value_text or str(candidate.value or ""))
            section = ((candidate.evidence.section if candidate.evidence else "") or "").strip().lower()
            if (
                section in {"mda", "revenue"}
                and ratio > _RAW_TABLE_OVERLAY_MAX_RELATIVE_DELTA
                and similarity < 0.82
                and not _is_small_scale_overlay_candidate(candidate.canonical_key)
            ):
                continue
            specificity = _overlay_candidate_specificity(candidate)
            ranked.append((similarity, specificity, -ratio, -delta, -idx, comparable_value, candidate))
        if not ranked:
            continue
        _similarity, _specificity, _ratio, _delta, neg_idx, comparable_value, candidate = max(ranked)
        idx = -neg_idx
        used.add(idx)
        selected_keys.add(candidate.canonical_key)
        label = _overlay_label(candidate)
        items.append(
            MetricItem(
                canonical_key=candidate.canonical_key,
                name=candidate.name,
                value=comparable_value,
                value_text=raw,
                unit=candidate.unit or doc_unit,
                currency=candidate.currency or doc_currency,
                period=candidate.period,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[visual overlay · {label}] {raw}",
                    section=seg.section,
                ),
                confidence=0.86,
                source="generic_pattern",
            )
        )


def _number_text_similarity(left: str, right: str) -> float:
    left_digits = re.sub(r"\D+", "", left or "")
    right_digits = re.sub(r"\D+", "", right or "")
    if not left_digits or not right_digits:
        return 0.0
    return SequenceMatcher(None, left_digits, right_digits).ratio()


def _extract_from_text(seg: TextSegment, side: ReportSide, doc_unit: Optional[str], doc_currency: Optional[Currency]) -> list[MetricItem]:
    """从单个文本段中提取"标签+数字"模式。"""
    items: list[MetricItem] = []
    text = (seg.text or seg.raw_text or "").strip()
    if len(text) < 5:
        return items

    # 策略1: 通用正则匹配 "标签: 数字" 或 "标签 数字"（中英文）
    for match in _TEXT_METRIC_RE.finditer(text):
        label = match.group(1).strip()
        num_text = match.group(2)
        unit_suffix = match.group(3).strip() if match.group(3) else ""

        val = _parse_number(num_text)
        if val is None:
            continue
        # 跳过小数字（可能是编号）
        if abs(val) < 100 and "," not in num_text:
            continue

        label_simplified = to_simplified(label)
        canonical_key = glossary.lookup(label) or glossary.lookup(label_simplified)
        conf = 0.75
        if not canonical_key:
            canonical_key = _to_snake_case(label_simplified)
            conf = 0.5

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label,
            en=entry.en if entry else "",
        )

        # 合并单位
        unit = doc_unit or ""
        if unit_suffix and any(u in unit_suffix for u in ("元", "万元", "百万元", "千元", "million", "thousand", "亿")):
            unit = unit_suffix

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=num_text,
                unit=unit or None,
                currency=doc_currency,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[{label}] {num_text}",
                    section=seg.section,
                ),
                confidence=conf,
                source="text" if conf >= 0.75 else "generic_pattern",
            )
        )

    # 策略1b: 英文表格内联文本模式（H股PDF转文本后常见）
    for match in _ENGLISH_METRIC_RE.finditer(text):
        label = match.group(1).strip()
        num_text = match.group(2)
        unit_suffix = (match.group(3) or "").strip()

        val = _parse_number(num_text)
        if val is None:
            continue
        # 跳过小数字
        if abs(val) < 100 and "," not in num_text:
            continue

        canonical_key = glossary.lookup(label) or glossary.lookup(label.lower())
        conf = 0.75
        if not canonical_key:
            canonical_key = _to_snake_case(label)
            conf = 0.5

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else "",
            en=entry.en if entry else label,
        )

        unit = doc_unit or ""
        if unit_suffix and any(u in unit_suffix.lower() for u in ("million", "thousand", "元")):
            unit = unit_suffix

        # 避免重复提取
        already = any(it.canonical_key == canonical_key and abs(it.value - val) < 1 for it in items)
        if already:
            continue

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=num_text,
                unit=unit or None,
                currency=doc_currency,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[{label}] {num_text}",
                    section=seg.section,
                ),
                confidence=conf,
                source="text" if conf >= 0.75 else "generic_pattern",
            )
        )

    # 策略2: 对 glossary 中的每个术语，在该文本段中搜索匹配
    # 这是为了捕获那些不符合 "标签: 数字" 格式的数据（如表格内联文本）
    # 英文文本用原始text，中文用simplified
    text_for_matching = text if side == ReportSide.H_SHARE else to_simplified(text)
    for key in glossary.all_canonical_keys():
        entry = glossary.get_entry(key)
        if not entry:
            continue
        matched = False
        matched_label = ""
        for form in [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]:
            if not form:
                continue
            # 英文文本优先用原始大小写匹配，中文用简化字匹配
            if side == ReportSide.H_SHARE:
                if form in text_for_matching or form.lower() in text_for_matching.lower():
                    matched = True
                    matched_label = form
                    break
            else:
                if form in text_for_matching or form.lower() in text_for_matching.lower():
                    matched = True
                    matched_label = form
                    break
        if not matched:
            continue

        # 已经通过策略1提取过的跳过
        already_extracted = any(it.canonical_key == key for it in items)
        if already_extracted:
            continue

        # 在标签附近找数字
        idx = text_for_matching.find(matched_label)
        if idx < 0:
            idx = text_for_matching.lower().find(matched_label.lower())
        if idx < 0:
            continue
        window = text_for_matching[max(0, idx - 20):idx + len(matched_label) + 60]
        val, val_text = _find_first_number(window, min_abs=0)  # 不限制最小值
        if val is None:
            continue

        name = LocalizedString(zh=entry.zh_cn, en=entry.en)
        items.append(
            MetricItem(
                canonical_key=key,
                name=name,
                value=val,
                value_text=val_text,
                unit=doc_unit,
                currency=doc_currency,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[{matched_label}] {val_text}",
                    section=seg.section,
                ),
                confidence=0.75,
                source="text",
            )
        )

    overlay_numbers = _visual_overlay_tail_numbers(seg)
    if overlay_numbers:
        items.extend(_extract_from_raw_table_text(seg, side, doc_unit, doc_currency))
    _append_visual_overlay_items(items, seg, side, overlay_numbers, doc_unit, doc_currency)
    return items


def _internal_key_allows_consistency(key: str) -> bool:
    normalized = (key or "").strip().lower()
    if not normalized:
        return False
    if re.fullmatch(r"[a-z]{1,4}", normalized):
        return normalized in {"eps", "roe", "roa", "lcr", "nsfr"}
    return True


def _internal_evidence_allows_consistency(key: str, item: MetricItem) -> bool:
    if item.value is None:
        return False
    normalized_key = (key or item.canonical_key or "").strip().lower()
    context = to_simplified(_internal_context_text(item)).lower()
    compact_context = re.sub(r"\s+", "", context)
    if any(
        marker in context or marker in compact_context
        for marker in (
            "关键审计事项",
            "关键审计事宜",
            "關鍵審計事項",
            "關鍵審計事宜",
            "key audit matter",
            "key audit matters",
        )
    ):
        return False
    if normalized_key in {
        "eps_basic",
        "eps_diluted",
        "net_asset_per_share",
        "operating_cash_per_share",
        "weighted_average_roe",
        "fully_diluted_roe",
        "average_total_asset_return",
        "non_performing_loan_ratio",
        "net_interest_spread",
        "net_interest_margin",
        "cost_to_income_ratio",
        "risk_coverage_ratio",
        "liquidity_coverage_ratio",
        "net_stable_funding_ratio",
    }:
        return True

    snippet = (item.evidence.snippet if item.evidence else "") or ""
    numeric_tokens = re.findall(r"\d[\d,]*(?:\.\d+)?", snippet)
    if len(numeric_tokens) >= 12 and abs(float(item.value or 0.0)) < 1000:
        return False
    return True


def _internal_metric_pair_comparable(key: str, a: MetricItem, b: MetricItem) -> bool:
    if a.value is None or b.value is None:
        return False
    if a.period and b.period and a.period != b.period:
        return False
    a_role = _internal_value_role(a)
    b_role = _internal_value_role(b)
    if a_role != b_role:
        return False
    if a_role != "main_value":
        return False
    if a.currency and b.currency and a.currency != b.currency:
        return False
    if _internal_unit_multiplier(a.unit) != _internal_unit_multiplier(b.unit):
        return False
    a_scope = _internal_reporting_scope(a)
    b_scope = _internal_reporting_scope(b)
    if a_scope != b_scope:
        return False
    if a_scope != "main":
        return False
    return True


def _internal_value_role(item: MetricItem) -> str:
    evidence = item.evidence
    section = to_simplified(evidence.section if evidence else "").lower()
    header = _internal_value_header_text(item)
    header_text = to_simplified(header).lower()
    context_text = to_simplified(_internal_context_text(item)).lower()
    text = " ".join(part for part in (header_text, section, context_text) if part)
    compact = re.sub(r"\s+", "", text)
    header_compact = re.sub(r"\s+", "", header_text)
    snippet = evidence.snippet if evidence else ""
    snippet_number_count = len(re.findall(r"\d[\d,]*(?:\.\d+)?", snippet))

    def has(*markers: str) -> bool:
        return any(marker in text or marker in compact for marker in markers)

    def header_has(*markers: str) -> bool:
        return any(marker in header_text or marker in header_compact for marker in markers)

    if header_has(
        "note",
        "notes",
        "note no",
        "noteno",
        "\u9644\u6ce8",
        "\u9644\u6ce8\u7f16\u53f7",
            "\u9644\u6ce8\u53f7",
    ):
        return "note_reference"
    if item.period:
        years_in_context = set(re.findall(r"(?:19|20)\d{2}", compact))
        if any(year != str(item.period) for year in years_in_context) and has(
            "12\u670831\u65e5",
            "december31",
            "31december",
        ):
            return "prior_period_balance"
    if header_has(
        "\u4e0a\u5e74\u5e74\u672b\u6570",
        "\u4e0a\u5e74\u5e74\u672b\u4f59\u989d",
        "\u4e0a\u5e74\u5e74\u672b\u9918\u984d",
        "\u4e0a\u5e74\u672b\u6570",
        "\u4e0a\u5e74\u672b\u4f59\u989d",
        "\u4e0a\u5e74\u672b\u9918\u984d",
        "prior year end",
        "previous year end",
        "last year end",
    ):
        return "prior_period_balance"
    if "parent_company" in section or has(
        "\u6bcd\u516c\u53f8\u62a5\u8868",
        "\u6bcd\u516c\u53f8\u5831\u8868",
        "\u6bcd\u516c\u53f8\u53ef\u4f9b\u5206\u914d",
        "parent company statement",
        "parent company financial statements",
    ):
        return "parent_company_distribution_component"
    if has(
        "\u5e74\u521d\u672a\u5206\u914d\u5229\u6da6",
        "\u5e74\u521d\u672a\u5206\u914d\u5229\u6f64",
        "opening retained earnings",
        "retained earnings at beginning",
    ):
        return "opening_balance"
    if "equity_statement" in section or has(
        "\u5229\u6da6\u5206\u914d",
        "\u5229\u6f64\u5206\u914d",
        "\u7efc\u5408\u6536\u76ca\u603b\u989d",
        "\u7d9c\u5408\u6536\u76ca\u7e3d\u984d",
        "\u4e13\u9879\u50a8\u5907",
        "\u5c08\u9805\u5132\u5099",
        "\u63d0\u53d6\u76c8\u4f59\u516c\u79ef",
        "\u63d0\u53d6\u76c8\u9918\u516c\u7a4d",
        "\u51cf\uff1a\u5e93\u5b58\u80a1",
        "\u6e1b\uff1a\u5eab\u5b58\u80a1",
        "\u5176\u4ed6\u7efc\u5408\u6536\u76ca",
        "\u5176\u4ed6\u7d9c\u5408\u6536\u76ca",
    ):
        return "equity_statement_component"
    if "cash_flow_reconciliation" in section:
        return "cash_flow_reconciliation_component"
    if has(
        "\u73b0\u91d1\u5206\u7ea2",
        "\u73fe\u91d1\u5206\u7d05",
        "\u73b0\u91d1\u80a1\u5229",
        "\u73fe\u91d1\u80a1\u5229",
        "cash dividend",
        "dividend paid",
        "dividend distribution",
    ):
        return "dividend_component"
    if has(
        "\u8dcc\u4ef7\u51c6\u5907",
        "\u8dcc\u50f9\u6e96\u5099",
        "\u8dcc\u4ef7\u635f\u5931",
        "\u8dcc\u50f9\u640d\u5931",
        "\u51cf\u503c\u635f\u5931",
        "\u6e1b\u503c\u640d\u5931",
        "impairment",
        "write-down",
        "writedown",
    ):
        return "impairment_component"
    if has(
        "\u6298\u65e7",
        "depreciation",
    ):
        return "depreciation_component"
    if has(
        "\u79df\u8d41\u6536\u5165",
        "\u79df\u8cc3\u6536\u5165",
        "rental income",
        "lease income",
    ):
        return "rental_income_component"
    if has(
        "\u4f7f\u7528\u524d\u671f\u672a\u786e\u8ba4\u9012\u5ef6\u6240\u5f97\u7a0e\u8d44\u4ea7",
        "\u4f7f\u7528\u524d\u671f\u672a\u78ba\u8a8d\u905e\u5ef6\u6240\u5f97\u7a05\u8cc7\u7522",
        "previously unrecognised deferred tax assets",
    ):
        return "tax_utilisation_component"
    if has(
        "\u672c\u5e74\u65b0\u589e",
        "\u672c\u5e74\u589e\u52a0",
        "\u672c\u5e74\u51cf\u5c11",
        "\u8865\u52a9\u91d1\u989d",
        "\u88dc\u52a9\u91d1\u984d",
        "additions during the year",
        "decrease during the year",
    ):
        return "movement_component"
    if has(
        "\u672c\u5e74\u53d1\u751f\u989d",
        "\u672c\u5e74\u767c\u751f\u984d",
        "amount incurred during the year",
        "current year amount",
    ) and snippet_number_count >= 3:
        return "current_period_detail"
    if has(
        "\u6536\u5230\u7684\u653f\u5e9c\u8865\u52a9",
        "\u6536\u5230\u653f\u5e9c\u8865\u52a9",
        "government grants received",
        "received government grants",
    ):
        return "cash_receipt_component"
    if has(
        "\u8ba1\u5165\u5f53\u671f\u635f\u76ca",
        "\u8a08\u5165\u7576\u671f\u640d\u76ca",
        "\u8ba1\u5165\u5f53\u5e74\u635f\u76ca",
        "\u8a08\u5165\u7576\u5e74\u640d\u76ca",
        "recognised in profit or loss",
        "credited to profit or loss",
    ):
        return "profit_or_loss_component"
    if has(
        "\u6743\u76ca\u6cd5\u6838\u7b97",
        "\u6b0a\u76ca\u6cd5\u6838\u7b97",
        "\u6210\u672c\u6cd5\u6838\u7b97",
        "\u957f\u671f\u80a1\u6743\u6295\u8d44\u6536\u76ca",
        "\u9577\u671f\u80a1\u6b0a\u6295\u8cc7\u6536\u76ca",
        "\u5904\u7f6e\u4ea4\u6613\u6027\u91d1\u878d\u8d44\u4ea7\u53d6\u5f97\u7684\u6295\u8d44\u6536\u76ca",
        "\u8655\u7f6e\u4ea4\u6613\u6027\u91d1\u878d\u8cc7\u7522\u53d6\u5f97\u7684\u6295\u8cc7\u6536\u76ca",
        "\u540c\u4e1a\u5b58\u5355\u53d6\u5f97\u7684\u6295\u8d44\u6536\u76ca",
        "\u540c\u696d\u5b58\u55ae\u53d6\u5f97\u7684\u6295\u8cc7\u6536\u76ca",
        "\u8ba1\u5212\u8d44\u4ea7\u6295\u8d44\u6536\u76ca",
        "\u8a08\u5283\u8cc7\u7522\u6295\u8cc7\u6536\u76ca",
        "investment income from",
        "share of profit",
    ):
        return "investment_income_component"
    if has(
        "\u5904\u7f6e",
        "\u8655\u7f6e",
        "disposal",
        "disposed",
    ) and has(
        "\u6536\u76ca",
        "\u635f\u5931",
        "\u640d\u5931",
        "gain",
        "loss",
    ):
        return "disposal_gain_loss"
    if has(
        "\u644a\u9500",
        "\u6524\u92b7",
        "amortisation",
        "amortization",
    ):
        return "amortization_component"
    if has(
        "percentage",
        "percent",
        "asapercentage",
        "% of",
        "%of",
        "ratio",
        "proportion",
        "rate",
        "\u5360\u6bd4",
        "\u6bd4\u4f8b",
        "\u6bd4\u7387",
        "\u7387",
        "\u589e\u51cf\u5e45",
    ):
        return "ratio"
    if has(
        "materiality threshold",
        "materialitystandard",
        "\u91cd\u8981\u6027\u6807\u51c6",
        "\u91cd\u8981\u6027\u6a19\u6e96",
    ):
        return "materiality_threshold"
    if has(
        "change amount",
        "changeamount",
        "increase/decrease",
        "increase or decrease",
        "movement",
        "variance",
        "\u589e\u51cf\u989d",
        "\u53d8\u52a8\u989d",
        "\u589e\u52a0\u989d",
        "\u51cf\u5c11\u989d",
        "\u8c03\u6574\u91d1\u989d",
    ):
        return "change_amount"
    if has(
        "transferred in",
        "transfer in",
        "transferred out",
        "transfer out",
        "reclassification",
        "reclassified",
        "\u8f6c\u5165",
        "\u8f49\u5165",
        "\u8f6c\u51fa",
        "\u8f49\u51fa",
        "\u91cd\u5206\u7c7b",
        "\u91cd\u5206\u985e",
    ):
        return "transfer_movement"
    if has(
        "opening balance",
        "beginning balance",
        "balance at beginning",
        "initial balance",
        "\u671f\u521d\u4f59\u989d",
        "\u671f\u521d\u9918\u984d",
        "\u5e74\u521d\u4f59\u989d",
        "\u5e74\u521d\u9918\u984d",
    ):
        return "opening_balance"
    if has(
        "impact on profit",
        "effect on profit",
        "effect on profit before tax",
        "impact on profit before tax",
        "\u5bf9\u5229\u6da6\u603b\u989d\u7684\u5f71\u54cd",
        "\u5c0d\u5229\u6f64\u7e3d\u984d\u7684\u5f71\u97ff",
        "\u5bf9\u5f53\u671f\u5229\u6da6\u7684\u5f71\u54cd",
        "\u5c0d\u7576\u671f\u5229\u6f64\u7684\u5f71\u97ff",
    ):
        return "profit_effect"
    if "equity_statement" in section and has(
        "treasury shares",
        "treasury stock",
        "retained earnings",
        "surplus reserve",
        "special reserve",
        "other comprehensive income",
        "non-controlling interests",
        "\u5e93\u5b58\u80a1",
        "\u5eab\u5b58\u80a1",
        "\u672a\u5206\u914d\u5229\u6da6",
        "\u672a\u5206\u914d\u5229\u6f64",
        "\u76c8\u4f59\u516c\u79ef",
        "\u76c8\u9918\u516c\u7a4d",
        "\u4e13\u9879\u50a8\u5907",
        "\u5c08\u9805\u5132\u5099",
        "\u5176\u4ed6\u7efc\u5408\u6536\u76ca",
        "\u5176\u4ed6\u7d9c\u5408\u6536\u76ca",
    ):
        return "equity_statement_component"
    if (
        header_has(
            "book value",
            "carrying amount",
            "\u8d26\u9762\u4ef7\u503c",
            "\u8cec\u9762\u50f9\u503c",
        )
        and has(
            "restricted",
            "pledged",
            "limited ownership",
            "\u53d7\u9650",
            "\u6240\u6709\u6743\u53d7\u5230\u9650\u5236",
            "\u6240\u6709\u6b0a\u53d7\u5230\u9650\u5236",
            "\u53d7\u9650\u539f\u56e0",
        )
    ):
        return "restricted_book_value"
    if has(
        "foreign currency",
        "exchange rate",
        "translated rmb",
        "translated renminbi",
        "\u5916\u5e01",
        "\u5916\u5e63",
        "\u6298\u7b97\u6c47\u7387",
        "\u6298\u7b97\u532f\u7387",
        "\u6298\u7b97\u4eba\u6c11\u5e01\u4f59\u989d",
        "\u6298\u7b97\u4eba\u6c11\u5e63\u9918\u984d",
        "\u5e74\u672b\u5916\u5e01\u4f59\u989d",
        "\u5e74\u672b\u5916\u5e63\u9918\u984d",
    ):
        return "foreign_currency_translation"
    if has(
        "maturity",
        "within3months",
        "within 3 months",
        "3 months or less",
        "past due",
        "indefinite",
        "undated",
        "\u671f\u9650",
        "3\u4e2a\u6708\u5185",
        "\u4e09\u4e2a\u6708\u5185",
        "\u5df2\u903e\u671f",
        "\u65e0\u671f\u9650",
    ):
        return "maturity_bucket"
    if has(
        "average balance",
        "averagebalance",
        "\u5e73\u5747\u4f59\u989d",
    ):
        return "average_balance"
    if header_has(
        "interest income",
        "interest expense",
        "interestincome",
        "interestexpense",
        "\u5229\u606f\u6536\u5165",
        "\u5229\u606f\u652f\u51fa",
    ):
        return "interest_component"
    return "main_value"


def _internal_value_header_text(item: MetricItem) -> str:
    evidence = item.evidence
    snippet = evidence.snippet if evidence else ""
    match = re.search(r"\[[^\]]*?\s*\u00b7\s*([^\]]+)\]", snippet)
    if match:
        return match.group(1).strip()
    return snippet


def _internal_context_text(item: MetricItem) -> str:
    evidence = item.evidence
    parts = [
        item.name.zh,
        item.name.en,
        item.period,
        item.value_text,
        evidence.section if evidence else None,
        evidence.snippet if evidence else None,
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _has_internal_marker(text: str, markers: tuple[str, ...]) -> bool:
    compact = re.sub(r"\s+", "", text or "").lower()
    return any(marker in text or marker in compact for marker in markers)


def _internal_reporting_scope(item: MetricItem) -> str:
    text = _internal_context_text(item)
    if _has_internal_marker(
        text,
        (
            "quarterly",
            "firstquarter",
            "secondquarter",
            "thirdquarter",
            "fourthquarter",
            "q1",
            "q2",
            "q3",
            "q4",
            "分季度",
            "季度",
            "一季度",
            "二季度",
            "三季度",
            "四季度",
        ),
    ):
        return "quarterly"
    if _has_internal_marker(
        text,
        (
            "subsidiary",
            "subsidiaries",
            "associate",
            "jointventure",
            "companylimited",
            "co.,ltd",
            "limitedcompany",
            "branch",
            "counterparty",
            "relatedparty",
            "子公司",
            "附属公司",
            "联营",
            "合营",
            "有限公司",
            "股份有限公司",
            "分支机构",
            "分行",
            "关联方",
            "交易对手",
        ),
    ):
        return "entity_detail"
    if _has_internal_marker(text, ("segment", "operatingsegment", "businesssegment", "分部", "经营分部", "业务分部")):
        return "segment"
    if _has_internal_marker(text, ("fairvalue", "riskexposure", "公允价值", "风险敞口")):
        return "valuation_detail"
    section = (item.evidence.section if item.evidence else "") or ""
    if len(re.findall(r"\d[\d,]*(?:\.\d+)?", section)) >= 2:
        return "line_detail"
    return "main"


def _internal_unit_multiplier(unit: str | None) -> float:
    if not unit:
        return 1.0
    lower = unit.lower()
    if "million" in lower or "百万" in unit or "百萬" in unit:
        return 1_000_000.0
    if "thousand" in lower or "千元" in unit:
        return 1_000.0
    if "亿元" in unit or "億" in unit:
        return 100_000_000.0
    if "万元" in unit or "萬" in unit:
        return 10_000.0
    return 1.0


# ============================================================
# 主入口
# ============================================================

def extract_metrics(doc: ReportDocument) -> list[MetricItem]:
    """从 ReportDocument 中提取全量数值指标。

    策略：
    1. 遍历所有表格（不限页码）提取行标签+数值
    2. 遍历所有文本段落（不限页码、不限章节）提取"标签+数值"
    3. 同一 canonical_key 同一页去重，保留 confidence 最高的
    """
    all_items: list[MetricItem] = []
    doc_unit = doc.metadata.get("unit")
    doc_currency = doc.metadata.get("currency")
    if isinstance(doc_currency, str):
        try:
            doc_currency = Currency(doc_currency)
        except ValueError:
            doc_currency = None

    side_label = "A股" if doc.side == ReportSide.A_SHARE else "H股"
    logger.info(f"[{side_label}] 开始提取指标: {len(doc.tables)} 表, {len(doc.texts)} 文本段, 单位={doc_unit}")

    # 1. 表格提取
    table_items_count = 0
    for table in doc.tables:
        items = _extract_from_table(table, doc.side)
        all_items.extend(items)
        table_items_count += len(items)

    # 2. 文本提取
    text_items_count = 0
    for seg in doc.texts:
        items = _extract_from_text(seg, doc.side, doc_unit, doc_currency)
        all_items.extend(items)
        text_items_count += len(items)

    logger.info(f"[{side_label}] 原始提取: 表格={table_items_count}, 文本={text_items_count}, 总计={len(all_items)}")

    # 3. 同页去重：同一 canonical_key + page + value 保留 confidence 最高的。
    # 同页同指标但数值不同需要保留，用于识别可见 overlay 与文本层不一致。
    seen: dict[tuple[str, int, str, float | str | None], MetricItem] = {}
    for item in all_items:
        if item.value is None:
            value_marker: float | str | None = item.value_text
        else:
            value_marker = round(float(item.value), 6)
        key = (item.canonical_key, item.page, item.period or "", value_marker)
        if key not in seen or item.confidence > seen[key].confidence:
            seen[key] = item

    result = list(seen.values())
    logger.info(f"[{side_label}] 同页去重后: {len(result)} 个指标")

    # 4. 分组收集：同一 canonical_key 保留所有出现（不再跨页合并为单一值）
    from ahcc.profile.models import InternalInconsistency, MetricOccurrences

    grouped: dict[str, list[MetricItem]] = {}
    for item in result:
        grouped.setdefault(item.canonical_key, []).append(item)

    # 5. 构建 MetricOccurrences（选 primary + 内部一致性检查）
    final: list[MetricOccurrences] = []
    for key, items in grouped.items():
        # primary = 绝对值最大 + confidence 最高的
        primary = max(items, key=lambda m: (abs(m.value or 0), m.confidence))
        occ = MetricOccurrences(
            canonical_key=key,
            name=primary.name,
            primary=primary,
            all_occurrences=items,
        )
        # 内部一致性检查：
        #   - 只比较高置信表格提取，减少正文碎片误报；
        #   - 同页不同值直接报告；
        #   - 核心指标跨页出现大幅不同值，也报告为单报告自身画像不一致。
        if not _internal_key_allows_consistency(key):
            final.append(occ)
            continue

        reliable = [
            m for m in items
            if (
                m.value is not None
                and m.confidence >= 0.9
                and m.source == "table"
                and _internal_evidence_allows_consistency(key, m)
            )
        ]

        count = 0
        for i, a in enumerate(reliable):
            for b in reliable[i + 1:]:
                if not _internal_metric_pair_comparable(key, a, b):
                    continue
                if round(a.value or 0.0, 2) == round(b.value or 0.0, 2):
                    continue
                delta = abs((a.value or 0.0) - (b.value or 0.0))
                base = max(abs(a.value or 0.0), abs(b.value or 0.0), 1e-9)
                delta_pct = delta / base * 100
                if a.page != b.page and delta_pct < 5.0:
                    continue
                occ.is_internally_consistent = False
                occ.internal_inconsistencies.append(
                    InternalInconsistency(item_a=a, item_b=b, delta=delta, delta_pct=delta_pct)
                )
                count += 1
                if count >= 3:
                    break
            if count >= 3:
                break
        final.append(occ)

    # 诊断
    core_keys = {"total_assets", "total_liabilities", "equity", "revenue",
                 "net_profit", "total_profit", "operating_profit",
                 "cash_equivalents", "operating_cash_flow"}
    found_core = [occ.canonical_key for occ in final if occ.canonical_key in core_keys]
    missing_core = core_keys - set(found_core)
    inconsistent_count = sum(1 for occ in final if not occ.is_internally_consistent)
    total_occurrences = sum(len(occ.all_occurrences) for occ in final)
    logger.info(
        f"[{side_label}] 最终指标: {len(final)} 个key, {total_occurrences} 次出现, "
        f"核心命中={len(found_core)}/{len(core_keys)}, 缺失={missing_core}, "
        f"内部不一致={inconsistent_count}"
    )

    # OCR 兜底：核心指标缺失过多或总量过少时触发
    if (
        settings.enable_profile_ocr_fallback
        and (len(found_core) < 5 or len(final) < 15)
        and Path(doc.file_path).exists()
    ):
        logger.warning(f"[{side_label}] 标准提取不足，触发 OCR 兜底")
        try:
            from ahcc.parser.ocr_fallback import extract_metrics_via_ocr
            ocr_items = extract_metrics_via_ocr(
                doc.file_path,
                side=doc.side,
                max_pages=max(int(getattr(settings, "profile_ocr_fallback_max_pages", 40) or 40), 1),
                unit=doc_unit,
                currency=doc_currency,
            )
            ocr_pages = sorted({item.page for item in ocr_items}) or list(range(1, max(doc.total_pages, 0) + 1))
            if ocr_items:
                add_audit_warning(
                    doc,
                    "ocr_used",
                    "OCR fallback was used because standard metric extraction was insufficient.",
                    ocr_pages=ocr_pages,
                )
            else:
                add_audit_warning(doc, "ocr_no_metrics", "OCR fallback ran but produced no metrics.")
            # 合并 OCR 结果到分组中
            existing_keys = {occ.canonical_key for occ in final}
            for ocr_item in ocr_items:
                if ocr_item.canonical_key not in existing_keys:
                    new_occ = MetricOccurrences(
                        canonical_key=ocr_item.canonical_key,
                        name=ocr_item.name,
                        primary=ocr_item,
                        all_occurrences=[ocr_item],
                    )
                    final.append(new_occ)
                    existing_keys.add(ocr_item.canonical_key)
                else:
                    # 补充到已有的 occurrences 中
                    occ = next(o for o in final if o.canonical_key == ocr_item.canonical_key)
                    occ.all_occurrences.append(ocr_item)
                    if ocr_item.value and occ.primary.value and abs(ocr_item.value) > abs(occ.primary.value):
                        occ.primary = ocr_item
            logger.info(f"[{side_label}] OCR 兜底后: {len(final)} 个指标")
        except Exception as e:
            add_audit_warning(doc, "ocr_failed", f"OCR fallback failed: {e}")
            logger.warning(f"[{side_label}] OCR 兜底失败: {e}")

    return final
