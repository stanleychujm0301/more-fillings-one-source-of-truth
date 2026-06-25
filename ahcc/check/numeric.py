"""数值差异检测（P3 实现）— 模块 A 核心。

执行流程：
1. 加载 rules/numeric_equal.yaml 和 cross_check.yaml
2. 对每对 AlignedPair：
   - 单位/币种归一（HKD ↔ CNY 用当期汇率，由 P6 提供）
   - 应用容差判定差异严重度
3. 对每份报告内部跑勾稽规则（如 总资产 = 流动资产 + 非流动资产）
4. 生成 Diff 列表，所有差异附 evidence
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml
from loguru import logger

from ahcc.schemas import (
    AlignedPair,
    DataPoint,
    Diff,
    DiffSeverity,
    DiffType,
    LocalizedString,
    RuleDef,
)
from ahcc.check.explanation import make_value_explanation
from ahcc.profile.models import MetricItem, MetricOccurrences


def run_numeric_checks(pairs: Iterable[AlignedPair]) -> list[Diff]:
    """主入口。"""
    rules = load_rules()
    logger.info(f"加载 {len(rules)} 条数值规则")

    diffs: list[Diff] = []
    for pair in pairs:
        diffs.extend(_check_pair(pair, rules))
    return diffs


def load_rules() -> list[RuleDef]:
    """加载所有 numeric/cross_check YAML 规则。"""
    rules_dir = Path(__file__).resolve().parents[2] / "rules"
    rules: list[RuleDef] = []
    for path in rules_dir.glob("*.yaml"):
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for item in data.get("rules", []):
            rules.append(RuleDef(**item))
    return [r for r in rules if r.enabled]


# 单位归一化乘数（统一折算到"元"）
_UNIT_MULTIPLIERS: dict[str, float] = {
    "元": 1.0,
    "人民币元": 1.0,
    "千元": 1_000.0,
    "人民币千元": 1_000.0,
    "RMB thousand": 1_000.0,
    "万元": 10_000.0,
    "人民币万元": 10_000.0,
    "RMB 万元": 10_000.0,
    "百万元": 1_000_000.0,
    "人民币百万元": 1_000_000.0,
    "RMB million": 1_000_000.0,
    "亿元": 100_000_000.0,
    "人民币亿元": 100_000_000.0,
    "RMB 亿元": 100_000_000.0,
    "HK$ thousand": 1_000.0,
    "HK$ million": 1_000_000.0,
    "US$ thousand": 1_000.0,
    "US$ million": 1_000_000.0,
}


def _get_unit_multiplier(unit: str | None) -> float:
    """根据单位字符串返回归一化乘数。"""
    if not unit:
        return 1.0
    # 精确匹配
    if unit in _UNIT_MULTIPLIERS:
        return _UNIT_MULTIPLIERS[unit]
    # 模糊匹配：包含关键词
    unit_lower = unit.lower()
    if "million" in unit_lower:
        return 1_000_000.0
    if "thousand" in unit_lower or "千元" in unit:
        return 1_000.0
    if "亿元" in unit or "億元" in unit:
        return 100_000_000.0
    if "万元" in unit or "萬元" in unit:
        return 10_000.0
    if "百万元" in unit or "百萬元" in unit:
        return 1_000_000.0
    if "亿" in unit or "億" in unit:
        return 100_000_000.0
    if "万" in unit or "萬" in unit:
        return 10_000.0
    return 1.0


def _check_pair(pair: AlignedPair, rules: list[RuleDef]) -> list[Diff]:
    """对单个数据点对做数值检查。"""
    if not pair.a_point or not pair.h_point:
        return []

    a_val = pair.a_point.value
    h_val = pair.h_point.value
    if a_val is None or h_val is None:
        return []

    # 跳过明显的非金额指标（年份、人数等）
    if _is_non_monetary_value(a_val) and _is_non_monetary_value(h_val):
        return []

    # 单位归一化（统一折算到元）
    a_mult = _get_unit_multiplier(pair.a_point.unit)
    h_mult = _get_unit_multiplier(pair.h_point.unit)
    a_norm = a_val * a_mult
    h_norm = h_val * h_mult

    # 单位不同时，数值差异可能仅由单位换算/汇率产生，非真正不一致
    unit_mismatch = a_mult != h_mult

    delta = abs(a_norm - h_norm)
    # YAML 中的 tolerance 已是「同币种、同单位归一后（折算到元）的绝对差异」，
    # a_norm/h_norm 也已归一化到元，故 delta 与 tolerance 同口径，直接比较，不再乘单位乘数。
    tolerance_norm = _find_tolerance(pair.canonical_key, rules)

    if delta <= tolerance_norm:
        return []

    base = max(abs(a_norm), abs(h_norm), 1e-9)
    ratio = delta / base

    # 智能检测：如果归一化后比例差接近 1000 或 10000 倍，说明单位标注有误
    # 尝试用另一种单位归一化再比较
    if ratio > 0.5:
        adjusted = _try_unit_fix(a_val, h_val, a_mult, h_mult)
        if adjusted is not None:
            a_adj, h_adj = adjusted
            delta_adj = abs(a_adj - h_adj)
            base_adj = max(abs(a_adj), abs(h_adj), 1e-9)
            ratio_adj = delta_adj / base_adj
            if ratio_adj <= 0.02:
                return [_make_unit_fix_diff(pair, a_adj, h_adj, delta_adj, ratio_adj)]

    # 单位不同时，比例差异 <= 2% 视为换算误差，降级为 info
    if unit_mismatch and ratio <= 0.02:
        return [_make_unit_conversion_diff(pair, a_norm, h_norm, delta)]

    # 单位相同时，比例差异 <= 1% 视为汇率/四舍五入误差，降级为 info
    if not unit_mismatch and ratio <= 0.01:
        return [_make_rounding_diff(pair, a_norm, h_norm, delta)]

    severity = _grade_severity(delta, a_norm, h_norm)
    return [
        Diff(
            diff_id=str(uuid.uuid4())[:8],
            diff_type=DiffType.NUMERIC,
            severity=severity,
            canonical_key=pair.canonical_key,
            topic=LocalizedString(zh=pair.topic_zh, en=pair.topic_en),
            summary=LocalizedString(
                zh=f"{pair.topic_zh}: A 股 {a_norm:,.2f} vs H 股 {h_norm:,.2f}, 差异 {delta:,.2f}",
                en=f"{pair.topic_en}: A {a_norm:,.2f} vs H {h_norm:,.2f}, delta {delta:,.2f}",
            ),
            a_value=a_norm,
            h_value=h_norm,
            delta=delta,
            tolerance=tolerance_norm,
            evidence=[pair.a_point.evidence, pair.h_point.evidence],
            diff_explanation=make_value_explanation(
                headline=f"{pair.topic_zh or pair.canonical_key}数值不一致",
                label=pair.topic_zh or pair.canonical_key,
                role=pair.canonical_key,
                a_value=a_norm,
                h_value=h_norm,
                delta=delta,
                evidence=[pair.a_point.evidence, pair.h_point.evidence],
                review_hint="优先核对该指标是否为同一期间、同一单位和同一披露口径。",
            ),
        )
    ]


def _try_unit_fix(a_val: float, h_val: float, a_mult: float, h_mult: float) -> tuple[float, float] | None:
    """当归一化后差异巨大时，尝试不同单位组合找到匹配。

    常见场景：A股标注为"元"但实际值是"千元"（或反之）。
    尝试所有合理的单位乘数组合，找到比例差 < 2% 的组合。
    """
    candidate_multipliers = [1.0, 1_000.0, 10_000.0, 1_000_000.0, 100_000_000.0]
    best_ratio = 1.0
    best_result = None

    for am in candidate_multipliers:
        for hm in candidate_multipliers:
            a_try = a_val * am
            h_try = h_val * hm
            base = max(abs(a_try), abs(h_try), 1e-9)
            delta = abs(a_try - h_try)
            r = delta / base
            if r < best_ratio:
                best_ratio = r
                best_result = (a_try, h_try)

    if best_ratio <= 0.02 and best_result is not None:
        return best_result
    return None


# 核心财务指标：单边缺失应提升严重度
# 注意：所有者权益合计在不同模块有两种规范键——glossary 用 "equity"，
# pdf 解析器与 YAML 规则（BS-003/cross_check）用 "total_equity"，两者都需保留，
# 否则该候选会被 _filter_profile_candidates 滤掉、对应规则永不命中。
# 与 ahcc/profile/compare.py 的 _CORE_KEYS 保持一致。
_CORE_KEYS = {
    "total_assets", "total_liabilities", "equity", "total_equity",
    "revenue", "net_profit", "total_profit", "operating_profit",
    "cash_equivalents", "operating_cash_flow",
    "eps_basic", "eps_diluted", "basic_eps", "diluted_eps",
}


def _make_unit_fix_diff(pair: AlignedPair, a_adj: float, h_adj: float, delta: float, ratio: float) -> Diff:
    """单位标注有误但修正后匹配，标记为 INFO。"""
    return Diff(
        diff_id=str(uuid.uuid4())[:8],
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.INFO,
        triage="expected",
        canonical_key=pair.canonical_key,
        topic=LocalizedString(zh=pair.topic_zh, en=pair.topic_en),
        summary=LocalizedString(
            zh=f"{pair.topic_zh}: A 股 {a_adj:,.2f} vs H 股 {h_adj:,.2f}（单位修正后匹配，差异 {delta:,.2f}，比例 {ratio*100:.2f}%）",
            en=f"{pair.topic_en}: A {a_adj:,.2f} vs H {h_adj:,.2f} (matched after unit fix, diff {delta:,.2f})",
        ),
        a_value=a_adj,
        h_value=h_adj,
        delta=delta,
        tolerance=None,
        evidence=[pair.a_point.evidence, pair.h_point.evidence],
    )


def _make_unit_conversion_diff(pair: AlignedPair, a_norm: float, h_norm: float, delta: float) -> Diff:
    """单位不同导致的换算差异，降级为 info。"""
    return Diff(
        diff_id=str(uuid.uuid4())[:8],
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.INFO,
        triage="expected",
        canonical_key=pair.canonical_key,
        topic=LocalizedString(zh=pair.topic_zh, en=pair.topic_en),
        summary=LocalizedString(
            zh=f"{pair.topic_zh}: A 股 {a_norm:,.2f} vs H 股 {h_norm:,.2f}（单位换算差异 {delta:,.2f}，比例 {delta / max(abs(a_norm), abs(h_norm), 1e-9) * 100:.2f}%）",
            en=f"{pair.topic_en}: A {a_norm:,.2f} vs H {h_norm:,.2f} (unit conversion diff {delta:,.2f})",
        ),
        a_value=a_norm,
        h_value=h_norm,
        delta=delta,
        tolerance=None,
        evidence=[pair.a_point.evidence, pair.h_point.evidence],
    )


def _make_rounding_diff(pair: AlignedPair, a_norm: float, h_norm: float, delta: float) -> Diff:
    """单位相同但差异极小（≤1%），可能是汇率/四舍五入误差，降级为 info。"""
    return Diff(
        diff_id=str(uuid.uuid4())[:8],
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.INFO,
        triage="expected",
        canonical_key=pair.canonical_key,
        topic=LocalizedString(zh=pair.topic_zh, en=pair.topic_en),
        summary=LocalizedString(
            zh=f"{pair.topic_zh}: A 股 {a_norm:,.2f} vs H 股 {h_norm:,.2f}（微小差异 {delta:,.2f}，比例 {delta / max(abs(a_norm), abs(h_norm), 1e-9) * 100:.2f}%，可能为汇率/四舍五入）",
            en=f"{pair.topic_en}: A {a_norm:,.2f} vs H {h_norm:,.2f} (minor diff {delta:,.2f}, likely rounding)",
        ),
        a_value=a_norm,
        h_value=h_norm,
        delta=delta,
        tolerance=None,
        evidence=[pair.a_point.evidence, pair.h_point.evidence],
    )


def _find_tolerance(canonical_key: str, rules: list[RuleDef]) -> float:
    for r in rules:
        if canonical_key in r.targets:
            return r.tolerance
    return 0.0


def _grade_severity(delta: float, a: float, h: float) -> DiffSeverity:
    """简单分级：相对差异 < 1% LOW；< 5% MEDIUM；< 20% HIGH；≥ 20% CRITICAL。"""
    base = max(abs(a), abs(h), 1e-9)
    ratio = delta / base
    if ratio < 0.01:
        return DiffSeverity.LOW
    if ratio < 0.05:
        return DiffSeverity.MEDIUM
    if ratio < 0.20:
        return DiffSeverity.HIGH
    return DiffSeverity.CRITICAL


# ============================================================
# Profile 适配器
# ============================================================

def _is_garbled_key(key: str) -> bool:
    """判断 canonical_key 是否为乱码（不可读字符占比过高）。"""
    if not key or len(key) < 2:
        return True
    # 统计可读字符（中文、英文、数字、下划线）
    readable = sum(1 for c in key if re.match(r"[\w一-鿿]", c))
    if len(key) > 10 and readable / len(key) < 0.5:
        return True
    return False


def _is_non_monetary_key(key: str) -> bool:
    """判断 canonical_key 是否为非金额指标（人数、年份、比例等）。"""
    non_monetary_patterns = [
        "人数", "人数合计", "职工人数", "研究人员", "投资人员", "人员",
        "占比", "比例", "比率", "比率(", "率(%)",
        "注册地址", "办公地址", "成立日期", "首次公开发",
        "持有", "持股", "持股比例",
        "家", "户", "间", "所",
    ]
    for p in non_monetary_patterns:
        if p in key:
            return True
    return False


def _is_non_monetary_value(val: float | None) -> bool:
    """判断数值是否明显不是金额（年份、小计数等）。"""
    if val is None:
        return False
    # 年份范围
    if 1990 <= val <= 2035:
        return True
    # 极小值（人数、计数）
    if 0 < abs(val) < 50:
        return True
    return False


_CONTEXT_BLOCKERS = {"segment_report", "financial_instruments", "parent_company", "risk_exposure", "share_changes"}
_MAIN_CONTEXTS = {"main_statement", "financial_highlights"}
_COMPATIBLE_CONTEXT_PAIRS = {
    ("main_statement", "financial_highlights"),
    ("financial_highlights", "main_statement"),
}
_SHARE_CONTEXT_ALLOWED_KEYS = {
    "share_capital",
    "treasury_stock",
    "preferred_stock",
    "perpetual_bond",
    "dividend_per_share",
    "cash_dividend_paid",
}
_STATEMENT_VALUE_KEYS = _CORE_KEYS | {
    "current_assets", "non_current_assets", "current_liabilities", "non_current_liabilities",
    "trading_financial_assets", "trading_financial_liabilities", "settlement_reserves",
    "agency_trading_payable", "margin_financing", "reverse_repo", "sell_repo",
    "other_debt_investments", "other_equity_investments", "debt_investments",
    "interest_receivable", "interest_payable", "other_receivables", "other_payables",
    "customer_loans", "customer_deposits", "gross_profit", "interest_income",
    "interest_expense", "interest_net", "commission_income", "commission_net",
    "cash_equivalents_end", "net_profit_attributable", "parent_equity",
}
_CURRENCY_INFERENCE_KEYS = _CORE_KEYS | {
    "gross_profit",
    "interest_income",
    "interest_expense",
    "interest_net",
    "total_liabilities",
    "net_profit_attributable",
    "parent_equity",
}


@dataclass
class _PairScore:
    a_item: MetricItem
    h_item: MetricItem
    a_norm: float
    h_norm: float
    direct_ratio: float
    converted_ratio: float | None
    score: float
    context_compatible: bool
    context_note: str
    label_score: float
    confidence_score: float
    period_score: float
    source_score: float


@lru_cache(maxsize=100_000)
def _normalize_text(*parts: str | None) -> str:
    from ahcc.align.glossary import to_simplified

    text = " ".join(part or "" for part in parts)
    text = to_simplified(text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _metric_search_text(item: MetricItem) -> str:
    return _normalize_text(
        item.name.zh,
        item.name.en,
        item.evidence.section if item.evidence else None,
        item.evidence.snippet if item.evidence else None,
        item.value_text,
    )


@lru_cache(maxsize=512)
def _canonical_terms(key: str) -> tuple[str, ...]:
    from ahcc.align.glossary import glossary, to_simplified

    entry = glossary.get_entry(key)
    if not entry:
        return (key.lower(),)
    raw_terms = [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]
    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        normalized = to_simplified(term).strip().lower()
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    terms.append(key.lower())
    return tuple(terms)


def _context_bucket(item: MetricItem) -> str:
    section = _normalize_text(item.evidence.section if item.evidence else None)
    text = _metric_search_text(item)
    return _context_bucket_cached(section, text)


@lru_cache(maxsize=100_000)
def _context_bucket_cached(section: str, text: str) -> str:
    if section in {"bs", "pl", "cf", "income", "cash_flow", "equity", "financial_statements"}:
        return "main_statement"

    if any(p in text for p in ("母公司", "本公司财务状况表", "公司财务状况表", "母公司资产负债表", "company financial position", "company statement of financial position")):
        return "parent_company"

    if any(p in text for p in ("金融工具", "公允价值", "公允價值", "fair value", "以公允价值计量", "以摊余成本计量", "以攤餘成本計量")):
        return "financial_instruments"

    if any(p in text for p in ("信用风险敞口", "最大信用风险", "风险敞口", "risk exposure", "credit risk exposure")):
        return "risk_exposure"

    if section == "share_changes" or any(p in text for p in ("股东", "股東", "持股", "前十名", "股份变动", "股份變動", "证券投资基金", "證券投資基金", "限售股")):
        return "share_changes"

    if any(p in text for p in ("分部", "业务分部", "经营分部", "營運分部", "segment", "银行业务关键指标", "保險業務", "保险业务", "分部分析")):
        return "segment_report"

    if any(p in text for p in ("主要会计数据", "主要财务指标", "财务摘要", "financial highlights", "key financial data")):
        return "financial_highlights"

    if any(p in text for p in ("合并资产负债表", "合并利润表", "合并现金流量表", "资产负债表", "利润表", "现金流量表", "consolidated statement of financial position", "consolidated income statement", "consolidated statement of cash flows")):
        return "main_statement"

    return "unknown"


def _candidate_bad_reason(item: MetricItem) -> str | None:
    if item.value is None:
        return "missing_value"
    if item.confidence < 0.65:
        return "low_extraction_confidence"
    if _is_garbled_key(item.canonical_key) or _is_non_monetary_key(item.canonical_key):
        return "invalid_key"

    bucket = _context_bucket(item)
    text = _metric_search_text(item)
    if bucket == "share_changes" and item.canonical_key in _STATEMENT_VALUE_KEYS - _SHARE_CONTEXT_ALLOWED_KEYS:
        return "shareholder_or_share_change_context"
    if item.canonical_key in _STATEMENT_VALUE_KEYS and any(
        p in text for p in ("基金名称", "基金名", "股东名称", "股東名稱", "持股数量", "持股比例", "普通股股东总数")
    ):
        return "shareholder_table_candidate"
    if item.canonical_key in _STATEMENT_VALUE_KEYS and _is_non_monetary_value(item.value) and item.source == "generic_pattern":
        return "non_monetary_pattern_value"
    return None


def _contexts_compatible(key: str, a_item: MetricItem, h_item: MetricItem) -> tuple[bool, str]:
    a_bucket = _context_bucket(a_item)
    h_bucket = _context_bucket(h_item)

    if a_bucket == h_bucket:
        if a_bucket == "unknown":
            return False, "context_uncertain:unknown/unknown"
        if a_bucket == "share_changes" and key not in _SHARE_CONTEXT_ALLOWED_KEYS:
            return False, "股东/股份变动上下文不适合判定财务指标真实差异"
        return True, f"same_context:{a_bucket}"

    if (a_bucket, h_bucket) in _COMPATIBLE_CONTEXT_PAIRS:
        return True, f"compatible_context:{a_bucket}/{h_bucket}"

    if a_bucket == "unknown" or h_bucket == "unknown":
        return False, f"context_uncertain:{a_bucket}/{h_bucket}"

    return False, f"context_mismatch:{a_bucket}/{h_bucket}"


def _label_similarity(key: str, a_item: MetricItem, h_item: MetricItem) -> float:
    a_text = _metric_search_text(a_item)
    h_text = _metric_search_text(h_item)
    a_name = _normalize_text(a_item.name.zh, a_item.name.en)
    h_name = _normalize_text(h_item.name.zh, h_item.name.en)
    seq = SequenceMatcher(None, a_name or key, h_name or key).ratio()

    terms = _canonical_terms(key)
    a_hit = any(term in a_text for term in terms)
    h_hit = any(term in h_text for term in terms)
    if a_hit and h_hit:
        return max(seq, 0.92)
    if a_hit or h_hit:
        return max(seq, 0.72)
    return max(seq, 0.62)


def _normalized_metric_value(item: MetricItem) -> float | None:
    if item.value is None:
        return None
    return item.value * _get_unit_multiplier(item.unit)


def _relative_delta(a_norm: float, h_norm: float) -> float:
    return abs(a_norm - h_norm) / max(abs(a_norm), abs(h_norm), 1e-9)


def _period_score(a_item: MetricItem, h_item: MetricItem) -> float:
    if not a_item.period or not h_item.period:
        return 0.8
    return 1.0 if a_item.period == h_item.period else 0.0


def _source_score(a_item: MetricItem, h_item: MetricItem) -> float:
    score = 0.0
    score += 0.5 if a_item.source == "table" else 0.25
    score += 0.5 if h_item.source == "table" else 0.25
    return score


def _score_candidate_pair(key: str, a_item: MetricItem, h_item: MetricItem, currency_factor: float | None = None) -> _PairScore | None:
    a_norm = _normalized_metric_value(a_item)
    h_norm = _normalized_metric_value(h_item)
    if a_norm is None or h_norm is None:
        return None

    direct_ratio = _relative_delta(a_norm, h_norm)
    converted_ratio = _relative_delta(a_norm, h_norm * currency_factor) if currency_factor else None
    best_value_ratio = min(direct_ratio, converted_ratio if converted_ratio is not None else direct_ratio)
    value_score = max(0.0, 1.0 - min(best_value_ratio, 1.0))
    context_compatible, context_note = _contexts_compatible(key, a_item, h_item)
    context_score = 1.0 if context_compatible else 0.0
    label_score = _label_similarity(key, a_item, h_item)
    confidence_score = (a_item.confidence + h_item.confidence) / 2
    period = _period_score(a_item, h_item)
    source = _source_score(a_item, h_item)
    score = (
        0.28 * context_score
        + 0.24 * label_score
        + 0.20 * confidence_score
        + 0.12 * period
        + 0.08 * source
        + 0.08 * value_score
    )
    return _PairScore(
        a_item=a_item,
        h_item=h_item,
        a_norm=a_norm,
        h_norm=h_norm,
        direct_ratio=direct_ratio,
        converted_ratio=converted_ratio,
        score=score,
        context_compatible=context_compatible,
        context_note=context_note,
        label_score=label_score,
        confidence_score=confidence_score,
        period_score=period,
        source_score=source,
    )


def _is_high_conf_same_scope(pair_score: _PairScore) -> bool:
    return (
        pair_score.context_compatible
        and pair_score.confidence_score >= 0.88
        and pair_score.label_score >= 0.62
        and pair_score.period_score >= 0.5
        and pair_score.source_score >= 0.5
        and pair_score.score >= 0.72
    )


def _pair_for_score(key: str, pair_score: _PairScore, alignment_confidence: float | None = None) -> AlignedPair:
    a_item = pair_score.a_item
    h_item = pair_score.h_item
    return AlignedPair(
        canonical_key=key,
        topic_zh=a_item.name.zh or h_item.name.zh or key,
        topic_en=a_item.name.en or h_item.name.en or key,
        a_point=DataPoint(
            name=a_item.name,
            canonical_key=a_item.canonical_key,
            value=a_item.value,
            value_text=a_item.value_text,
            unit=a_item.unit,
            currency=a_item.currency,
            period=a_item.period,
            evidence=a_item.evidence,
            confidence=a_item.confidence,
        ),
        h_point=DataPoint(
            name=h_item.name,
            canonical_key=h_item.canonical_key,
            value=h_item.value,
            value_text=h_item.value_text,
            unit=h_item.unit,
            currency=h_item.currency,
            period=h_item.period,
            evidence=h_item.evidence,
            confidence=h_item.confidence,
        ),
        alignment_confidence=alignment_confidence if alignment_confidence is not None else pair_score.score,
    )


def _pair_match_kind(key: str, pair_score: _PairScore, rules: list[RuleDef], currency_factor: float | None) -> str | None:
    tolerance = _find_tolerance(key, rules)
    tolerance_norm = tolerance * _get_unit_multiplier(pair_score.a_item.unit)
    delta = abs(pair_score.a_norm - pair_score.h_norm)
    if delta <= tolerance_norm:
        return "direct"

    if pair_score.direct_ratio <= 0.005:
        return "direct"

    if _get_unit_multiplier(pair_score.a_item.unit) != _get_unit_multiplier(pair_score.h_item.unit) and pair_score.direct_ratio <= 0.02:
        return "unit_conversion"

    adjusted = _try_unit_fix(
        pair_score.a_item.value or 0.0,
        pair_score.h_item.value or 0.0,
        _get_unit_multiplier(pair_score.a_item.unit),
        _get_unit_multiplier(pair_score.h_item.unit),
    )
    if adjusted is not None:
        a_adj, h_adj = adjusted
        if _relative_delta(a_adj, h_adj) <= 0.02:
            return "unit_fix"

    if pair_score.direct_ratio <= 0.01:
        return "rounding"

    if currency_factor and pair_score.converted_ratio is not None and pair_score.converted_ratio <= 0.05:
        return "currency_conversion"

    return None


def _make_currency_conversion_diff(key: str, pair_score: _PairScore, factor: float) -> Diff:
    h_converted = pair_score.h_norm * factor
    delta = abs(pair_score.a_norm - h_converted)
    ratio = delta / max(abs(pair_score.a_norm), abs(h_converted), 1e-9)
    return Diff(
        diff_id=str(uuid.uuid4())[:8],
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.INFO,
        triage="expected",
        canonical_key=key,
        topic=LocalizedString(
            zh=pair_score.a_item.name.zh or pair_score.h_item.name.zh or key,
            en=pair_score.a_item.name.en or pair_score.h_item.name.en or key,
        ),
        summary=LocalizedString(
            zh=f"{pair_score.a_item.name.zh or key}: 按推断币种/列示货币因子 {factor:.4f} 折算后匹配，A股 {pair_score.a_norm:,.2f} vs H股折算后 {h_converted:,.2f}，差异比例 {ratio*100:.2f}%",
            en=f"{pair_score.a_item.name.en or key}: matched after inferred currency/presentation conversion factor {factor:.4f}, ratio diff {ratio*100:.2f}%",
        ),
        a_value=pair_score.a_norm,
        h_value=h_converted,
        delta=delta,
        tolerance=None,
        evidence=[pair_score.a_item.evidence, pair_score.h_item.evidence],
        diff_explanation=make_value_explanation(
            headline=f"{pair_score.a_item.name.zh or key}列示口径换算后匹配",
            label=pair_score.a_item.name.zh or key,
            role=key,
            a_value=pair_score.a_norm,
            h_value=h_converted,
            delta=delta,
            evidence=[pair_score.a_item.evidence, pair_score.h_item.evidence],
            review_hint="该项已按推断币种或列示单位换算，通常属于预期差异。",
        ),
        rule_id="currency_converted_match",
    )


def _make_unresolved_candidate_diff(key: str, pair_score: _PairScore, reason: str) -> Diff:
    return Diff(
        diff_id=str(uuid.uuid4())[:8],
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.INFO,
        triage="unresolved",
        canonical_key=key,
        topic=LocalizedString(
            zh=pair_score.a_item.name.zh or pair_score.h_item.name.zh or key,
            en=pair_score.a_item.name.en or pair_score.h_item.name.en or key,
        ),
        summary=LocalizedString(
            zh=f"{pair_score.a_item.name.zh or key}: A/H 候选证据未达到同口径高置信标准，暂不判定为真实差异（{reason}；{pair_score.context_note}）",
            en=f"{pair_score.a_item.name.en or key}: candidate evidence is not high-confidence same-scope, not classified as a real difference ({reason}; {pair_score.context_note})",
        ),
        a_value=pair_score.a_norm,
        h_value=pair_score.h_norm,
        delta=abs(pair_score.a_norm - pair_score.h_norm),
        tolerance=None,
        evidence=[pair_score.a_item.evidence, pair_score.h_item.evidence],
        diff_explanation=make_value_explanation(
            headline=f"{pair_score.a_item.name.zh or key}候选证据需复核",
            label=pair_score.a_item.name.zh or key,
            role=key,
            a_value=pair_score.a_norm,
            h_value=pair_score.h_norm,
            delta=abs(pair_score.a_norm - pair_score.h_norm),
            evidence=[pair_score.a_item.evidence, pair_score.h_item.evidence],
            review_hint=f"暂不判定为真实差异：{reason}；{pair_score.context_note}",
        ),
        rule_id="context_mismatch" if not pair_score.context_compatible else "low_confidence_candidate",
    )


def _flatten_metric_occurrences(metrics: Iterable[MetricOccurrences | MetricItem]) -> list[MetricItem]:
    items: list[MetricItem] = []
    seen: set[tuple[str, int, str, str]] = set()
    for occ in metrics:
        if isinstance(occ, MetricOccurrences):
            candidates = occ.all_occurrences or [occ.primary]
        elif isinstance(occ, MetricItem):
            candidates = [occ]
        else:
            continue
        for item in candidates:
            marker = (
                item.canonical_key,
                item.page,
                str(item.value_text or item.value),
                (item.evidence.snippet if item.evidence else "")[:80],
            )
            if marker in seen:
                continue
            seen.add(marker)
            items.append(item)
    return items


def _filter_profile_candidates(metrics: Iterable[MetricOccurrences | MetricItem], glossary_keys: set[str]) -> list[MetricItem]:
    candidates: list[MetricItem] = []
    for item in _flatten_metric_occurrences(metrics):
        if item.canonical_key not in glossary_keys and item.canonical_key not in _CORE_KEYS:
            continue
        if _candidate_bad_reason(item):
            continue
        candidates.append(item)
    return candidates


def _group_by_key(items: Iterable[MetricItem]) -> dict[str, list[MetricItem]]:
    grouped: dict[str, list[MetricItem]] = {}
    for item in items:
        grouped.setdefault(item.canonical_key, []).append(item)
    return grouped


def _infer_currency_factor(a_by_key: dict[str, list[MetricItem]], h_by_key: dict[str, list[MetricItem]]) -> float | None:
    ratios: list[float] = []
    for key in sorted((set(a_by_key) & set(h_by_key)) & _CURRENCY_INFERENCE_KEYS):
        pair_scores: list[_PairScore] = []
        for a_item in a_by_key[key]:
            for h_item in h_by_key[key]:
                score = _score_candidate_pair(key, a_item, h_item, None)
                if not score or score.confidence_score < 0.85 or score.label_score < 0.62:
                    continue
                a_bucket = _context_bucket(a_item)
                h_bucket = _context_bucket(h_item)
                if a_bucket in _CONTEXT_BLOCKERS or h_bucket in _CONTEXT_BLOCKERS:
                    continue
                if score:
                    pair_scores.append(score)
        if not pair_scores:
            continue
        best = max(pair_scores, key=lambda p: p.score)
        if abs(best.a_norm) < 1_000 or abs(best.h_norm) < 1_000:
            continue
        ratio = abs(best.a_norm / best.h_norm) if best.h_norm else 0.0
        # 已知限制：以下区间仅覆盖「A/H 单位归一后仍相差数倍」的历史汇率场景（约 4~10 倍）。
        # 主办方样本（如光大银行）A、H 两份均以人民币列报（ratio≈1），本推断在这些样本上不触发，
        # 属预期行为——不要为了让它触发而加入 ~1.0 的区间，否则会对同币种同值的真实差异
        # 误判为「汇率换算匹配」而漏检。若后续引入真以 HKD 列报的样本，需用真实多币种样本重新标定区间。
        if 4.0 <= ratio <= 10.0:
            ratios.append(ratio)
        elif 0.10 <= ratio <= 0.25:
            ratios.append(1 / ratio)

    if len(ratios) < 2:
        return None
    ratios.sort()
    median = ratios[len(ratios) // 2]
    close = [r for r in ratios if abs(r - median) / median <= 0.10]
    if len(close) >= 3 or (len(close) >= 2 and max(close) / min(close) <= 1.04):
        return sum(close) / len(close)
    return None


def run_numeric_checks_on_profiles(profile_a, profile_h) -> list[Diff]:
    """基于画像的数值差异检测。

    核心策略：
    1. 使用 MetricOccurrences.all_occurrences，而不是只取 primary。
    2. 对同一 canonical_key 的 A/H 候选做口径、置信度、标签、期间和数值打分。
    3. 只有高置信同口径候选仍冲突时才进入 real；口径不兼容或低置信进入 unresolved。
    """
    from ahcc.align.glossary import glossary

    rules = load_rules()
    glossary_keys = set(glossary.all_canonical_keys())
    a_candidates = _filter_profile_candidates(profile_a.metrics, glossary_keys)
    h_candidates = _filter_profile_candidates(profile_h.metrics, glossary_keys)
    a_by_key = _group_by_key(a_candidates)
    h_by_key = _group_by_key(h_candidates)
    common_keys = sorted(set(a_by_key) & set(h_by_key))
    currency_factor = _infer_currency_factor(a_by_key, h_by_key)

    diffs: list[Diff] = []
    matched_equal = 0
    expected_matches = 0
    unresolved_candidates = 0
    real_candidates = 0

    for key in common_keys:
        pair_scores: list[_PairScore] = []
        for a_item in a_by_key[key]:
            for h_item in h_by_key[key]:
                score = _score_candidate_pair(key, a_item, h_item, currency_factor)
                if score:
                    pair_scores.append(score)
        if not pair_scores:
            continue

        # 先找任何可解释匹配。只要全集候选中有同值/单位/币种换算后的匹配，
        # 该 key 就不应再因为 primary 错选而报真实差异。
        matched_scores: list[tuple[_PairScore, str]] = []
        for score in pair_scores:
            kind = _pair_match_kind(key, score, rules, currency_factor)
            if kind:
                matched_scores.append((score, kind))

        if matched_scores:
            best_score, kind = max(
                matched_scores,
                key=lambda item: (
                    1 if item[1] == "currency_conversion" else 0,
                    item[0].context_compatible,
                    item[0].score,
                    -item[0].direct_ratio,
                ),
            )
            if kind == "currency_conversion" and currency_factor:
                diffs.append(_make_currency_conversion_diff(key, best_score, currency_factor))
                expected_matches += 1
            elif kind in {"unit_conversion", "unit_fix", "rounding"}:
                pair = _pair_for_score(key, best_score)
                expected = [d for d in _check_pair(pair, rules) if d.triage == "expected"]
                if expected:
                    diffs.extend(expected)
                    expected_matches += len(expected)
                else:
                    matched_equal += 1
            else:
                matched_equal += 1
            continue

        best_score = max(pair_scores, key=lambda item: item.score)
        if _is_high_conf_same_scope(best_score):
            pair = _pair_for_score(key, best_score)
            pair_diffs = _check_pair(pair, rules)
            diffs.extend(pair_diffs)
            real_candidates += sum(1 for d in pair_diffs if d.triage == "real")
            expected_matches += sum(1 for d in pair_diffs if d.triage == "expected")
            continue

        reason = "口径不兼容" if not best_score.context_compatible else "候选置信度不足"
        diffs.append(_make_unresolved_candidate_diff(key, best_score, reason))
        unresolved_candidates += 1

    logger.info(
        "画像数值候选甄别: keys={} A_candidates={} H_candidates={} matched={} expected={} unresolved={} real={} currency_factor={}",
        len(common_keys),
        len(a_candidates),
        len(h_candidates),
        matched_equal,
        expected_matches,
        unresolved_candidates,
        real_candidates,
        f"{currency_factor:.4f}" if currency_factor else "none",
    )

    return diffs
