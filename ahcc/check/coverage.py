"""Disclosure coverage and cross-page event checks.

Coverage items are not inconsistencies. They keep A-only/H-only disclosures and
matched cross-page events visible without polluting the real-difference list.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from ahcc.align.glossary import to_simplified
from ahcc.check.explanation import (
    explanation_item,
    format_explanation_value,
    format_location,
)
from ahcc.profile.compare import compare_metrics, compare_narratives, compare_structures
from ahcc.profile.topic_map import get_topic_for_text, get_topic_name
from ahcc.schemas import (
    Diff,
    DiffExplanation,
    DiffSeverity,
    DiffType,
    DisclosureCoverageItem,
    Evidence,
    LocalizedString,
    ReportSide,
)


EVENT_KEYWORDS = (
    "重大事项",
    "重要事项",
    "诉讼",
    "仲裁",
    "案件",
    "担保",
    "承诺",
    "关联交易",
    "处罚",
    "罚款",
    "收购",
    "出售",
    "转让",
    "发行",
    "分红",
    "股息",
    "变更",
    "签订",
    "协议",
    "合作",
    "批准",
    "完成",
    "终止",
    "litigation",
    "arbitration",
    "lawsuit",
    "guarantee",
    "commitment",
    "related party",
    "penalty",
    "acquisition",
    "disposal",
    "dividend",
    "dividends",
    "cash dividend",
    "profit distribution",
    "profit appropriation",
    "利润分配",
    "利潤分配",
    "现金股利",
    "現金股利",
    "现金分红",
    "現金分紅",
    "股利",
    "bond",
    "bonds",
    "issuance",
    "repayment",
    "agreement",
    "approved",
    "completed",
    "terminated",
)

STATUS_KEYWORDS = (
    "已完成",
    "完成",
    "已批准",
    "批准",
    "生效",
    "终止",
    "尚未",
    "进行中",
    "未决",
    "已发行",
    "completed",
    "approved",
    "effective",
    "terminated",
    "pending",
    "ongoing",
    "issued",
    "proposed",
    "subject to approval",
    "待批准",
    "尚待",
    "提议",
    "提議",
)

_EVENT_MATCH_THRESHOLD = 0.42
_EVENT_DIFF_THRESHOLD = 0.65
_EVENT_DIFF_IDENTITY_ANCHOR_MIN = 1
_LABEL_DATE = "日期"
# 通用财报截止日/报告期日期不应作为事件身份锚点，否则会把同一年报期内不同事项错误配对
_GENERIC_REPORT_DATES = {
    "03-31",
    "06-30",
    "09-30",
    "12-31",
    "03",
    "06",
    "09",
    "12",
}
_LABEL_AMOUNT = "金额/数量"
_LABEL_PERCENTAGE = "比例"
_LABEL_ENTITY = "主体"
_LABEL_STATUS = "状态"
_STRONG_FACT_LABELS = {_LABEL_DATE, _LABEL_AMOUNT, _LABEL_PERCENTAGE, _LABEL_STATUS}
_EVENT_DIFF_EXCLUDED_SECTIONS = {"bs", "pl", "cf", "equity", "financial_statements"}
_GENERIC_ENTITIES = {"本公司", "本集团", "本行", "公司", "集团", "国泰海通证券股份有限公司"}
_TEMPLATE_MARKERS = ("□适用", "√不适用", "不适用")
_ACTION_KEYWORDS = (
    "签订",
    "协议",
    "合作",
    "批准",
    "完成",
    "终止",
    "生效",
    "收购",
    "出售",
    "转让",
    "发行",
    "分红",
    "股息",
    "担保",
    "承诺",
    "处罚",
    "罚款",
    "诉讼",
    "仲裁",
    "案件",
    "关联交易",
    "acquisition",
    "agreement",
    "approved",
    "completed",
    "terminated",
    "litigation",
    "arbitration",
    "guarantee",
    "commitment",
    "dividend",
    "cash dividend",
    "profit distribution",
    "bond",
    "issuance",
    "repayment",
)
_GOVERNANCE_SOFT_TERMS = (
    "董事",
    "监事",
    "高级管理人员",
    "薪酬",
    "报酬",
    "津贴",
    "福利",
    "住房公积金",
    "年金",
    "履职",
    "考核",
    "评价",
    "股东大会",
    "董事会",
    "监事会",
    "议案",
    "会议",
)
_AMOUNT_CONTEXT_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(人民币|港币|美元|港元|rmb|usd|hkd)?\s*"
    r"(亿元|亿美元|港元|百万元|万元|万股|元|亿|万|rmb million|rmb thousand|million|billion)"
)
_AMOUNT_ROLE_TERMS = {
    "proceeds": ("募集资金", "募资", "募集", "融资额", "融资"),
    "underwriting_scale": ("承销规模", "承销金额", "发行规模", "发行金额"),
    "share_count": ("发行股", "总股数", "股本", "股份", "万股", "股"),
    "transaction_amount": ("交易对价", "转让", "收购", "出售", "价款", "金额"),
    "guarantee": ("担保",),
    "penalty": ("处罚", "罚款"),
    "claim_amount": ("诉讼", "仲裁", "赔偿", "案件"),
}
_AMOUNT_CONTEXT_TERMS = tuple(term for terms in _AMOUNT_ROLE_TERMS.values() for term in terms)
_STRUCTURED_AMOUNT_NUMBER = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
_STRUCTURED_AMOUNT_RE = re.compile(
    rf"(?:(?P<currency>rmb|usd|hkd|人民币|人民幣|港币|港幣|美元)\s*)?"
    rf"(?P<value>{_STRUCTURED_AMOUNT_NUMBER})\s*"
    r"(?P<unit>rmb\s+thousand|rmb\s+million|thousand\s+shares?|"
    r"thousand|million|billion|千元|千股|万元|萬元|亿元|億元|万股|萬股|元|股|shares?)?",
    re.IGNORECASE,
)
_DIVIDEND_DOMAIN_TERMS = (
    "profit distribution",
    "profit appropriation",
    "cash dividend",
    "dividends",
    "dividend",
    "利润分配",
    "利潤分配",
    "现金股利",
    "現金股利",
    "现金分红",
    "現金分紅",
    "股利",
    "股息",
)
_DIVIDEND_TOTAL_TERMS = (
    "total dividends",
    "total amount of cash dividends",
    "amounting to",
    "共计股利",
    "共計股利",
    "共计分配现金股利",
    "共計分配現金股利",
    "现金股利人民币",
    "現金股利人民幣",
    "股利人民币",
    "股利人民幣",
)
_DIVIDEND_BASE_TERMS = (
    "total outstanding shares",
    "total share capital",
    "basis of the shares",
    "shares for the distribution",
    "股本总额",
    "股本總額",
    "总股本",
    "總股本",
    "为基数",
    "為基數",
)
_DIVIDEND_RATE_TERMS = ("per 10 shares", "for every 10 shares", "per 10", "每10股", "每 10 股", "每10")
_STRUCTURED_DOMAINS = {
    "dividend": _DIVIDEND_DOMAIN_TERMS,
    "bond": ("bond", "bonds", "notes", "债券", "債券", "公司债", "公司債", "短期融资券", "短期融資券", "偿还", "償還"),
    "litigation": ("litigation", "lawsuit", "arbitration", "claim", "诉讼", "訴訟", "仲裁", "案件"),
    "guarantee": ("guarantee", "担保", "擔保", "承诺", "承諾", "commitment"),
    "related_party": ("related party", "connected transaction", "关联交易", "關聯交易", "关连交易", "關連交易"),
    "share_change": ("share capital", "share change", "share changes", "股本", "股份变动", "股份變動", "股本变动", "股本變動", "总股本", "總股本"),
}
_REAL_AMOUNT_MISMATCH_ROLES = {
    "dividend_total",
    "bond_issue_amount",
    "bond_repayment_amount",
    "guarantee",
    "claim_amount",
    "transaction_amount",
    "penalty",
    "proceeds",
    "underwriting_scale",
    "share_count",
}
_REAL_PERCENTAGE_MISMATCH_ROLES = {
    "coupon_rate",
    "shareholding_ratio",
    "guarantee_ratio",
    "asset_ratio",
    "interest_rate",
}
_FACT_ROLE_ORDER = (
    "dividend_total",
    "dividend_rate_per_10_shares",
    "dividend_base_share_count",
    "coupon_rate",
    "shareholding_ratio",
    "guarantee_ratio",
    "asset_ratio",
    "interest_rate",
    "bond_issue_amount",
    "bond_repayment_amount",
    "guarantee",
    "claim_amount",
    "transaction_amount",
    "penalty",
    "proceeds",
    "underwriting_scale",
    "share_count",
    "amount",
)
_FACT_ROLE_LABELS = {
    "dividend_total": "股利总额",
    "dividend_rate_per_10_shares": "每10股派息",
    "dividend_base_share_count": "股本基数",
    "coupon_rate": "票面利率",
    "shareholding_ratio": "持股比例",
    "guarantee_ratio": "担保比例",
    "asset_ratio": "资产占比",
    "interest_rate": "利率",
    "bond_issue_amount": "债券发行金额",
    "bond_repayment_amount": "债券偿还金额",
    "guarantee": "担保金额",
    "claim_amount": "涉案金额",
    "transaction_amount": "交易金额",
    "penalty": "处罚金额",
    "proceeds": "募集资金",
    "underwriting_scale": "承销规模",
    "share_count": "股本/股份数量",
    "amount": "金额/数量",
}
_FACT_ROLE_HEADLINES = {
    "dividend_total": "利润分配股利总额不一致",
    "dividend_rate_per_10_shares": "利润分配每10股派息不一致",
    "dividend_base_share_count": "利润分配股本基数不一致",
    "coupon_rate": "债券票面利率不一致",
    "shareholding_ratio": "持股比例不一致",
    "guarantee_ratio": "担保比例不一致",
    "asset_ratio": "资产占比不一致",
    "interest_rate": "利率不一致",
    "bond_issue_amount": "债券发行金额不一致",
    "bond_repayment_amount": "债券偿还金额不一致",
    "guarantee": "担保金额不一致",
    "claim_amount": "重大诉讼涉案金额不一致",
    "transaction_amount": "交易金额不一致",
    "share_count": "股份数量不一致",
}
_FACT_ROLE_REVIEW_HINTS = {
    "dividend_total": "优先核对利润分配附注中的股利总额，排除股本基数和每10股派息口径影响。",
    "dividend_rate_per_10_shares": "优先核对利润分配方案中的每10股派息口径、币种和含税说明。",
    "dividend_base_share_count": "优先核对利润分配所采用的股本基数，确认是否为千股、股或期末总股本口径。",
    "coupon_rate": "优先核对同一期债券的名称、发行期次和票面利率，排除现金及银行结余或债务工具表格利率影响。",
    "shareholding_ratio": "优先核对同一主体和同一交易事项下的持股比例，排除历史股本流水和表格错位影响。",
    "guarantee_ratio": "优先核对同一担保事项的担保对象、期间和比例口径。",
    "asset_ratio": "优先核对同一资产项目和同一表格口径下的占比，排除跨表格错配影响。",
    "interest_rate": "优先核对同一金融工具或存款项目的利率区间，排除债券票面利率和现金余额附注混用。",
    "bond_issue_amount": "优先核对期后事项或债券附注中的发行金额、币种、单位和统计期间。",
    "bond_repayment_amount": "优先核对期后事项或债券附注中的偿还金额、币种、单位和统计期间。",
    "guarantee": "优先核对担保或承诺披露中的担保金额、主体和余额/发生额口径。",
    "claim_amount": "优先核对重大诉讼附注中的涉案金额、案件主体和进展状态。",
    "transaction_amount": "优先核对同一交易事项的交易金额、交易主体和发生额/余额口径。",
    "share_count": "优先核对股本及股份变动表中的数量单位和变动期间。",
}
_DOMAIN_TOPIC_LABELS = {
    "dividend": ("利润分配", "Profit distribution"),
    "bond": ("债券发行及偿还", "Bond issuance and repayment"),
    "litigation": ("重大诉讼", "Material litigation"),
    "guarantee": ("担保/承诺", "Guarantee and commitment"),
    "related_party": ("关联交易", "Related party transaction"),
    "share_change": ("股份变动", "Share changes"),
}
_DOMAIN_TOPIC_ORDER = ("dividend", "bond", "litigation", "guarantee", "related_party", "share_change")


@dataclass(frozen=True)
class FactItem:
    fact_type: str
    role: str
    value: float | str
    context: str


@dataclass
class EventFacts:
    dates: set[str]
    date_roles: dict[str, set[str]]
    amounts: set[float]
    amount_roles: dict[str, set[float]]
    percentages: set[float]
    percentage_roles: dict[str, set[float]]
    entities: set[str]
    statuses: set[str]
    domains: set[str]
    fact_items: list[FactItem]


@dataclass
class EventCandidate:
    side: ReportSide
    topic_key: str
    topic_label: str
    text: str
    normalized_text: str
    page: int
    section: str | None
    evidence: Evidence
    facts: EventFacts
    tokens: set[str]
    action_terms: set[str]
    section_bucket: str
    specific_entities: set[str]
    fact_strength: int
    event_identity_signature: tuple[str, ...] = ()


@dataclass(frozen=True)
class AmountMention:
    value: float
    role: str
    context: str
    reliable: bool


@dataclass(frozen=True)
class PercentageMention:
    value: float
    role: str
    context: str
    reliable: bool


def build_disclosure_coverage(profile_a, profile_h) -> tuple[list[DisclosureCoverageItem], list[Diff]]:
    """Build non-diff coverage items and event fact diffs for two profiles."""
    coverage: list[DisclosureCoverageItem] = []

    for pd in compare_metrics(profile_a, profile_h):
        if pd.diff_type == "metric_missing":
            coverage.append(_profile_diff_to_coverage(pd, "metric"))

    for pd in compare_narratives(profile_a, profile_h):
        if pd.diff_type == "topic_missing":
            coverage.append(_profile_diff_to_coverage(pd, "narrative"))
        elif pd.diff_type == "narrative_depth":
            coverage.append(_profile_diff_to_coverage(pd, "narrative", forced_status="matched"))

    for pd in compare_structures(profile_a, profile_h):
        if pd.diff_type == "structure_missing":
            coverage.append(_profile_diff_to_coverage(pd, "structure"))

    coverage.extend(_document_rule_coverage(profile_a, profile_h))

    event_coverage, event_diffs = run_event_checks_on_profiles(profile_a, profile_h)
    coverage.extend(event_coverage)

    return _dedupe_coverage(coverage), event_diffs


def run_event_checks_on_profiles(profile_a, profile_h) -> tuple[list[DisclosureCoverageItem], list[Diff]]:
    """Match event-like disclosures across all pages and compare key facts."""
    a_events = _extract_events(profile_a, ReportSide.A_SHARE)
    h_events = _extract_events(profile_h, ReportSide.H_SHARE)

    # 预计算完整匹配分数矩阵，用于判断最佳匹配是否“独占”
    score_matrix: list[list[float]] = []
    for a_event in a_events:
        row = [_event_score(a_event, h_event) for h_event in h_events]
        score_matrix.append(row)

    matched_h: set[int] = set()
    coverage: list[DisclosureCoverageItem] = []
    diffs: list[Diff] = []

    for a_idx, a_event in enumerate(a_events):
        best_idx = None
        best_score = 0.0
        for idx, h_event in enumerate(h_events):
            if idx in matched_h:
                continue
            score = score_matrix[a_idx][idx]
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= _EVENT_MATCH_THRESHOLD:
            h_event = h_events[best_idx]
            mismatches = _fact_mismatches(a_event, h_event)
            # 独占性检查：无硬锚点的匹配必须明显优于次优匹配，否则视为模棱两可
            if not _has_hard_fact_anchor(a_event, h_event) and _match_is_ambiguous(
                a_idx, best_idx, score_matrix, matched_h
            ):
                matched_h.add(best_idx)
                coverage.append(_event_coverage(a_event, h_event, best_score, mismatches, real_mismatch=False))
                continue
            matched_h.add(best_idx)
            strong_mismatches = _real_event_mismatches(a_event, h_event, best_score, mismatches)
            is_real_event_diff = bool(strong_mismatches)
            coverage.append(_event_coverage(a_event, h_event, best_score, mismatches, is_real_event_diff))
            if is_real_event_diff:
                diffs.append(_event_diff(a_event, h_event, best_score, strong_mismatches))
        else:
            coverage.append(_event_coverage(a_event, None, 0.0, []))

    for idx, h_event in enumerate(h_events):
        if idx not in matched_h:
            coverage.append(_event_coverage(None, h_event, 0.0, []))

    return _dedupe_coverage(coverage), diffs


def _match_is_ambiguous(
    a_idx: int,
    h_idx: int,
    score_matrix: list[list[float]],
    matched_h: set[int],
    margin: float = 0.12,
) -> bool:
    """判断当前最佳匹配是否模棱两可（次优匹配分数接近最佳）。

    当 A 侧或 H 侧存在另一个得分接近的候选时，说明仅靠主题/角色相似
    无法唯一确定这是同一事项，应降级处理以避免误报。
    """
    best = score_matrix[a_idx][h_idx]
    if best <= 0.0:
        return True
    # A 侧次优（除当前 H 外）
    a_second = 0.0
    for idx, score in enumerate(score_matrix[a_idx]):
        if idx == h_idx or idx in matched_h:
            continue
        if score > a_second:
            a_second = score
    # H 侧次优（除当前 A 外）
    h_second = 0.0
    for idx, row in enumerate(score_matrix):
        if idx == a_idx:
            continue
        score = row[h_idx]
        if score > h_second:
            h_second = score
    # 若任一侧次优接近最佳，则视为模棱两可
    return (best - a_second) < margin or (best - h_second) < margin


def _profile_diff_to_coverage(pd, category: str, forced_status: str | None = None) -> DisclosureCoverageItem:
    a_evidence = [ev for ev in pd.evidence if ev.side == ReportSide.A_SHARE]
    h_evidence = [ev for ev in pd.evidence if ev.side == ReportSide.H_SHARE]
    if forced_status:
        status = forced_status
    elif pd.a_pages or a_evidence:
        status = "a_only"
    else:
        status = "h_only"

    return DisclosureCoverageItem(
        coverage_id=_coverage_id("profile", category, status, pd.canonical_key or pd.topic_label or pd.summary.best()),
        category=category,
        status=status,
        topic=pd.topic,
        canonical_key=pd.canonical_key or pd.topic_label or pd.structure_code,
        a_pages=pd.a_pages or sorted({ev.page for ev in a_evidence}),
        h_pages=pd.h_pages or sorted({ev.page for ev in h_evidence}),
        a_evidence=a_evidence,
        h_evidence=h_evidence,
        match_confidence=0.75 if status == "matched" else 0.0,
        note=pd.summary.best(),
        source=pd.source,
    )


def _document_rule_coverage(profile_a, profile_h) -> list[DisclosureCoverageItem]:
    doc_a = getattr(profile_a, "source_doc", None)
    doc_h = getattr(profile_h, "source_doc", None)
    if not doc_a or not doc_h:
        return []

    from ahcc.check.disclosure import (
        _collect_sections,
        _concat_text,
        _extract_keywords_from_check_logic,
        _load_depth_rules,
        _load_framework_map,
    )

    coverage: list[DisclosureCoverageItem] = []
    framework_map = _load_framework_map()
    a_sections = _collect_sections(doc_a)
    h_sections = _collect_sections(doc_h)

    for a_name, expected_h in framework_map.items():
        if a_name in a_sections and expected_h and expected_h not in h_sections:
            pages = sorted(a_sections[a_name])
            coverage.append(_section_coverage("a_only", a_name, expected_h, pages, []))
        elif expected_h in h_sections and a_name not in a_sections:
            pages = sorted(h_sections[expected_h])
            coverage.append(_section_coverage("h_only", a_name, expected_h, [], pages))

    a_text = _concat_text(doc_a)
    h_text = _concat_text(doc_h)
    for rule in _load_depth_rules():
        keywords = _extract_keywords_from_check_logic(rule.get("check_logic", ""))
        if not keywords:
            continue
        a_has = any(kw in a_text for kw in keywords)
        h_has = any(kw in h_text for kw in keywords)
        if a_has == h_has:
            continue
        status = "a_only" if a_has else "h_only"
        topic_zh = rule.get("topic", {}).get("zh", "") or rule.get("rule_id", "披露规则")
        topic_en = rule.get("topic", {}).get("en", "") or rule.get("rule_id", "Disclosure rule")
        side = ReportSide.A_SHARE if a_has else ReportSide.H_SHARE
        coverage.append(
            DisclosureCoverageItem(
                coverage_id=_coverage_id("depth", rule.get("rule_id", ""), status, ",".join(keywords)),
                category="depth_rule",
                status=status,
                topic=LocalizedString(zh=topic_zh, en=topic_en),
                canonical_key=rule.get("rule_id"),
                a_pages=[1] if a_has else [],
                h_pages=[1] if h_has else [],
                a_evidence=[
                    Evidence(side=ReportSide.A_SHARE, page=1, snippet="关键词：" + ", ".join(keywords[:5]))
                ] if a_has else [],
                h_evidence=[
                    Evidence(side=ReportSide.H_SHARE, page=1, snippet="关键词：" + ", ".join(keywords[:5]))
                ] if h_has else [],
                match_confidence=0.0,
                note=f"{'A股' if side == ReportSide.A_SHARE else 'H股'}披露了规则关键词：{', '.join(keywords[:5])}",
                source="depth_rule",
            )
        )

    return coverage


def _section_coverage(
    status: str,
    a_name: str,
    h_name: str,
    a_pages: list[int],
    h_pages: list[int],
) -> DisclosureCoverageItem:
    side = ReportSide.A_SHARE if status == "a_only" else ReportSide.H_SHARE
    page = (a_pages or h_pages or [1])[0]
    section = a_name if status == "a_only" else h_name
    note = f"A股章节「{a_name}」与H股章节「{h_name}」未形成双边覆盖"
    evidence = Evidence(
        side=side,
        page=page,
        snippet=note,
        section=section,
    )
    return DisclosureCoverageItem(
        coverage_id=_coverage_id("location", status, a_name, h_name),
        category="location",
        status=status,
        topic=LocalizedString(zh=f"披露位置：{a_name}", en=f"Disclosure location: {h_name}"),
        canonical_key=a_name,
        a_pages=a_pages,
        h_pages=h_pages,
        a_evidence=[evidence] if status == "a_only" else [],
        h_evidence=[evidence] if status == "h_only" else [],
        match_confidence=0.0,
        note=note,
        source="location_rule",
    )


def _extract_events(profile, side: ReportSide, max_events: int | None = None) -> list[EventCandidate]:
    doc = getattr(profile, "source_doc", None)
    raw_segments = getattr(doc, "texts", []) if doc is not None else []
    if not raw_segments:
        raw_segments = [
            seg
            for block in getattr(profile, "narratives", [])
            for seg in getattr(block, "segments", [])
        ]

    events: list[EventCandidate] = []
    seen: set[tuple[int, str]] = set()
    for seg in raw_segments:
        raw_text = getattr(seg, "text", "") or ""
        if len(raw_text.strip()) < 20:
            continue
        page = int(getattr(seg, "page", 1) or 1)
        section = getattr(seg, "section", None)
        for text in _split_event_fragments(raw_text):
            normalized = _normalize_text(text)
            if len(normalized) < 20 or not _has_event_signal(normalized):
                continue
            if _looks_like_layout_or_overview(normalized):
                continue

            key = (page, normalized[:120])
            if key in seen:
                continue
            seen.add(key)

            facts = _extract_facts(normalized)
            topic_key = _event_topic_key(text, facts)
            action_terms = _action_terms(normalized)
            specific_entities = _specific_entities_for_event(normalized, facts.entities)
            candidate = EventCandidate(
                side=side,
                topic_key=topic_key,
                topic_label=get_topic_name(topic_key, "zh"),
                text=text,
                normalized_text=normalized,
                page=page,
                section=section,
                evidence=Evidence(
                    side=side,
                    page=page,
                    bbox=getattr(seg, "bbox", None),
                    snippet=text[:220],
                    section=section,
                ),
                facts=facts,
                tokens=_tokens(normalized),
                action_terms=action_terms,
                section_bucket=_section_bucket(section, topic_key),
                specific_entities=specific_entities,
                fact_strength=_fact_strength(facts, action_terms, specific_entities),
                event_identity_signature=(),
            )
            candidate = _with_identity_signature(candidate)
            events.append(candidate)
            if max_events is not None and len(events) >= max_events:
                break
        if max_events is not None and len(events) >= max_events:
            break
    return events


def _event_topic_key(text: str, facts: EventFacts) -> str:
    if "dividend" in facts.domains:
        return "dividend_distribution"
    if "bond" in facts.domains:
        return "bond_events"
    if "litigation" in facts.domains:
        return "litigation"
    if "guarantee" in facts.domains:
        return "guarantee_commitment"
    if "related_party" in facts.domains:
        return "related_party"
    if "share_change" in facts.domains:
        return "share_changes"
    return get_topic_for_text(text, max_topics=1)[0]


def _normalize_text(text: str) -> str:
    simplified = to_simplified(text or "")
    return re.sub(r"\s+", " ", simplified).strip().lower()


def _has_event_signal(text: str) -> bool:
    return any(keyword.lower() in text for keyword in EVENT_KEYWORDS) or bool(_fact_domains(text))


def _split_event_fragments(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= 260:
        return [compact]
    numbered_note_boundary = r"(?=\(\d+\)\s+[A-Za-z])|(?=（\d+）\s*[A-Za-z])"
    parts = [
        p.strip()
        for p in re.split(
            rf"(?<=[。！？；;])\s+|\n+| {{2,}}|(?=（[一二三四五六七八九十]+）)|(?=\d+、)|{numbered_note_boundary}",
            compact,
        )
        if p.strip()
    ]
    fragments: list[str] = []
    for part in parts:
        if len(part) < 20:
            continue
        if len(part) <= 360:
            fragments.append(part)
            continue
        sentences = [s.strip() for s in re.split(r"(?<=[。！？；;])", part) if s.strip()]
        fragments.extend(s for s in sentences if len(s) >= 20)
    return fragments or [compact[:360]]


def _extract_facts(text: str) -> EventFacts:
    amount_mentions = _amount_mentions(text)
    percentage_mentions = _percentage_mentions(text)
    dates = _extract_dates(text)
    statuses = {kw for kw in STATUS_KEYWORDS if kw.lower() in text}
    domains = _fact_domains(text)
    fact_items = [
        FactItem(fact_type="amount", role=mention.role, value=mention.value, context=mention.context)
        for mention in amount_mentions
    ]
    fact_items.extend(
        FactItem(fact_type="percentage", role=mention.role, value=mention.value, context=mention.context)
        for mention in percentage_mentions
    )
    fact_items.extend(FactItem(fact_type="date", role="date", value=date, context=text[:160]) for date in dates)
    fact_items.extend(FactItem(fact_type="status", role="status", value=status, context=text[:160]) for status in statuses)
    return EventFacts(
        dates=dates,
        date_roles=_extract_date_roles(text),
        amounts={mention.value for mention in amount_mentions},
        amount_roles=_amount_roles(amount_mentions),
        percentages={mention.value for mention in percentage_mentions},
        percentage_roles=_percentage_roles(percentage_mentions),
        entities=_extract_entities(text),
        statuses=statuses,
        domains=domains,
        fact_items=fact_items,
    )


def _extract_dates(text: str) -> set[str]:
    dates: set[str] = set()
    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    for y, m, d in re.findall(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
        dates.add(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    for d, month, y in re.findall(
        r"(\d{1,2})\s+("
        r"january|february|march|april|may|june|july|august|september|october|november|december"
        r")\s+(20\d{2})",
        text,
        flags=re.IGNORECASE,
    ):
        dates.add(f"{int(y):04d}-{month_names[month.lower()]:02d}-{int(d):02d}")
    for month, d, y in re.findall(
        r"("
        r"january|february|march|april|may|june|july|august|september|october|november|december"
        r")\s+(\d{1,2}),?\s+(20\d{2})",
        text,
        flags=re.IGNORECASE,
    ):
        dates.add(f"{int(y):04d}-{month_names[month.lower()]:02d}-{int(d):02d}")
    for y, m, d in re.findall(r"(20\d{2})[年/\-.](\d{1,2})(?:[月/\-.](\d{1,2}))?", text):
        if d:
            dates.add(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
        else:
            dates.add(f"{int(y):04d}-{int(m):02d}")
    return dates


def _extract_date_roles(text: str) -> dict[str, set[str]]:
    dates = _extract_dates(text)
    roles: dict[str, set[str]] = {}
    for date in dates:
        year = date[:4]
        contexts = [m.group(0) for m in re.finditer(rf".{{0,18}}{year}.{{0,24}}", text)]
        role = "general"
        context = " ".join(contexts)
        if any(term in context for term in ("会议", "审议", "董事会", "股东大会", "通过")):
            role = "meeting"
        if any(term in context for term in ("完成", "登记", "变更", "工商")):
            role = "completion"
        if any(term in context for term in ("辞任", "离任", "辞职", "委任", "任命")):
            role = "personnel"
        if any(term in context for term in ("上市规则", "规定", "生效", "规则")):
            role = "rule"
        if "报告期" in context or "年度" in context:
            role = "reporting_period" if role == "general" else role
        roles.setdefault(role, set()).add(date)
    return roles


def _extract_amounts(text: str) -> set[float]:
    return {mention.value for mention in _amount_mentions(text)}


def _amount_roles(mentions: list[AmountMention]) -> dict[str, set[float]]:
    roles: dict[str, set[float]] = {}
    for mention in mentions:
        roles.setdefault(mention.role, set()).add(mention.value)
    return roles


def _percentage_roles(mentions: list[PercentageMention]) -> dict[str, set[float]]:
    roles: dict[str, set[float]] = {}
    for mention in mentions:
        roles.setdefault(mention.role, set()).add(mention.value)
    return roles


def _amount_multiplier(unit: str) -> float:
    normalized = re.sub(r"\s+", "", (unit or "").lower())
    if normalized in {"", "rmb", "usd", "hkd", "元", "人民币", "人民幣", "港币", "港幣", "美元", "股", "share", "shares"}:
        return 1.0
    if normalized in {"千元", "rmbthousand", "thousand"}:
        return 1_000.0
    if normalized in {"千股", "thousandshare", "thousandshares"}:
        return 1_000.0
    if normalized in {"万元", "萬元", "万股", "萬股"}:
        return 10_000.0
    if normalized in {"亿元", "億元"}:
        return 100_000_000.0
    if normalized in {"million", "rmbmillion"}:
        return 1_000_000.0
    if normalized == "billion":
        return 1_000_000_000.0
    multipliers = {
        "元": 1.0,
        "万": 10_000.0,
        "万股": 10_000.0,
        "万元": 10_000.0,
        "亿": 100_000_000.0,
        "亿元": 100_000_000.0,
        "亿美元": 100_000_000.0,
        "港元": 1.0,
        "百万元": 1_000_000.0,
        "rmb thousand": 1_000.0,
        "rmb million": 1_000_000.0,
        "million": 1_000_000.0,
        "billion": 1_000_000_000.0,
    }
    return multipliers.get(unit.lower(), multipliers.get(unit, 1.0))


def _amount_mentions(text: str) -> list[AmountMention]:
    mentions: list[AmountMention] = []
    seen: set[tuple[int, int, float, str]] = set()

    def append_mention(value_text: str, unit: str, start_pos: int, end_pos: int) -> None:
        try:
            amount = round(float(value_text.replace(",", "")) * _amount_multiplier(unit), 2)
        except ValueError:
            return
        immediate = (text or "")[max(0, start_pos - 18):min(len(text or ""), end_pos + 24)]
        immediate_compact = re.sub(r"\s+", "", immediate).lower()
        if amount == 10.0 and any(term.lower().replace(" ", "") in immediate_compact for term in _DIVIDEND_RATE_TERMS):
            return
        start = max(0, start_pos - 56)
        end = min(len(text or ""), end_pos + 56)
        context = (text or "")[start:end]
        role = _amount_role(context, unit, immediate, amount)
        key = (start_pos, end_pos, amount, role)
        if key in seen:
            return
        seen.add(key)
        mentions.append(
            AmountMention(
                value=amount,
                role=role,
                context=context,
                reliable=_amount_context_reliable(context, unit, role),
            )
        )

    for match in _STRUCTURED_AMOUNT_RE.finditer(text or ""):
        unit = match.group("unit") or match.group("currency") or ""
        if not unit:
            continue
        append_mention(match.group("value"), unit, match.start(), match.end())

    for match in _AMOUNT_CONTEXT_RE.finditer(text or ""):
        value, _, unit = match.groups()
        append_mention(value, unit, match.start(), match.end())
    return mentions


def _amount_role(context: str, unit: str, immediate_context: str = "", value: float | None = None) -> str:
    compact = re.sub(r"\s+", "", context or "").lower()
    spaced = re.sub(r"\s+", " ", context or "").lower()
    immediate_compact = re.sub(r"\s+", "", immediate_context or "").lower()
    immediate_spaced = re.sub(r"\s+", " ", immediate_context or "").lower()
    unit_text = (unit or "").lower()
    is_share_unit = any(term in unit_text for term in ("share", "股"))
    has_dividend_context = any(
        term.lower().replace(" ", "") in compact or term.lower() in spaced
        for term in _DIVIDEND_DOMAIN_TERMS
    )
    # 财务报告附注编号/章节编号不应被识别为 share_count
    note_number_markers = ("附注", "编号", "序号", "第", "注", "(", ")", "（", "）", "·")
    looks_like_note_number = (
        value is not None
        and value < 1_000_000
        and not is_share_unit
        and any(marker in compact for marker in note_number_markers)
    )
    # 真正的股本/股份数量：数值较大（至少百万级），或带万股/千股/亿股单位
    # 单独一个 "22.3 股" 不可能是股份数量，更可能是附注编号或每股指标
    is_real_share_count = (
        value is None
        or value >= 1_000_000
        or any(term in unit_text for term in ("万股", "千股", "亿股", "thousandshares", "millionshares", "billionshares"))
    )
    if is_share_unit and any(term.lower().replace(" ", "") in compact for term in _DIVIDEND_BASE_TERMS):
        if not looks_like_note_number and is_real_share_count:
            return "dividend_base_share_count" if has_dividend_context else "share_count"
    if any(term.lower().replace(" ", "") in immediate_compact for term in _DIVIDEND_RATE_TERMS) or any(
        term in immediate_spaced for term in _DIVIDEND_RATE_TERMS
    ):
        return "dividend_rate_per_10_shares"
    if any(term.lower().replace(" ", "") in immediate_compact for term in _DIVIDEND_TOTAL_TERMS):
        return "dividend_total"
    if is_share_unit and any(term.lower().replace(" ", "") in immediate_compact for term in _DIVIDEND_BASE_TERMS):
        if not looks_like_note_number and is_real_share_count:
            return "dividend_base_share_count"
    if any(term.lower().replace(" ", "") in compact for term in _DIVIDEND_TOTAL_TERMS):
        return "dividend_total"
    if any(term.lower().replace(" ", "") in compact for term in _DIVIDEND_BASE_TERMS):
        if not looks_like_note_number and is_real_share_count:
            return "dividend_base_share_count" if has_dividend_context else "share_count"
    if "issuanceamount" in compact or "发行金额" in compact or "發行金額" in compact or ("issued" in spaced and "bond" in spaced):
        return "bond_issue_amount"
    if "repayment" in spaced and ("bond" in spaced or "note" in spaced):
        return "bond_repayment_amount"
    if "claimamount" in compact or "涉案金额" in compact or "涉案金額" in compact:
        return "claim_amount"
    if "guaranteeamount" in compact or "担保金额" in compact or "擔保金額" in compact:
        return "guarantee"
    if "transactionamount" in compact or "交易金额" in compact or "交易金額" in compact:
        return "transaction_amount"
    for role, terms in _AMOUNT_ROLE_TERMS.items():
        if any(term.lower() in compact or term.lower() in unit_text for term in terms):
            # share_count 必须满足数量级/单位要求，避免附注编号被误判
            if role == "share_count" and not is_real_share_count:
                continue
            return role
    if any(term in compact for term in ("金额", "价款", "资金", "规模", "人民币", "美元", "港元", "rmb", "usd", "hkd")):
        return "amount"
    return "unknown"


def _amount_context_reliable(context: str, unit: str, role: str) -> bool:
    compact = re.sub(r"\s+", "", context or "").lower()
    if role == "unknown":
        return False
    if role.startswith("dividend_") or role in _REAL_AMOUNT_MISMATCH_ROLES:
        return True
    if unit in {"万", "亿"} and not any(term in compact for term in _AMOUNT_CONTEXT_TERMS):
        return False
    if re.search(r"\d+(?:\.\d+)?年", compact) and not re.search(r"20\d{2}年", compact):
        return False
    if re.search(r"20\d{2}\d{1,2}(a股)?年月", compact):
        return False
    return True


def _extract_percentages(text: str) -> set[float]:
    return {mention.value for mention in _percentage_mentions(text)}


def _percentage_mentions(text: str) -> list[PercentageMention]:
    mentions: list[PercentageMention] = []
    seen: set[tuple[int, int, float, str]] = set()
    for match in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%", text or ""):
        try:
            value = round(float(match.group(1)), 4)
        except ValueError:
            continue
        start = max(0, match.start() - 80)
        end = min(len(text or ""), match.end() + 80)
        context = (text or "")[start:end]
        role = _percentage_role(context)
        key = (match.start(), match.end(), value, role)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            PercentageMention(
                value=value,
                role=role,
                context=context,
                reliable=role not in {"unknown", "ratio"},
            )
        )
    return mentions


def _percentage_role(context: str) -> str:
    compact = re.sub(r"\s+", "", context or "").lower()
    spaced = re.sub(r"\s+", " ", context or "").lower()
    if any(term in spaced for term in ("coupon rate", "coupon rates", "nominal interest rate")) or any(
        term in compact for term in ("票面利率", "名义利率", "票息")
    ):
        return "coupon_rate"
    if any(term in spaced for term in ("shareholding ratio", "shareholding percentage", "equity interest")) or any(
        term in compact for term in ("持股比例", "持股占比", "股权比例")
    ):
        return "shareholding_ratio"
    if "guarantee ratio" in spaced or any(term in compact for term in ("担保比例", "担保比率")):
        return "guarantee_ratio"
    if any(term in spaced for term in ("asset ratio", "assets ratio", "percentage of assets")) or any(
        term in compact for term in ("资产占比", "资产比例", "占比")
    ):
        return "asset_ratio"
    if any(term in spaced for term in ("interest rate", "interest rates", "bank balances", "deposits")) or any(
        term in compact for term in ("利率", "银行结余", "存款")
    ):
        return "interest_rate"
    if "ratio" in spaced or "percentage" in spaced or "比例" in compact:
        return "ratio"
    return "unknown"


def _extract_entities(text: str) -> set[str]:
    entities: set[str] = set()
    suffixes = "股份有限公司|有限责任公司|有限公司|银行|证券|集团|分行|公司"
    for match in re.findall(rf"([一-龥A-Za-z0-9（）()·]{{2,30}}(?:{suffixes}))", text):
        cleaned = re.sub(r"^[，。、；：\s]+", "", match)
        if len(cleaned) >= 3:
            entities.add(cleaned)
    return entities


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z]{3,}|[一-龥]{2,}", text)
    stop = {"本公司", "本集团", "本行", "报告期", "年度报告", "公司", "银行", "股份有限公司"}
    return {tok for tok in raw if tok not in stop and len(tok) >= 2}


def _looks_like_layout_or_overview(text: str) -> bool:
    terms = ("全面助力", "深度服务", "专业赋能", "全球拓展布局", "全球投资交易", "全球资产配置", "hong kong", "india")
    if sum(1 for term in terms if term in text) >= 4:
        return True
    number_count = len(re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text))
    branch_terms = ("家证券营业部", "家证券分公司", "家期货营业部", "家期货分公司", "家一级子公司", "家参股公司")
    return number_count >= 8 and sum(1 for term in branch_terms if term in text) >= 2


def _fact_domains(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text or "").lower()
    spaced = re.sub(r"\s+", " ", text or "").lower()
    domains: set[str] = set()
    for domain, terms in _STRUCTURED_DOMAINS.items():
        for term in terms:
            lowered = term.lower()
            if lowered in spaced or lowered.replace(" ", "") in compact:
                domains.add(domain)
                break
    return domains


def _domain_action_terms(text: str) -> set[str]:
    domains = _fact_domains(text)
    actions = set(domains)
    compact = re.sub(r"\s+", "", text or "").lower()
    spaced = re.sub(r"\s+", " ", text or "").lower()
    if "dividend" in domains:
        actions.add("dividend_distribution")
    if "bond" in domains:
        if "repayment" in spaced or "偿还" in compact or "償還" in compact:
            actions.add("bond_repayment")
        if "issued" in spaced or "issuance" in spaced or "发行" in compact or "發行" in compact:
            actions.add("bond_issue")
    if "litigation" in domains:
        actions.add("litigation")
    if "guarantee" in domains:
        actions.add("guarantee")
    if "related_party" in domains:
        actions.add("related_party")
    if "share_change" in domains:
        actions.add("share_change")
    return actions


def _domain_action_terms(text: str) -> set[str]:
    domains = _fact_domains(text)
    actions = set(domains)
    compact = re.sub(r"\s+", "", text or "").lower()
    spaced = re.sub(r"\s+", " ", text or "").lower()
    if "dividend" in domains:
        actions.add("dividend_distribution")
    if "bond" in domains:
        if "repayment" in spaced or "偿还" in compact or "償還" in compact:
            actions.add("bond_repayment")
        if "issued" in spaced or "issuance" in spaced or "发行" in compact or "發行" in compact:
            actions.add("bond_issue")
    if "litigation" in domains:
        actions.add("litigation")
    if "guarantee" in domains:
        actions.add("guarantee")
    if "related_party" in domains:
        actions.add("related_party")
    if "share_change" in domains:
        actions.add("share_change")
    return actions


# ---------------------------------------------------------------------------
# 事项身份签名（第二阶段核心）：从文本中提取能唯一标识一笔事项的关键词。
# 同一宏观主题下不同具体事项（不同期次债券、不同年度分红等）凭签名隔离。
# ---------------------------------------------------------------------------

_BOND_NAME_RE = re.compile(
    r"(\d{4}\s*年\s*[一-龥a-z]{0,18}?(?:转债|债券|公司债|企业债|短期融资券|超短期融资券|中期票据|金融债|绿色金融债|乡村振兴债|收益凭证|cd|cp|mtn|ppn|abs))",
    re.IGNORECASE,
)
_BOND_ISSUE_NO_RE = re.compile(
    r"(\d{4})\s*年度\s*第\s*([一二三四五六七八九十〇0-9]+)\s*期|"
    r"第\s*([一二三四五六七八九十〇0-9]+)\s*期|"
    r"(\d{4})\s*年\s*第\s*([一二三四五六七八九十〇0-9]+)\s*期|"
    r"(短期融资券|超短期融资券)\s*([a-z]?\d{3,})",
    re.IGNORECASE,
)
_BOND_CODE_RE = re.compile(r"((?:\d{2})?[\s\-]?[一-龥]{2,6}债\s*\d{3,})", re.IGNORECASE)
_DIVIDEND_YEAR_RE = re.compile(r"(\d{4})\s*年度.*?利润分配|(\d{4})\s*年.*?分红|(\d{4})\s*年.*?派息|(\d{4})\s*年度.*?派息")
_DIVIDEND_TIMING_RE = re.compile(r"(中期|末期|年度|半年度|季度).*?利润分配|(中期|末期).*?股息|(中期|末期).*?分红")
_LITIGATION_NAME_RE = re.compile(r"([一-龥（）()a-z0-9\-]{4,30}?(?:纠纷|诉讼|仲裁|案件|合同纠纷|侵权纠纷|股权纠纷))", re.IGNORECASE)
_GUARANTEE_COUNTERPARTY_RE = re.compile(r"(?:为|向)\s*([一-龥（）()a-z0-9\-]{2,30}?(?:公司|集团|银行|企业|个人))\s*(?:提供|出具的)")
_RELATED_PARTY_ROLE_RE = re.compile(r"(控股股东|实际控制人|关联方|子公司|联营公司|合营公司|关键管理人员|董事|监事|高管|近亲属)")


def _extract_event_identity_terms(text: str, topic_key: str, facts: EventFacts) -> set[str]:
    """从事件文本中提取结构化身份标识词。"""
    terms: set[str] = set()
    compact = re.sub(r"\s+", "", text or "").lower()
    spaced = re.sub(r"\s+", " ", text or "").lower()

    # 债券：年份、期次、名称、代码
    if topic_key == "bond_events" or "bond" in facts.domains:
        # 优先提取完整债券名称（含年份）
        for m in _BOND_NAME_RE.finditer(text):
            name = re.sub(r"\s+", "", m.group(1).lower())
            if name:
                terms.add(f"name={name}")
        # 单独提取年份
        for m in re.finditer(r"(\d{4})\s*年", text):
            terms.add(f"year={m.group(1)}")
        # 期次
        for m in re.finditer(r"第\s*([一二三四五六七八九十〇0-9]+)\s*期", text):
            terms.add(f"phase={m.group(1)}")
        for m in _BOND_ISSUE_NO_RE.finditer(text):
            groups = [g for g in m.groups() if g]
            if groups:
                terms.add(re.sub(r"\s+", "", "".join(groups).lower()))
        for m in _BOND_CODE_RE.finditer(text):
            terms.add(re.sub(r"\s+", "", m.group(1).lower()))

    # 利润分配：年度、中期/末期、决议日期、每10股派息
    if topic_key == "dividend_distribution" or "dividend" in facts.domains:
        for m in _DIVIDEND_YEAR_RE.finditer(text):
            terms.add(f"year={next(g for g in m.groups() if g)}")
        for m in _DIVIDEND_TIMING_RE.finditer(text):
            terms.add(f"timing={next(g for g in m.groups() if g)}")
        specific_dates = sorted(_specific_dates(facts.dates))
        if len(specific_dates) == 1:
            terms.add(f"date={specific_dates[0]}")
        elif len(specific_dates) > 1:
            # 多个具体日期时，用最早日期作为分红方案标识
            terms.add(f"date={specific_dates[0]}")
        rates = sorted(facts.amount_roles.get("dividend_rate_per_10_shares", set()))
        if len(rates) == 1:
            terms.add(f"rate10={rates[0]}")

    # 诉讼/仲裁：案件名、涉案主体
    if topic_key == "litigation" or "litigation" in facts.domains:
        for m in _LITIGATION_NAME_RE.finditer(text):
            terms.add(re.sub(r"\s+", "", m.group(1).lower())[:40])
        for entity in facts.entities:
            if any(suffix in entity for suffix in ("公司", "银行", "集团", "证券")):
                cleaned = re.sub(r"[\s，。、；：]+", "", entity)
                if cleaned:
                    terms.add(f"entity={cleaned}")

    # 担保：被担保方
    if topic_key == "guarantee_commitment" or "guarantee" in facts.domains:
        for m in _GUARANTEE_COUNTERPARTY_RE.finditer(text):
            terms.add(f"counterparty={re.sub(r'[\s，。、；：]+', '', m.group(1))}")

    # 关联交易：对手方、关联关系
    if topic_key == "related_party" or "related_party" in facts.domains:
        for entity in facts.entities:
            cleaned = re.sub(r"[\s，。、；：]+", "", entity)
            if cleaned and cleaned not in _GENERIC_ENTITIES and len(cleaned) >= 4:
                terms.add(f"counterparty={cleaned}")
        for m in _RELATED_PARTY_ROLE_RE.finditer(text):
            terms.add(f"role={m.group(1)}")

    # 股份变动：所属年度/报告期
    if topic_key == "share_changes" or "share_change" in facts.domains:
        for m in re.finditer(r"(\d{4})\s*年", text):
            terms.add(f"year={m.group(1)}")

    return terms


def _with_identity_signature(candidate: EventCandidate) -> EventCandidate:
    """为 EventCandidate 填充结构化事项身份签名。"""
    terms = _extract_event_identity_terms(candidate.text, candidate.topic_key, candidate.facts)
    # 加入已提取的特定实体作为补充身份锚点，但过滤掉含通用主体词的弱实体
    for e in candidate.specific_entities:
        if e and not any(ge in e for ge in _GENERIC_ENTITIES):
            terms.add(f"entity={e}")
    # 稳定排序，便于分组和比较
    signature = tuple(sorted(terms))
    candidate.event_identity_signature = signature
    return candidate


def _effective_signature(event: EventCandidate) -> tuple[str, ...]:
    """返回有效签名：过滤掉仅含通用实体等弱身份标识的签名。"""
    strong: list[str] = []
    for term in event.event_identity_signature:
        if term.startswith("entity="):
            entity = term[7:]
            if any(ge in entity for ge in _GENERIC_ENTITIES):
                continue
        strong.append(term)
    return tuple(strong)


def _signature_overlap(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    """判断两个事件有效签名是否有交集。空签名不与任何签名相交。"""
    a = _effective_signature(a_event)
    h = _effective_signature(h_event)
    if not a or not h:
        return False
    return bool(set(a) & set(h))


def _signature_similarity(a_event: EventCandidate, h_event: EventCandidate) -> float:
    """计算两个事件有效签名集合的 Jaccard 相似度。"""
    a = set(_effective_signature(a_event))
    b = set(_effective_signature(h_event))
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)


def _action_terms(text: str) -> set[str]:
    return {keyword for keyword in _ACTION_KEYWORDS if keyword.lower() in text} | _domain_action_terms(text)


def _section_bucket(section: str | None, topic_key: str | None = None) -> str:
    value = (section or topic_key or "").strip().lower()
    if value in {"corporate_governance", "governance"} or "governance" in value:
        return "corporate_governance"
    if value in _EVENT_DIFF_EXCLUDED_SECTIONS:
        return "financial_statement"
    if value in {"significant_events", "share_changes", "directors_report"}:
        return value
    # 将已知的 topic_key 映射到稳定的 section_bucket，防止债券/利润分配被泛化
    topic_bucket = {
        "dividend_distribution": "dividend_distribution",
        "bond_events": "bond_events",
        "related_party": "related_party",
        "guarantee_commitment": "guarantee_commitment",
        "litigation": "litigation",
        "share_changes": "share_changes",
    }
    if value in topic_bucket:
        return topic_bucket[value]
    return value or "unknown"


def _specific_entities_for_event(text: str, entities: set[str]) -> set[str]:
    result = _specific_entities(entities)
    compact = re.sub(r"\s+", "", text or "")
    filtered: set[str] = set()
    for entity in result:
        position = compact.find(entity)
        # Page headers often contain only the issuer name and page number. Do
        # not let that generic header make unrelated paragraphs match.
        if 0 <= position <= 60 and re.search(re.escape(entity) + r"\d{1,4}", compact[:90]):
            continue
        filtered.add(entity)
    return filtered


def _fact_strength(facts: EventFacts, action_terms: set[str], specific_entities: set[str]) -> int:
    strength = 0
    strength += 2 if facts.dates else 0
    strength += 2 if facts.amounts else 0
    strength += 2 if facts.amount_roles else 0
    strength += 2 if facts.percentages else 0
    strength += 2 if specific_entities else 0
    strength += 1 if facts.domains else 0
    strength += 1 if action_terms else 0
    return strength


def _comparison_text(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = re.sub(r"华泰证券|huatai securities|中信银行股份有限公司|第[一二三四五六七八九十]+章|公司治理、环境和社会", "", normalized)
    normalized = re.sub(r"\b\d{1,4}\b", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _near_duplicate_event_text(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    a_text = _comparison_text(a_event.text)
    h_text = _comparison_text(h_event.text)
    if len(a_text) < 40 or len(h_text) < 40:
        return False
    return SequenceMatcher(None, a_text[:700], h_text[:700]).ratio() >= 0.86


def _is_generic_report_date(date: str) -> bool:
    """判断日期是否为财报截止日/仅含年月而无具体事项日的通用日期。

    例如 2025-12-31、2025-06-30、2025-03 等年报/半年报/季报截止日常被多篇
    披露同时引用，不能作为同一笔事项的身份锚点。
    """
    if not date:
        return False
    # 去掉年份，只看月日或仅月份
    tail = date[5:] if len(date) >= 7 else date
    if tail in _GENERIC_REPORT_DATES:
        return True
    # 仅年月（如 2025-06）也视为通用
    if re.fullmatch(r"20\d{2}-(0[369]|12)", date):
        return True
    return False


def _specific_dates(dates: set[str]) -> set[str]:
    """返回去除通用财报日期后的具体日期集合。"""
    return {d for d in dates if not _is_generic_report_date(d)}


def _event_score(a_event: EventCandidate, h_event: EventCandidate) -> float:
    token_score = _jaccard(a_event.tokens, h_event.tokens)
    text_score = SequenceMatcher(None, a_event.normalized_text[:500], h_event.normalized_text[:500]).ratio()
    if not _has_shared_event_anchor(a_event, h_event, token_score, text_score):
        return min(0.35, token_score * 0.2 + text_score * 0.2)
    if _is_soft_governance_pair(a_event, h_event) and not _has_hard_fact_anchor(a_event, h_event):
        return min(0.41, token_score * 0.25 + text_score * 0.25)

    fact_score = 0.0
    for attr in ("amounts", "percentages", "statuses"):
        a_values = getattr(a_event.facts, attr)
        h_values = getattr(h_event.facts, attr)
        if not a_values or not h_values:
            continue
        if attr in {"amounts", "percentages"}:
            fact_score += 0.12 if _numeric_overlap(a_values, h_values) else 0.0
        else:
            fact_score += 0.12 if a_values & h_values else 0.0
    # 日期锚点只使用具体日期；通用财报截止日不计入事实分
    if _specific_dates(a_event.facts.dates) & _specific_dates(h_event.facts.dates):
        fact_score += 0.12
    if a_event.specific_entities & h_event.specific_entities:
        fact_score += 0.12
    action_score = 0.10 if a_event.action_terms & h_event.action_terms else 0.0
    section_score = 0.06 if a_event.section_bucket == h_event.section_bucket else 0.0
    topic_score = 0.12 if a_event.topic_key == h_event.topic_key else 0.0
    domain_score = 0.18 if a_event.facts.domains & h_event.facts.domains else 0.0
    shared_roles = _shared_amount_roles(a_event, h_event)
    role_score = 0.12 if shared_roles else 0.0
    structured_score = 0.14 if (shared_roles & _REAL_AMOUNT_MISMATCH_ROLES and domain_score) else 0.0
    return min(
        1.0,
        topic_score
        + section_score
        + action_score
        + domain_score
        + role_score
        + structured_score
        + token_score * 0.24
        + text_score * 0.26
        + fact_score,
    )


def _has_shared_event_anchor(
    a_event: EventCandidate,
    h_event: EventCandidate,
    token_score: float,
    text_score: float,
) -> bool:
    if _has_hard_fact_anchor(a_event, h_event):
        return True
    if _is_structured_fact_pair(a_event, h_event):
        return True
    if a_event.action_terms & h_event.action_terms and token_score >= 0.12:
        return True
    return text_score >= 0.82 and token_score >= 0.18


def _shared_amount_roles(a_event: EventCandidate, h_event: EventCandidate) -> set[str]:
    shared = set(a_event.facts.amount_roles) & set(h_event.facts.amount_roles)
    shared.discard("unknown")
    return shared


def _is_structured_fact_pair(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if not (a_event.facts.domains & h_event.facts.domains):
        return False
    if _shared_amount_roles(a_event, h_event):
        return True
    # 日期必须是非通用财报日期，否则同一年报期内不同事项会被误判为结构化配对
    return bool(
        a_event.action_terms & h_event.action_terms
        and (_specific_dates(a_event.facts.dates) or _specific_dates(h_event.facts.dates))
    )


def _has_hard_fact_anchor(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if _specific_dates(a_event.facts.dates) & _specific_dates(h_event.facts.dates):
        return True
    if _numeric_overlap(a_event.facts.amounts, h_event.facts.amounts):
        return True
    return bool(a_event.specific_entities & h_event.specific_entities)


def _is_soft_governance_pair(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if a_event.section_bucket != "corporate_governance" or h_event.section_bucket != "corporate_governance":
        return False
    combined = f"{a_event.normalized_text} {h_event.normalized_text}"
    return any(term in combined for term in _GOVERNANCE_SOFT_TERMS)


def _jaccard(a_values: set[str], h_values: set[str]) -> float:
    if not a_values or not h_values:
        return 0.0
    return len(a_values & h_values) / max(len(a_values | h_values), 1)


def _numeric_overlap(a_values: Iterable[float], h_values: Iterable[float], tolerance: float = 0.01) -> bool:
    for a_value in a_values:
        for h_value in h_values:
            base = max(abs(a_value), abs(h_value), 1.0)
            if abs(a_value - h_value) / base <= tolerance:
                return True
    return False


def _amount_role_tolerance(role: str) -> float:
    if role in {"share_count", "dividend_base_share_count"}:
        return 0.0
    return 0.01


def _role_numeric_overlap(role: str, a_values: Iterable[float], h_values: Iterable[float]) -> bool:
    return _numeric_overlap(a_values, h_values, tolerance=_amount_role_tolerance(role))


def _fact_mismatches(a_event: EventCandidate, h_event: EventCandidate) -> list[str]:
    mismatches: list[str] = []
    if _date_values_conflict(a_event, h_event):
        mismatches.append(_LABEL_DATE)
    checks = (
        (_LABEL_AMOUNT, a_event.facts.amounts, h_event.facts.amounts, "numeric"),
        (_LABEL_PERCENTAGE, a_event.facts.percentages, h_event.facts.percentages, "numeric"),
        (_LABEL_ENTITY, a_event.facts.entities, h_event.facts.entities, "set"),
        (_LABEL_STATUS, a_event.facts.statuses, h_event.facts.statuses, "set"),
    )
    for label, a_values, h_values, mode in checks:
        if not a_values or not h_values:
            continue
        if mode == "numeric":
            if label == _LABEL_AMOUNT and _amount_values_conflict(a_event.facts, h_event.facts):
                mismatches.append(label)
            elif label == _LABEL_PERCENTAGE and _percentage_values_conflict(a_event.facts, h_event.facts):
                mismatches.append(label)
            elif label == _LABEL_PERCENTAGE:
                continue
            elif label != _LABEL_AMOUNT and not _numeric_overlap(a_values, h_values):
                mismatches.append(label)
        elif not set(a_values) & set(h_values):
            mismatches.append(label)
    return mismatches


def _amount_values_conflict(a_facts: EventFacts, h_facts: EventFacts) -> bool:
    shared_roles = set(a_facts.amount_roles) & set(h_facts.amount_roles)
    shared_roles.discard("unknown")
    if not shared_roles:
        return bool(a_facts.amounts and h_facts.amounts and not _numeric_overlap(a_facts.amounts, h_facts.amounts))
    for role in shared_roles:
        a_values = a_facts.amount_roles.get(role, set())
        h_values = h_facts.amount_roles.get(role, set())
        if a_values and h_values and not _role_numeric_overlap(role, a_values, h_values):
            return True
    return False


def _percentage_values_conflict(a_facts: EventFacts, h_facts: EventFacts) -> bool:
    shared_roles = set(a_facts.percentage_roles) & set(h_facts.percentage_roles)
    shared_roles.discard("unknown")
    shared_roles.discard("ratio")
    if not shared_roles:
        return bool(a_facts.percentages and h_facts.percentages and not _numeric_overlap(a_facts.percentages, h_facts.percentages))
    for role in shared_roles:
        a_values = a_facts.percentage_roles.get(role, set())
        h_values = h_facts.percentage_roles.get(role, set())
        if a_values and h_values and not _numeric_overlap(a_values, h_values):
            return True
    return False


def _date_values_conflict(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if not a_event.facts.dates or not h_event.facts.dates:
        return False
    if a_event.facts.dates & h_event.facts.dates:
        return False
    a_roles = a_event.facts.date_roles or {"general": a_event.facts.dates}
    h_roles = h_event.facts.date_roles or {"general": h_event.facts.dates}
    shared_roles = set(a_roles) & set(h_roles)
    if not shared_roles:
        return False
    return any(not (a_roles[role] & h_roles[role]) for role in shared_roles)


def _strong_fact_mismatches(mismatches: list[str]) -> list[str]:
    return [label for label in mismatches if label in _STRONG_FACT_LABELS]


def _real_event_mismatches(
    a_event: EventCandidate,
    h_event: EventCandidate,
    confidence: float,
    mismatches: list[str],
) -> list[str]:
    """Return mismatches eligible for the real-difference list.

    Event extraction is intentionally broad so reviewers can see cross-page
    coverage. The real-difference list must be narrower: generic entity-only
    changes, financial statement table rows and template disclosure fragments
    are review coverage, not confirmed A/H inconsistencies.
    """
    if confidence < _EVENT_DIFF_THRESHOLD:
        return []

    strong = _strong_fact_mismatches(mismatches)
    if not strong:
        return []

    if _is_low_confidence_event_pair(a_event, h_event):
        return []

    if not _has_real_diff_identity_anchor(a_event, h_event, strong):
        return []

    if _LABEL_DATE in strong and not _date_mismatch_is_real(a_event, h_event):
        strong = [label for label in strong if label != _LABEL_DATE]

    if _LABEL_STATUS in strong and not _status_mismatch_is_real(a_event, h_event, strong):
        strong = [label for label in strong if label != _LABEL_STATUS]

    if _LABEL_AMOUNT in strong and not _amount_mismatch_is_real(a_event, h_event):
        strong = [label for label in strong if label != _LABEL_AMOUNT]

    # 数量级明显不符时，视为不同事项而非真实差异
    if _LABEL_AMOUNT in strong and _amount_magnitude_mismatch(a_event, h_event):
        strong = [label for label in strong if label != _LABEL_AMOUNT]

    if _LABEL_PERCENTAGE in strong and not _percentage_mismatch_is_real(a_event, h_event):
        strong = [label for label in strong if label != _LABEL_PERCENTAGE]

    # 结构化事项必须有共同签名或足够高的文本相似度才允许生成真实差异
    if strong and not (
        _event_identity_compatible(a_event, h_event)
        or _has_real_diff_identity_anchor_with_signature(a_event, h_event, strong)
    ):
        return []

    return strong


def _event_identity_compatible(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    """判断两个事件是否具备同一具体事项身份，才允许生成真实差异。

    使用有效签名（过滤掉通用实体等弱标识）：
    - 双方有效签名均非空且相交：视为同一具体事项。
    - 双方有效签名均空：需要高文本相似度 + 至少一个硬锚点。
    - 一方有效、一方为空：不允许，避免无身份事项被配对。

    债券需特别注意：同一年份可能有多只债券，因此仅有 year 重合不足以
    认定同一债券，必须同时有 name 或 phase 重合。
    """
    a_sig = set(_effective_signature(a_event))
    h_sig = set(_effective_signature(h_event))
    if not a_sig or not h_sig:
        if a_sig or h_sig:
            return False
        # 无有效签名事件：必须文本高度相似且有硬事实锚点
        text_score = SequenceMatcher(None, a_event.normalized_text[:500], h_event.normalized_text[:500]).ratio()
        token_score = _jaccard(a_event.tokens, h_event.tokens)
        return text_score >= 0.80 and token_score >= 0.30 and _has_hard_fact_anchor(a_event, h_event)

    overlap = a_sig & h_sig
    if not overlap:
        return False

    # 债券：同一年份多只债券很常见，year 单独重合不算
    if a_event.topic_key == "bond_events" or h_event.topic_key == "bond_events":
        non_year_overlap = {term for term in overlap if not term.startswith("year=")}
        return bool(non_year_overlap)

    return True


def _has_real_diff_identity_anchor_with_signature(
    a_event: EventCandidate,
    h_event: EventCandidate,
    strong: list[str],
) -> bool:
    """债券/利润分配必须依赖签名重叠；其他领域保留原有结构化锚点兜底。"""
    a_sig = _effective_signature(a_event)
    h_sig = _effective_signature(h_event)
    strict_topics = {"bond_events", "dividend_distribution"}
    is_strict = a_event.topic_key in strict_topics or h_event.topic_key in strict_topics
    # 债券/利润分配：只要任一方有有效签名，就必须签名重叠，不能用金额/比例角色兜底
    if is_strict and (a_sig or h_sig):
        return False
    return _has_real_diff_identity_anchor(a_event, h_event, strong)


def _is_low_confidence_event_pair(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if (
        (_is_excluded_event_section(a_event.section) or _is_excluded_event_section(h_event.section))
        and not _is_structured_fact_pair(a_event, h_event)
    ):
        return True
    if _looks_like_template_disclosure(a_event.text) or _looks_like_template_disclosure(h_event.text):
        return True
    if _is_dense_numeric_table_text(a_event.text) or _is_dense_numeric_table_text(h_event.text):
        return True
    if _looks_like_layout_or_overview(a_event.normalized_text) or _looks_like_layout_or_overview(h_event.normalized_text):
        return True
    if _looks_like_board_composition(a_event.normalized_text) or _looks_like_board_composition(h_event.normalized_text):
        return True
    if _looks_like_mda_operational_text(a_event.normalized_text) or _looks_like_mda_operational_text(h_event.normalized_text):
        return True
    if _is_soft_governance_pair(a_event, h_event) and not _has_hard_fact_anchor(a_event, h_event):
        return True
    if not _has_hard_fact_anchor(a_event, h_event) and not (a_event.action_terms & h_event.action_terms):
        return True
    return False


def _is_excluded_event_section(section: str | None) -> bool:
    return (section or "").strip().lower() in _EVENT_DIFF_EXCLUDED_SECTIONS


def _looks_like_template_disclosure(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return sum(1 for marker in _TEMPLATE_MARKERS if marker in compact) >= 2


def _is_dense_numeric_table_text(text: str) -> bool:
    numbers = re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text or "")
    if len(numbers) < 28:
        return False
    table_terms = ("合并资产负债表", "合并利润表", "合并所有者权益变动表", "单位：", "附注", "人民币元", "人民币千元")
    return any(term in (text or "") for term in table_terms)


def _looks_like_board_composition(text: str) -> bool:
    terms = ("董事会人员构成", "年龄组别", "董事类别", "执行董事", "非执行董事", "独立非执行董事", "女性董事", "男性董事")
    return sum(1 for term in terms if term in (text or "")) >= 3


def _looks_like_mda_operational_text(text: str) -> bool:
    terms = ("创新业务", "优化运营", "赋能员工", "智能审核", "ai", "智能助手", "风险监测", "翻译服务", "文档处理")
    return sum(1 for term in terms if term in (text or "")) >= 3


def _is_low_confidence_amount_pair(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if not a_event.facts.amounts or not h_event.facts.amounts:
        return True
    if len(a_event.facts.amounts) > 8 or len(h_event.facts.amounts) > 8:
        return True
    if a_event.facts.dates & h_event.facts.dates:
        return False
    if a_event.facts.statuses & h_event.facts.statuses:
        return False
    if a_event.specific_entities & h_event.specific_entities:
        return False
    return True


def _amount_magnitude_mismatch(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    """判断金额/数量差异是否为数量级异常，数量级异常更可能是不同事项。

    针对 share_count、dividend_base_share_count 等角色：A/H 值相差过大
    （如 2 亿 vs 111.67 亿，或 3 vs 62.04 亿）时，大概率不是同一事项口径。
    """
    shared_roles = _shared_amount_roles(a_event, h_event)
    for role in shared_roles:
        if role not in {"share_count", "dividend_base_share_count", "transaction_amount", "bond_issue_amount", "bond_repayment_amount"}:
            continue
        a_values = sorted(a_event.facts.amount_roles.get(role, set()))
        h_values = sorted(h_event.facts.amount_roles.get(role, set()))
        if not a_values or not h_values:
            continue
        a_val = a_values[0]
        h_val = h_values[0]
        base = max(abs(a_val), abs(h_val), 1.0)
        ratio = min(abs(a_val), abs(h_val)) / base
        delta = abs(a_val - h_val)
        if role in {"share_count", "dividend_base_share_count"}:
            if delta > 1_000_000 and ratio < 0.1:
                return True
        else:
            if delta > 1_000_000_000 and ratio < 0.05:
                return True
    return False


def _amount_mismatch_is_real(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if not a_event.facts.amounts or not h_event.facts.amounts:
        return False
    if _amount_text_quality_low(a_event.normalized_text) or _amount_text_quality_low(h_event.normalized_text):
        return False

    a_mentions = [item for item in _amount_mentions(a_event.normalized_text) if item.reliable]
    h_mentions = [item for item in _amount_mentions(h_event.normalized_text) if item.reliable]
    if not a_mentions or not h_mentions:
        return False

    shared_roles = {item.role for item in a_mentions} & {item.role for item in h_mentions}
    shared_roles.discard("unknown")
    shared_roles.discard("amount")
    if not shared_roles:
        return False

    conflicting_roles = []
    for role in shared_roles:
        a_values = sorted({item.value for item in a_mentions if item.role == role})
        h_values = sorted({item.value for item in h_mentions if item.role == role})
        if a_values and h_values and not _role_numeric_overlap(role, a_values, h_values):
            conflicting_roles.append((role, a_values, h_values))
    if not conflicting_roles:
        return False

    if not all(role in _REAL_AMOUNT_MISMATCH_ROLES for role, _, _ in conflicting_roles):
        return False

    if _amount_magnitude_mismatch(a_event, h_event):
        return False

    if _is_structured_fact_pair(a_event, h_event):
        for role, a_values, h_values in conflicting_roles:
            if role in _REAL_AMOUNT_MISMATCH_ROLES and len(a_values) == 1 and len(h_values) == 1:
                return True

    if _identity_anchor_count(a_event, h_event) < 2:
        return False

    for role, a_values, h_values in conflicting_roles:
        if len(a_values) == 1 and len(h_values) == 1:
            return True
    return False


def _percentage_mismatch_is_real(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if not a_event.facts.percentages or not h_event.facts.percentages:
        return False
    if _percentage_text_quality_low(a_event.normalized_text) or _percentage_text_quality_low(h_event.normalized_text):
        return False

    shared_roles = set(a_event.facts.percentage_roles) & set(h_event.facts.percentage_roles)
    shared_roles.discard("unknown")
    shared_roles.discard("ratio")
    if not shared_roles:
        return False

    conflicting_roles = []
    for role in shared_roles:
        a_values = sorted(a_event.facts.percentage_roles.get(role, set()))
        h_values = sorted(h_event.facts.percentage_roles.get(role, set()))
        if a_values and h_values and not _numeric_overlap(a_values, h_values):
            conflicting_roles.append((role, a_values, h_values))
    if not conflicting_roles:
        return False

    if not all(role in _REAL_PERCENTAGE_MISMATCH_ROLES for role, _, _ in conflicting_roles):
        return False

    if not _has_percentage_identity_anchor(a_event, h_event):
        return False

    return any(len(a_values) == 1 and len(h_values) == 1 for _, a_values, h_values in conflicting_roles)


def _percentage_text_quality_low(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text or "").lower()
    if not compact:
        return True
    if "cash and bank balances" in compact or "bank balances" in compact:
        return True
    if "notes to the consolidated financial statements" in compact and len(_percentage_mentions(text)) >= 3:
        return True
    if len(_percentage_mentions(text)) >= 5:
        return True
    return False


def _has_percentage_identity_anchor(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    # 结构化事项的比例冲突需要身份锚点：签名兼容、硬锚点、或同 domain 同动作
    if _event_identity_compatible(a_event, h_event):
        return True
    if _has_hard_fact_anchor(a_event, h_event):
        return True
    # 同 structured domain 且共享真实比例角色，视为同一类事项锚点
    shared_pct_roles = set(a_event.facts.percentage_roles) & set(h_event.facts.percentage_roles)
    if (shared_pct_roles & _REAL_PERCENTAGE_MISMATCH_ROLES) and (a_event.facts.domains & h_event.facts.domains):
        return True
    count = 0
    count += 1 if _specific_dates(a_event.facts.dates) & _specific_dates(h_event.facts.dates) else 0
    count += 1 if _numeric_overlap(a_event.facts.amounts, h_event.facts.amounts) else 0
    count += 1 if a_event.specific_entities & h_event.specific_entities else 0
    count += 1 if a_event.facts.domains & h_event.facts.domains else 0
    count += 1 if a_event.action_terms & h_event.action_terms else 0
    return count >= 2


def _amount_text_quality_low(text: str) -> bool:
    raw = (text or "").lower()
    compact = re.sub(r"\s+", "", text or "").lower()
    if not compact:
        return True
    mentions = _amount_mentions(text)
    if any(item.reliable and item.role in _REAL_AMOUNT_MISMATCH_ROLES for item in mentions):
        return False
    if re.search(r"(?<!\d)[1-9]\d{0,2}(?:\.\d+)?\s*年", raw):
        return True
    if re.search(r"20\d{2}\d{1,2}(?:a股)?年月", compact):
        return True
    if re.search(r"\d{1,3}(?:,\d{3})+20\d{2}\d{1,2}万股", compact):
        return True
    if mentions and not any(item.reliable for item in mentions):
        return True
    number_count = len(re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text or ""))
    context_hits = sum(1 for term in _AMOUNT_CONTEXT_TERMS if term in compact)
    return number_count >= 10 and context_hits <= 1 and len(compact) > 120


def _date_mismatch_is_real(a_event: EventCandidate, h_event: EventCandidate) -> bool:
    if _near_duplicate_event_text(a_event, h_event):
        return False
    if _looks_like_board_composition(a_event.normalized_text) or _looks_like_board_composition(h_event.normalized_text):
        return False
    return _date_values_conflict(a_event, h_event) and _identity_anchor_count(a_event, h_event) >= 2


def _has_structured_identity_anchor(
    a_event: EventCandidate,
    h_event: EventCandidate,
    mismatches: list[str],
) -> bool:
    """结构化 domain 的同一角色金额/比例冲突，在数量级合理时可视为身份锚点。

    例如同一债券的发行金额、票面利率，或同一诉讼的涉案金额。若 A/H 值
    数量级相差过大（如 2 亿 vs 111.67 亿股），则更可能是不同事项。
    """
    if _LABEL_AMOUNT in mismatches:
        shared_roles = _shared_amount_roles(a_event, h_event)
        real_roles = shared_roles & _REAL_AMOUNT_MISMATCH_ROLES
        if not real_roles:
            return False
        for role in real_roles:
            a_values = sorted(a_event.facts.amount_roles.get(role, set()))
            h_values = sorted(h_event.facts.amount_roles.get(role, set()))
            if not a_values or not h_values:
                continue
            a_val = a_values[0]
            h_val = h_values[0]
            base = max(abs(a_val), abs(h_val), 1.0)
            ratio = min(abs(a_val), abs(h_val)) / base
            if role in {"share_count", "dividend_base_share_count"}:
                if ratio >= 0.1:
                    return True
            else:
                if ratio >= 0.05:
                    return True
    if _LABEL_PERCENTAGE in mismatches:
        shared_roles = set(a_event.facts.percentage_roles) & set(h_event.facts.percentage_roles)
        real_roles = shared_roles & _REAL_PERCENTAGE_MISMATCH_ROLES
        if real_roles:
            return True
    return False


def _has_real_diff_identity_anchor(a_event: EventCandidate, h_event: EventCandidate, mismatches: list[str]) -> bool:
    """真实差异必须至少有一个硬事实锚点，避免同主题不同事项被误判。

    硬锚点包括：非通用日期重合、金额数值重合、特定交易对手/实体重合，
    或在结构化 domain 中同一角色金额/比例冲突且数量级合理。
    """
    if _has_hard_fact_anchor(a_event, h_event):
        return True
    if _has_structured_identity_anchor(a_event, h_event, mismatches):
        return True
    # 兜底：只有金额/比例冲突且文本高度相似时才可能成立
    if {_LABEL_AMOUNT, _LABEL_PERCENTAGE} & set(mismatches):
        token_score = _jaccard(a_event.tokens, h_event.tokens)
        text_score = SequenceMatcher(None, a_event.normalized_text[:500], h_event.normalized_text[:500]).ratio()
        return bool(a_event.action_terms & h_event.action_terms) and token_score >= 0.30 and text_score >= 0.80
    return False


def _status_mismatch_is_real(a_event: EventCandidate, h_event: EventCandidate, mismatches: list[str]) -> bool:
    if set(mismatches) != {_LABEL_STATUS}:
        return True
    return False


def _identity_anchor_count(a_event: EventCandidate, h_event: EventCandidate) -> int:
    """身份锚点只计硬事实，不计 domain/action_terms 等泛主题信号。"""
    count = 0
    count += 1 if _specific_dates(a_event.facts.dates) & _specific_dates(h_event.facts.dates) else 0
    count += 1 if _numeric_overlap(a_event.facts.amounts, h_event.facts.amounts) else 0
    count += 1 if a_event.specific_entities & h_event.specific_entities else 0
    return count


def _specific_entity_overlap(a_entities: set[str], h_entities: set[str]) -> set[str]:
    return _specific_entities(a_entities) & _specific_entities(h_entities)


def _specific_entities(entities: set[str]) -> set[str]:
    result: set[str] = set()
    for entity in entities:
        cleaned = re.sub(r"[\s，。、；：]+", "", entity)
        if cleaned and cleaned not in _GENERIC_ENTITIES and len(cleaned) >= 4:
            result.add(cleaned)
    return result


def _event_coverage(
    a_event: EventCandidate | None,
    h_event: EventCandidate | None,
    confidence: float,
    mismatches: list[str],
    real_mismatch: bool = False,
) -> DisclosureCoverageItem:
    status = "matched" if a_event and h_event else ("a_only" if a_event else "h_only")
    topic = _event_topic(a_event, h_event)
    note = "事件双边披露，事实一致"
    if mismatches and real_mismatch:
        note = "事件双边披露，存在事实差异：" + "、".join(mismatches)
    elif mismatches:
        note = "事件双边披露，匹配事实需复核：" + "、".join(mismatches)
    elif status == "a_only":
        note = "事件仅在 A 股报告中匹配到"
    elif status == "h_only":
        note = "事件仅在 H 股报告中匹配到"

    return DisclosureCoverageItem(
        coverage_id=_coverage_id(
            "event",
            status,
            str(a_event.page if a_event else ""),
            str(h_event.page if h_event else ""),
            topic.best(),
            (a_event.normalized_text[:60] if a_event else "") + (h_event.normalized_text[:60] if h_event else ""),
        ),
        category="event",
        status=status,
        topic=topic,
        canonical_key="event",
        a_pages=[a_event.page] if a_event else [],
        h_pages=[h_event.page] if h_event else [],
        a_evidence=[a_event.evidence] if a_event else [],
        h_evidence=[h_event.evidence] if h_event else [],
        match_confidence=round(confidence, 3),
        note=note,
        source="event_coverage",
    )


def _event_diff(
    a_event: EventCandidate,
    h_event: EventCandidate,
    confidence: float,
    mismatches: list[str],
) -> Diff:
    topic = _event_topic(a_event, h_event)
    mismatch_text = "、".join(mismatches)
    explanation = _event_explanation(a_event, h_event, confidence, mismatches)
    return Diff(
        diff_id=_coverage_id("event-diff", str(a_event.page), str(h_event.page), topic.best())[:12],
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_mismatch",
        topic=topic,
        summary=LocalizedString(
            zh=f"事件事实不一致：{topic.best()}（差异字段：{mismatch_text}，匹配置信度 {confidence:.2f}）",
            en=f"Event fact mismatch: {topic.best()} ({mismatch_text}, confidence {confidence:.2f})",
        ),
        evidence=[a_event.evidence, h_event.evidence],
        diff_explanation=explanation,
        rule_id="event_fact_match",
    )


def _event_explanation(
    a_event: EventCandidate,
    h_event: EventCandidate,
    confidence: float,
    mismatches: list[str],
) -> DiffExplanation:
    topic = _event_topic(a_event, h_event)
    items = _event_explanation_items(a_event, h_event, mismatches)
    first_role = items[0].role if items else None
    first_label = items[0].label if items else (mismatches[0] if mismatches else "披露事实")
    # 避免债券/担保等事项被错误贴上“关联交易”标签
    default_headline = f"{topic.best()}{first_label}不一致"
    headline = _FACT_ROLE_HEADLINES.get(first_role or "", default_headline)
    if first_role == "transaction_amount" and "关联" not in topic.best():
        headline = f"{topic.best()}交易金额不一致"
    issue = _event_issue(items, topic, mismatches, confidence)
    review_hint = _FACT_ROLE_REVIEW_HINTS.get(
        first_role or "",
        "优先核对同一主题、同一报告期下 A/H 年报的披露动作、取值、状态和原文证据。",
    )
    return DiffExplanation(
        headline=headline,
        issue=issue,
        location=format_location([a_event.evidence, h_event.evidence]),
        items=items,
        review_hint=review_hint,
    )


def _event_explanation_items(
    a_event: EventCandidate,
    h_event: EventCandidate,
    mismatches: list[str],
) -> list:
    items = []
    if _LABEL_AMOUNT in mismatches:
        for role in _ordered_roles(_shared_amount_roles(a_event, h_event)):
            a_values = sorted(a_event.facts.amount_roles.get(role, set()))
            h_values = sorted(h_event.facts.amount_roles.get(role, set()))
            if not a_values or not h_values:
                continue
            if _role_numeric_overlap(role, a_values, h_values):
                continue
            a_value = a_values[0]
            h_value = h_values[0]
            items.append(
                explanation_item(
                    label=_FACT_ROLE_LABELS.get(role, "金额/数量"),
                    role=role,
                    a_value=a_value,
                    h_value=h_value,
                    delta=abs(float(a_value) - float(h_value)),
                    a_evidence=a_event.evidence,
                    h_evidence=h_event.evidence,
                    a_snippet=_fact_snippet(a_event, "amount", role, a_value),
                    h_snippet=_fact_snippet(h_event, "amount", role, h_value),
                )
            )
    if _LABEL_DATE in mismatches and _date_values_conflict(a_event, h_event):
        items.append(
            explanation_item(
                label="日期",
                role="date",
                a_value=_joined_values(a_event.facts.dates),
                h_value=_joined_values(h_event.facts.dates),
                a_evidence=a_event.evidence,
                h_evidence=h_event.evidence,
                a_snippet=_fact_snippet(a_event, "date", "date"),
                h_snippet=_fact_snippet(h_event, "date", "date"),
            )
        )
    if _LABEL_STATUS in mismatches and a_event.facts.statuses and h_event.facts.statuses:
        items.append(
            explanation_item(
                label="状态",
                role="status",
                a_value=_joined_values(a_event.facts.statuses),
                h_value=_joined_values(h_event.facts.statuses),
                a_evidence=a_event.evidence,
                h_evidence=h_event.evidence,
                a_snippet=_fact_snippet(a_event, "status", "status"),
                h_snippet=_fact_snippet(h_event, "status", "status"),
            )
        )
    if _LABEL_PERCENTAGE in mismatches and a_event.facts.percentages and h_event.facts.percentages:
        shared_roles = set(a_event.facts.percentage_roles) & set(h_event.facts.percentage_roles)
        shared_roles.discard("unknown")
        shared_roles.discard("ratio")
        for role in _ordered_roles(shared_roles):
            a_values = sorted(a_event.facts.percentage_roles.get(role, set()))
            h_values = sorted(h_event.facts.percentage_roles.get(role, set()))
            if not a_values or not h_values or _numeric_overlap(a_values, h_values):
                continue
            items.append(
                explanation_item(
                    label=_FACT_ROLE_LABELS.get(role, "比例"),
                    role=role,
                    a_value=_joined_values(a_values),
                    h_value=_joined_values(h_values),
                    a_evidence=a_event.evidence,
                    h_evidence=h_event.evidence,
                    a_snippet=_fact_snippet(a_event, "percentage", role, a_values[0]),
                    h_snippet=_fact_snippet(h_event, "percentage", role, h_values[0]),
                )
            )
    return items


def _event_issue(
    items: list,
    topic: LocalizedString,
    mismatches: list[str],
    confidence: float,
) -> str:
    if not items:
        fields = "、".join(mismatches) if mismatches else "披露事实"
        return f"A/H 在{topic.best()}的{fields}披露不一致，匹配置信度 {confidence:.2f}。"
    parts = []
    for item in items[:3]:
        parts.append(
            f"A 披露{item.label} {format_explanation_value(item.a_value)}；"
            f"H 披露{item.label} {format_explanation_value(item.h_value)}"
        )
    return "；".join(parts)


def _ordered_roles(roles: Iterable[str]) -> list[str]:
    order = {role: index for index, role in enumerate(_FACT_ROLE_ORDER)}
    return sorted(roles, key=lambda role: (order.get(role, len(order)), role))


def _joined_values(values: Iterable) -> str:
    return "、".join(str(value) for value in sorted(values))


def _fact_snippet(
    event: EventCandidate,
    fact_type: str,
    role: str,
    value: float | str | None = None,
) -> str:
    for item in event.facts.fact_items:
        if item.fact_type != fact_type or item.role != role:
            continue
        if value is not None and fact_type == "amount":
            try:
                if float(item.value) != float(value):
                    continue
            except (TypeError, ValueError):
                continue
        return _short_snippet(item.context)
    return _short_snippet(event.evidence.snippet or event.text)


def _short_snippet(text: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _event_topic(a_event: EventCandidate | None, h_event: EventCandidate | None) -> LocalizedString:
    event = a_event or h_event
    if event is None:
        return LocalizedString(zh="事件披露", en="Event disclosure")
    # 优先使用 section_bucket 对应的已知主题，避免 domain 关键词漂移
    section_domains = {
        "bond_events": "bond",
        "related_party": "related_party",
        "share_changes": "share_change",
        "dividend_distribution": "dividend",
        "guarantee_commitment": "guarantee",
        "litigation": "litigation",
    }
    bucket = (a_event.section_bucket if a_event else None) or (h_event.section_bucket if h_event else None)
    if bucket in section_domains:
        zh, en = _DOMAIN_TOPIC_LABELS[section_domains[bucket]]
        return LocalizedString(zh=zh, en=en)
    domains = set()
    if a_event:
        domains |= a_event.facts.domains
    if h_event:
        domains |= h_event.facts.domains
    for domain in _DOMAIN_TOPIC_ORDER:
        if domain in domains:
            zh, en = _DOMAIN_TOPIC_LABELS[domain]
            return LocalizedString(zh=zh, en=en)
    if event.topic_label:
        return LocalizedString(zh=event.topic_label, en=event.topic_key)
    entities = sorted(event.specific_entities or event.facts.entities)
    label = entities[0] if entities else event.topic_label
    return LocalizedString(zh=label, en=event.topic_key)


def _dedupe_coverage(items: list[DisclosureCoverageItem]) -> list[DisclosureCoverageItem]:
    seen: set[str] = set()
    result: list[DisclosureCoverageItem] = []
    for item in items:
        if item.coverage_id in seen:
            continue
        seen.add(item.coverage_id)
        result.append(item)
    return result


def _coverage_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"cov-{digest}"
