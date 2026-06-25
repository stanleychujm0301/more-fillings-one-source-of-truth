"""Profile comparison for A/H annual-report portraits.

The comparator is intentionally deterministic:
- metrics are compared after unit/currency/tolerance normalization;
- narrative topics use the stable taxonomy from ``topic_map``;
- structure comparison works on the recursive chapter tree;
- every finding is triaged as real / expected / unresolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ahcc.align.glossary import glossary
from ahcc.profile.expected_diffs import is_expected_metric, is_expected_topic
from ahcc.profile.models import ChapterNode, MetricItem, MetricOccurrences, NarrativeBlock, ProfileDiff, ReportProfile
from ahcc.schemas import Currency, DiffSeverity, Evidence, LocalizedString, ReportSide


_CORE_KEYS = {
    "total_assets",
    "total_liabilities",
    "equity",
    "total_equity",
    "revenue",
    "net_profit",
    "total_profit",
    "operating_profit",
    "cash_equivalents",
    "operating_cash_flow",
    "eps_basic",
    "eps_diluted",
    "basic_eps",
    "diluted_eps",
}

_UNIT_MULTIPLIERS: dict[str, float] = {
    "元": 1.0,
    "人民币元": 1.0,
    "RMB 元": 1.0,
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

_SECTION_ALIASES = {
    "governance": "corporate_governance",
    "segment": "segment_report",
    "basic_eps": "eps",
    "diluted_eps": "eps",
}


def _is_garbled_key(key: str) -> bool:
    if not key or len(key) < 2:
        return True
    readable = sum(1 for c in key if re.match(r"[\w一-鿿]", c))
    return len(key) > 10 and readable / len(key) < 0.5


def _is_garbled_text(text: str) -> bool:
    if not text:
        return True
    if text.count("�") > len(text) * 0.1:
        return True
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    alpha = sum(1 for c in text if c.isalpha() and c.isascii())
    total = len(text.strip())
    return total > 20 and chinese / total < 0.05 and alpha / total < 0.3


def _is_junk_topic_label(label: str) -> bool:
    if not label or len(label) < 2:
        return True
    junk_patterns = ("不适用", "不適用", "号填列", "號填列")
    if any(p in label for p in junk_patterns):
        return True
    has_punct = any(c in label for c in "，。、；：！？,.;:!?·—–（）()[]【】")
    if len(label) > 18 and not has_punct:
        return True
    return False


def _flatten_evidence(values: Iterable[Evidence | list[Evidence] | None]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            evidence.extend(ev for ev in value if isinstance(ev, Evidence))
        elif isinstance(value, Evidence):
            evidence.append(value)
    return evidence


def _metric_items(metrics: Iterable[MetricOccurrences | MetricItem]) -> list[MetricItem]:
    items: list[MetricItem] = []
    glossary_keys = glossary.all_canonical_keys()
    for occ in metrics:
        if isinstance(occ, MetricOccurrences):
            candidate = occ.primary
        elif isinstance(occ, MetricItem):
            candidate = occ
        else:
            continue
        if candidate.value is None:
            continue
        if _is_garbled_key(candidate.canonical_key):
            continue
        # Keep comparison noise under control: all data is retained in the profile,
        # but A/H comparison only trusts glossary/core keys or high-confidence table rows.
        if candidate.canonical_key not in glossary_keys and candidate.canonical_key not in _CORE_KEYS:
            if candidate.confidence < 0.9 or candidate.source != "table":
                continue
        items.append(candidate)
    return items


def _best_by_key(items: Iterable[MetricItem]) -> dict[str, MetricItem]:
    by_key: dict[str, MetricItem] = {}
    for item in items:
        current = by_key.get(item.canonical_key)
        if current is None:
            by_key[item.canonical_key] = item
            continue
        current_score = (current.confidence, 1 if current.source == "table" else 0, abs(current.value or 0))
        new_score = (item.confidence, 1 if item.source == "table" else 0, abs(item.value or 0))
        if new_score > current_score:
            by_key[item.canonical_key] = item
    return by_key


def _unit_multiplier(unit: str | None) -> float:
    if not unit:
        return 1.0
    if unit in _UNIT_MULTIPLIERS:
        return _UNIT_MULTIPLIERS[unit]
    lower = unit.lower()
    if "million" in lower or "百万元" in unit or "百萬元" in unit:
        return 1_000_000.0
    if "thousand" in lower or "千元" in unit:
        return 1_000.0
    if "亿元" in unit or "億元" in unit or "亿" in unit or "億" in unit:
        return 100_000_000.0
    if "万元" in unit or "萬元" in unit or "万" in unit or "萬" in unit:
        return 10_000.0
    return 1.0


def _normalized_value(item: MetricItem) -> float | None:
    if item.value is None:
        return None
    return item.value * _unit_multiplier(item.unit)


def _currencies_compatible(a_currency: Currency | None, h_currency: Currency | None) -> bool:
    return not a_currency or not h_currency or a_currency == h_currency


def _within_tolerance(a_value: float, h_value: float, canonical_key: str) -> bool:
    delta = abs(a_value - h_value)
    base = max(abs(a_value), abs(h_value), 1.0)
    if canonical_key.startswith("eps") or canonical_key in {"basic_eps", "diluted_eps"}:
        return delta <= max(0.01, base * 0.005)
    return delta <= max(1.0, base * 0.01)


def _triage(expected: bool, unresolved: bool = False) -> str:
    if expected:
        return "expected"
    if unresolved:
        return "unresolved"
    return "real"


def _severity_for_triage(triage: str, default: DiffSeverity) -> DiffSeverity:
    if triage == "expected":
        return DiffSeverity.INFO
    if triage == "unresolved" and default in {DiffSeverity.HIGH, DiffSeverity.CRITICAL}:
        return DiffSeverity.MEDIUM
    return default


def compare_metrics(profile_a: ReportProfile, profile_h: ReportProfile) -> list[ProfileDiff]:
    """Compare numeric facts by canonical key after normalization."""
    diffs: list[ProfileDiff] = []
    a_by_key = _best_by_key(_metric_items(profile_a.metrics))
    h_by_key = _best_by_key(_metric_items(profile_h.metrics))

    for key in sorted(set(a_by_key) | set(h_by_key)):
        a_item = a_by_key.get(key)
        h_item = h_by_key.get(key)

        if a_item and h_item:
            a_norm = _normalized_value(a_item)
            h_norm = _normalized_value(h_item)
            if a_norm is None or h_norm is None:
                continue

            currency_unresolved = not _currencies_compatible(a_item.currency, h_item.currency)
            if not currency_unresolved and _within_tolerance(a_norm, h_norm, key):
                continue

            triage = _triage(False, unresolved=currency_unresolved)
            rationale = None
            if currency_unresolved:
                rationale = "A/H币种不同，未配置汇率折算，需人工确认折算口径"

            delta = abs(a_norm - h_norm)
            ratio = delta / max(abs(a_norm), abs(h_norm), 1.0)
            severity = _severity_for_triage(triage, _grade_metric_diff(ratio))
            diffs.append(
                ProfileDiff(
                    diff_type="metric_mismatch",
                    severity=severity,
                    triage=triage,
                    topic=LocalizedString(zh=a_item.name.zh or key, en=a_item.name.en or h_item.name.en or key),
                    summary=LocalizedString(
                        zh=f"{a_item.name.zh or key}: A股={a_norm:,.2f}, H股={h_norm:,.2f}, 差异={delta:,.2f}",
                        en=f"{a_item.name.en or key}: A={a_norm:,.2f}, H={h_norm:,.2f}, delta={delta:,.2f}",
                    ),
                    canonical_key=key,
                    a_value=a_norm,
                    h_value=h_norm,
                    a_pages=[a_item.page],
                    h_pages=[h_item.page],
                    evidence=[a_item.evidence, h_item.evidence],
                    expected=False,
                    rationale=rationale,
                    source="profile.metrics",
                )
            )
            continue

        present = a_item or h_item
        if not present:
            continue
        missing_side = ReportSide.H_SHARE if a_item else ReportSide.A_SHARE
        expected, rationale = is_expected_metric(key, missing_side)
        unresolved = not expected and key not in _CORE_KEYS
        triage = _triage(expected, unresolved=unresolved)
        present_side_label = "A股" if a_item else "H股"
        missing_side_label = "H股" if a_item else "A股"
        summary_zh = f"{present.name.zh or key}: {present_side_label}披露，{missing_side_label}未找到"
        summary_en = f"{present.name.en or key}: disclosed in {present_side_label}, not found in {missing_side_label}"
        if rationale:
            summary_zh += f"（{rationale}）"
            summary_en += f" ({rationale})"
        elif unresolved:
            summary_zh += "（非核心指标，需复核是否为抽取或口径差异）"
            summary_en += " (non-core metric, extraction or scope difference needs review)"

        diffs.append(
            ProfileDiff(
                diff_type="metric_missing",
                severity=_severity_for_triage(triage, DiffSeverity.MEDIUM),
                triage=triage,
                topic=LocalizedString(zh=present.name.zh or key, en=present.name.en or key),
                summary=LocalizedString(zh=summary_zh, en=summary_en),
                canonical_key=key,
                a_value=_normalized_value(a_item) if a_item else None,
                h_value=_normalized_value(h_item) if h_item else None,
                a_pages=[a_item.page] if a_item else [],
                h_pages=[h_item.page] if h_item else [],
                evidence=[present.evidence],
                expected=expected,
                rationale=rationale or ("待人工确认是否为真实缺失" if unresolved else None),
                source="profile.metrics",
            )
        )

    return diffs


def _grade_metric_diff(ratio: float) -> DiffSeverity:
    if ratio < 0.01:
        return DiffSeverity.LOW
    if ratio < 0.05:
        return DiffSeverity.MEDIUM
    if ratio < 0.20:
        return DiffSeverity.HIGH
    return DiffSeverity.CRITICAL


@dataclass
class _NarrativeAggregate:
    topic_key: str
    topic_label: str
    word_count: int = 0
    pages: set[int] = field(default_factory=set)
    evidence: list[Evidence] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)


def _aggregate_narratives(blocks: Iterable[NarrativeBlock]) -> dict[str, _NarrativeAggregate]:
    aggregated: dict[str, _NarrativeAggregate] = {}
    for block in blocks:
        if _is_garbled_text(block.topic_label) or _is_junk_topic_label(block.topic_label):
            continue
        key = block.topic_key or "uncategorized"
        agg = aggregated.setdefault(key, _NarrativeAggregate(topic_key=key, topic_label=block.topic_label))
        agg.word_count += block.word_count
        if block.page_range != (0, 0):
            agg.pages.update(range(block.page_range[0], block.page_range[1] + 1))
        agg.evidence.extend(block.evidence[:2])
        agg.keywords.update(block.keywords[:20])
    return aggregated


def compare_narratives(profile_a: ReportProfile, profile_h: ReportProfile) -> list[ProfileDiff]:
    """Compare stable narrative topics and depth."""
    diffs: list[ProfileDiff] = []
    a_topics = _aggregate_narratives(profile_a.narratives)
    h_topics = _aggregate_narratives(profile_h.narratives)

    for topic_key in sorted(set(a_topics) | set(h_topics)):
        a_block = a_topics.get(topic_key)
        h_block = h_topics.get(topic_key)

        if a_block and h_block:
            if min(a_block.word_count, h_block.word_count) <= 0:
                continue
            ratio = max(a_block.word_count, h_block.word_count) / min(a_block.word_count, h_block.word_count)
            if ratio <= 1.8:
                continue
            less_side = ReportSide.A_SHARE if a_block.word_count < h_block.word_count else ReportSide.H_SHARE
            expected, rationale = is_expected_topic(a_block.topic_label, less_side, topic_key=topic_key)
            unresolved = not expected and ratio <= 3.0
            triage = _triage(expected, unresolved=unresolved)
            more_side_label = "H股" if h_block.word_count > a_block.word_count else "A股"
            less_side_label = "A股" if h_block.word_count > a_block.word_count else "H股"
            summary_zh = f"{a_block.topic_label}: {more_side_label}比{less_side_label}多披露{ratio:.1f}倍内容"
            if rationale:
                summary_zh += f"（{rationale}）"
            elif unresolved:
                summary_zh += "（详略差异需人工确认是否构成真实披露差异）"
            diffs.append(
                ProfileDiff(
                    diff_type="narrative_depth",
                    severity=_severity_for_triage(triage, DiffSeverity.LOW),
                    triage=triage,
                    topic=LocalizedString(zh=a_block.topic_label, en=h_block.topic_label),
                    summary=LocalizedString(zh=summary_zh, en=summary_zh),
                    topic_label=a_block.topic_label,
                    a_word_count=a_block.word_count,
                    h_word_count=h_block.word_count,
                    a_pages=sorted(a_block.pages),
                    h_pages=sorted(h_block.pages),
                    evidence=_flatten_evidence([a_block.evidence[:3], h_block.evidence[:3]]),
                    expected=expected,
                    rationale=rationale or ("待人工确认" if unresolved else None),
                    source="profile.narratives",
                )
            )
            continue

        present = a_block or h_block
        if not present:
            continue
        missing_side = ReportSide.H_SHARE if a_block else ReportSide.A_SHARE
        expected, rationale = is_expected_topic(present.topic_label, missing_side, topic_key=topic_key)
        unresolved = not expected and topic_key == "uncategorized"
        triage = _triage(expected, unresolved=unresolved)
        present_side_label = "A股" if a_block else "H股"
        missing_side_label = "H股" if a_block else "A股"
        summary_zh = f"{present.topic_label}: {present_side_label}披露，{missing_side_label}未找到"
        if rationale:
            summary_zh += f"（{rationale}）"
        elif unresolved:
            summary_zh += "（未分类段落，需人工确认主题归属）"

        diffs.append(
            ProfileDiff(
                diff_type="topic_missing",
                severity=_severity_for_triage(triage, DiffSeverity.LOW),
                triage=triage,
                topic=LocalizedString(zh=present.topic_label),
                summary=LocalizedString(zh=summary_zh, en=summary_zh),
                topic_label=present.topic_label,
                a_word_count=a_block.word_count if a_block else None,
                h_word_count=h_block.word_count if h_block else None,
                a_pages=sorted(a_block.pages) if a_block else [],
                h_pages=sorted(h_block.pages) if h_block else [],
                evidence=present.evidence[:3],
                expected=expected,
                rationale=rationale or ("待人工确认" if unresolved else None),
                source="profile.narratives",
            )
        )

    return diffs


def _flatten_structure(nodes: Iterable[ChapterNode]) -> dict[str, ChapterNode]:
    flattened: dict[str, ChapterNode] = {}
    for node in nodes:
        if node.section_code:
            code = _normalize_section_code(node.section_code)
            if code not in flattened or node.level > flattened[code].level:
                flattened[code] = node
        flattened.update(_flatten_structure(node.children))
    return flattened


def _normalize_section_code(section_code: str) -> str:
    return _SECTION_ALIASES.get(section_code, section_code)


def _section_evidence(profile: ReportProfile, node: ChapterNode) -> Evidence:
    side = profile.side
    source_doc = getattr(profile, "source_doc", None)
    section_code = _normalize_section_code(node.section_code or "")
    if source_doc is not None:
        for seg in getattr(source_doc, "texts", []):
            if _normalize_section_code(seg.section or "") == section_code:
                return Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=seg.text[:200],
                    section=seg.section,
                )
    return Evidence(
        side=side,
        page=max(node.page_start, 1),
        bbox=None,
        snippet=node.title.best() or section_code,
        section=section_code,
    )


def compare_structures(profile_a: ReportProfile, profile_h: ReportProfile) -> list[ProfileDiff]:
    """Compare recursive chapter trees after section-code normalization."""
    diffs: list[ProfileDiff] = []
    a_nodes = _flatten_structure(profile_a.structure)
    h_nodes = _flatten_structure(profile_h.structure)
    ignored_roots = {"company_overview", "governance_and_shareholders", "financial_statements_tree", "esg_and_social", "other_disclosures"}

    for code in sorted((set(a_nodes) | set(h_nodes)) - ignored_roots):
        a_node = a_nodes.get(code)
        h_node = h_nodes.get(code)
        if a_node and h_node:
            continue

        present_node = a_node or h_node
        if present_node is None:
            continue
        missing_side = ReportSide.H_SHARE if a_node else ReportSide.A_SHARE
        expected, rationale = is_expected_topic(present_node.title.best(), missing_side, topic_key=code)
        unresolved = not expected and code not in {"bs", "pl", "cf", "equity", "notes", "mda", "financial_statements"}
        triage = _triage(expected, unresolved=unresolved)
        present_side_label = "A股" if a_node else "H股"
        missing_side_label = "H股" if a_node else "A股"
        summary_zh = f"章节 {present_node.title.best() or code}: {present_side_label}存在，{missing_side_label}未找到对应章节"
        if rationale:
            summary_zh += f"（{rationale}）"
        elif unresolved:
            summary_zh += "（需复核章节映射或披露位置）"
        evidence = _section_evidence(profile_a if a_node else profile_h, present_node)
        diffs.append(
            ProfileDiff(
                diff_type="structure_missing",
                severity=_severity_for_triage(triage, DiffSeverity.LOW),
                triage=triage,
                topic=present_node.title,
                summary=LocalizedString(zh=summary_zh, en=summary_zh),
                structure_code=code,
                a_pages=[a_node.page_start] if a_node else [],
                h_pages=[h_node.page_start] if h_node else [],
                evidence=[evidence],
                expected=expected,
                rationale=rationale or ("待人工确认" if unresolved else None),
                source="profile.structure",
            )
        )

    return diffs


def compare_profiles(profile_a: ReportProfile, profile_h: ReportProfile) -> list[ProfileDiff]:
    """Compare A-share and H-share profiles across metrics, narratives, and structure."""
    diffs: list[ProfileDiff] = []
    diffs.extend(compare_metrics(profile_a, profile_h))
    diffs.extend(compare_narratives(profile_a, profile_h))
    diffs.extend(compare_structures(profile_a, profile_h))
    diffs.extend(check_internal_consistency(profile_a))
    diffs.extend(check_internal_consistency(profile_h))
    return diffs


def check_internal_consistency(profile: ReportProfile) -> list[ProfileDiff]:
    """Report internal inconsistencies within a single annual report."""
    diffs: list[ProfileDiff] = []
    for occ in profile.metrics:
        if not isinstance(occ, MetricOccurrences) or occ.is_internally_consistent:
            continue
        for inc in occ.internal_inconsistencies:
            severity = _grade_internal_diff(inc.delta_pct)
            section_a = inc.item_a.evidence.section or f"第{inc.item_a.page}页"
            section_b = inc.item_b.evidence.section or f"第{inc.item_b.page}页"
            pages = sorted({inc.item_a.page, inc.item_b.page})
            diffs.append(
                ProfileDiff(
                    diff_type="internal_inconsistency",
                    severity=severity,
                    triage="real",
                    topic=occ.name,
                    summary=LocalizedString(
                        zh=f"{occ.name.zh or occ.canonical_key}: {section_a}为 {inc.item_a.value:,.2f}，{section_b}为 {inc.item_b.value:,.2f}，差异 {inc.delta_pct:.1f}%",
                        en=f"{occ.name.en or occ.canonical_key}: {inc.item_a.value:,.2f} ({section_a}) vs {inc.item_b.value:,.2f} ({section_b}), diff {inc.delta_pct:.1f}%",
                    ),
                    canonical_key=occ.canonical_key,
                    a_pages=pages if profile.side == ReportSide.A_SHARE else [],
                    h_pages=pages if profile.side == ReportSide.H_SHARE else [],
                    a_value=inc.item_a.value if profile.side == ReportSide.A_SHARE else None,
                    h_value=inc.item_a.value if profile.side == ReportSide.H_SHARE else None,
                    evidence=[inc.item_a.evidence, inc.item_b.evidence],
                    source="profile.internal",
                )
            )
    return diffs


def _grade_internal_diff(delta_pct: float) -> DiffSeverity:
    if delta_pct < 1:
        return DiffSeverity.INFO
    if delta_pct < 5:
        return DiffSeverity.LOW
    if delta_pct < 20:
        return DiffSeverity.MEDIUM
    return DiffSeverity.HIGH
