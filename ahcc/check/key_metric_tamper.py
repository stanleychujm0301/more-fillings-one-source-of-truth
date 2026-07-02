"""Targeted key-metric exact and visual-layer tamper checks."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from loguru import logger

from ahcc.config import settings
from ahcc.llm.client import cached_call
from ahcc.profile.models import MetricItem, MetricOccurrences, ReportProfile
from ahcc.schemas import Diff, DiffScope, DiffSeverity, DiffType, Evidence, LocalizedString, ReportSide


KEY_METRIC_TAMPER_KEYS = {
    "revenue",
    "total_profit",
    "profit_before_tax",
    "net_profit",
    "net_profit_attributable",
    "net_profit_attributable_to_parent",
    "operating_profit",
    "eps_basic",
    "eps_diluted",
    "net_asset_per_share",
    "operating_cash_per_share",
    "weighted_average_roe",
    "fully_diluted_roe",
    "average_total_asset_return",
    "net_interest_spread",
    "net_interest_margin",
    "cost_to_income_ratio",
    "non_performing_loan_ratio",
    "risk_coverage_ratio",
    "capital_leverage_ratio",
    "liquidity_coverage_ratio",
    "net_stable_funding_ratio",
    "interest_net",
    "commission_net",
    "credit_impairment_loss",
    "investing_cash_flow",
    "operating_cash_flow",
    "cash_equivalents",
    "cash_equivalents_end",
    "customer_loans",
    "customer_deposits",
    "central_bank_deposits",
    "financial_investments",
    "receivables",
    "inventory",
    "taxes_and_surcharges",
    "cost_of_revenue",
    "parent_equity",
    "total_assets",
}

LATE_NOTE_VISUAL_PRIORITY_KEYS = {
    "central_bank_deposits",
    "cash_equivalents",
    "cash_equivalents_end",
    "commission_net",
    "credit_impairment_loss",
    "customer_deposits",
    "customer_loans",
    "financial_investments",
    "interest_net",
    "inventory",
    "investing_cash_flow",
    "operating_cash_flow",
    "receivables",
    "taxes_and_surcharges",
}

FRONT_MATTER_PAGE_LIMIT = 60
SMART_VISUAL_MAX_PAGES = 24
STRICT_VISUAL_PAGE_LIMIT = 120
MAX_EXACT_RELATIVE_DELTA = 0.01
MAX_EXACT_DIFFS_PER_KEY = 8
MAX_SMALL_SCALE_ABSOLUTE_DELTA = 5.0
MIN_VISUAL_ROW_LABEL_SIMILARITY = 0.45
MIN_VISUAL_DIGIT_TAMPER_SIMILARITY = 0.82
MIN_DENSE_EMBEDDED_VISUAL_ITEMS = 8
MIN_DENSE_EMBEDDED_VISUAL_PAGES = 4
MIN_SUFFICIENT_H_TEXT_METRIC_KEYS = 4
SIGNED_ABS_VISUAL_KEYS = {
    "credit_impairment_loss",
    "investing_cash_flow",
    "operating_cash_flow",
    "financing_cash_flow",
}

HIGH_RISK_PAGE_TERMS = (
    "key financial indicators",
    "key accounting data",
    "major accounting data",
    "financial indicators",
    "profitability",
    "return on net assets",
    "return on equity",
    "roe",
    "capital adequacy",
    "regulatory ratios",
    "risk coverage",
    "liquidity coverage",
    "net stable funding",
    "per share",
    "financial statements",
    "statement of financial position",
    "income statement",
    "cash flow statement",
    "balance sheet",
    "主要会计数据",
    "主要财务指标",
    "关键指标",
    "盈利能力",
    "净资产收益率",
    "资本监管",
    "监管指标",
    "风险覆盖率",
    "流动性覆盖率",
    "每股",
    "财务报表",
    "资产负债表",
    "利润表",
    "现金流量表",
)

OcrExtractor = Callable[[ReportProfile], list[MetricItem]]
VisualReviewMode = Literal["off", "smart", "strict"]


def run_key_metric_tamper_checks(
    profile_a: ReportProfile,
    profile_h: ReportProfile,
    *,
    visual_review_mode: VisualReviewMode = "smart",
    ocr_extractor: OcrExtractor | None = None,
    visual_ocr_status: dict | None = None,
) -> list[Diff]:
    """Check small exact mismatches and visual/text-layer disagreements.

    This is intentionally separate from the broad numeric checker. The broad
    checker keeps materiality/rounding safeguards; this checker only covers the
    front key-metric table and high-value tamper-style signals.
    """

    started = time.perf_counter()
    status = _new_visual_ocr_status(visual_review_mode)
    try:
        text_a = _front_key_metric_items(profile_a)
        text_h = _front_key_metric_items(profile_h)
        embedded_visual_a = _embedded_visual_items(text_a)
        embedded_visual_h = _embedded_visual_items(text_h)
        if visual_review_mode == "smart" and _has_sufficient_h_text_layer_metrics(text_h):
            embedded_visual_h = []
        ocr_a = _ocr_items_for_profile(
            profile_a,
            text_a,
            embedded_visual_a,
            visual_review_mode=visual_review_mode,
            ocr_extractor=ocr_extractor,
            visual_ocr_status=status,
        )
        ocr_h = _ocr_items_for_profile(
            profile_h,
            text_h,
            embedded_visual_h,
            visual_review_mode=visual_review_mode,
            ocr_extractor=ocr_extractor,
            visual_ocr_status=status,
        )

        diffs: list[Diff] = []
        diffs.extend(_visual_layer_mismatches(profile_a, text_a, [*ocr_a, *embedded_visual_a]))
        diffs.extend(_visual_layer_mismatches(profile_h, text_h, [*ocr_h, *embedded_visual_h]))

        compare_a = [*_non_embedded_items(text_a), *_front_key_metric_items_from_list(ocr_a)]
        compare_h = [*_non_embedded_items(text_h), *_front_key_metric_items_from_list(ocr_h)]
        diffs.extend(_exact_cross_report_mismatches(compare_a, compare_h))
        return _dedupe_diffs(diffs)
    finally:
        status["elapsed_seconds"] = round(time.perf_counter() - started, 4)
        if visual_ocr_status is not None:
            visual_ocr_status.clear()
            visual_ocr_status.update(status)


def _ocr_items_for_profile(
    profile: ReportProfile,
    text_items: list[MetricItem],
    embedded_visual_items: list[MetricItem],
    *,
    visual_review_mode: VisualReviewMode,
    ocr_extractor: OcrExtractor | None,
    visual_ocr_status: dict,
) -> list[MetricItem]:
    if visual_review_mode == "off":
        visual_ocr_status.setdefault("skipped_reason", "runtime_ocr_disabled")
        return []
    side_status = _side_visual_status(visual_ocr_status, profile)

    if (
        visual_review_mode == "smart"
        and profile.side == ReportSide.H_SHARE
        and not embedded_visual_items
        and _has_sufficient_h_text_layer_metrics(text_items)
    ):
        side_status["skipped_reason"] = "h_text_layer_sufficient"
        return []

    skip_reason = _runtime_ocr_skip_reason(profile, visual_review_mode)
    if skip_reason:
        visual_ocr_status.setdefault("skipped_reason", skip_reason)
        side_status["skipped_reason"] = skip_reason
        return []

    if visual_review_mode == "smart" and embedded_visual_items:
        if _has_dense_embedded_visual_coverage(embedded_visual_items):
            side_status["skipped_reason"] = "embedded_visual_coverage_sufficient"
            return []
        uncovered_pages = _uncovered_visual_metric_pages(text_items, embedded_visual_items)
        candidate_pages = set(_candidate_ocr_pages(profile, visual_review_mode=visual_review_mode))
        side_status["candidate_page_count"] = len(candidate_pages)
        if candidate_pages:
            uncovered_pages = [page for page in uncovered_pages if page in candidate_pages]
        if not uncovered_pages:
            side_status["skipped_reason"] = "no_uncovered_visual_pages"
            return []
        if ocr_extractor is None:
            return _safe_ocr(
                profile,
                lambda target: _call_default_ocr_extractor(
                    target,
                    visual_review_mode=visual_review_mode,
                    pages_override=uncovered_pages,
                    visual_ocr_status=visual_ocr_status,
                ),
                visual_ocr_status,
            )
        return _safe_ocr(profile, ocr_extractor, visual_ocr_status)

    if ocr_extractor is not None:
        if not _front_key_metric_items_from_list(_non_embedded_items(text_items)):
            side_status["skipped_reason"] = "no_front_key_metrics"
            return []
        side_status["candidate_page_count"] = len(_candidate_ocr_pages(profile, visual_review_mode=visual_review_mode))
        return _safe_ocr(profile, ocr_extractor, visual_ocr_status)

    candidate_pages = _candidate_ocr_pages(profile, visual_review_mode=visual_review_mode)
    side_status["candidate_page_count"] = len(candidate_pages)
    if not candidate_pages:
        side_status["skipped_reason"] = "no_candidate_pages"
        return []

    extractor = lambda target: _call_default_ocr_extractor(
        target,
        visual_review_mode=visual_review_mode,
        visual_ocr_status=visual_ocr_status,
    )
    return _safe_ocr(profile, extractor, visual_ocr_status)


def _has_sufficient_h_text_layer_metrics(text_items: list[MetricItem]) -> bool:
    keys = {
        item.canonical_key
        for item in _front_key_metric_items_from_list(_non_embedded_items(text_items))
        if item.confidence >= 0.7 and item.value is not None
    }
    return len(keys) >= MIN_SUFFICIENT_H_TEXT_METRIC_KEYS


def _has_dense_embedded_visual_coverage(embedded_visual_items: list[MetricItem]) -> bool:
    pages = {int(item.page or 0) for item in embedded_visual_items if item.page}
    keys = {item.canonical_key for item in embedded_visual_items if item.canonical_key}
    return (
        len(embedded_visual_items) >= MIN_DENSE_EMBEDDED_VISUAL_ITEMS
        and len(pages) >= MIN_DENSE_EMBEDDED_VISUAL_PAGES
        and len(keys) >= 2
    )


def _uncovered_visual_metric_pages(
    text_items: list[MetricItem],
    embedded_visual_items: list[MetricItem],
) -> list[int]:
    covered = {
        (item.canonical_key, int(item.page or 0))
        for item in embedded_visual_items
        if item.page
    }
    uncovered_pages = {
        int(item.page or 0)
        for item in _front_key_metric_items_from_list(_non_embedded_items(text_items))
        if item.page and (item.canonical_key, int(item.page or 0)) not in covered
    }
    return sorted(page for page in uncovered_pages if page > 0)


def _safe_ocr(profile: ReportProfile, extractor: OcrExtractor, visual_ocr_status: dict | None = None) -> list[MetricItem]:
    try:
        return extractor(profile)
    except Exception as exc:  # noqa: BLE001
        logger.warning("key metric visual OCR skipped for {}: {}", profile.doc_id, exc)
        if visual_ocr_status is not None:
            visual_ocr_status.setdefault("skipped_reason", "ocr_exception")
            side_status = _side_visual_status(visual_ocr_status, profile)
            side_status["skipped_reason"] = "ocr_exception"
            side_status["error"] = str(exc)
        return []


def _embedded_visual_items(items: list[MetricItem]) -> list[MetricItem]:
    return [item for item in items if _is_ocr_item(item)]


def _non_embedded_items(items: list[MetricItem]) -> list[MetricItem]:
    return [item for item in items if not _is_ocr_item(item)]


def _default_ocr_extractor(
    profile: ReportProfile,
    *,
    visual_review_mode: VisualReviewMode = "smart",
    pages_override: list[int] | None = None,
    visual_ocr_status: dict | None = None,
) -> list[MetricItem]:
    doc = getattr(profile, "source_doc", None)
    file_path = getattr(doc, "file_path", "") if doc is not None else ""
    if not file_path or not Path(file_path).exists():
        return []

    pages = pages_override or _candidate_ocr_pages(profile, visual_review_mode=visual_review_mode)
    if not pages:
        return []
    selected_pages = _budgeted_visual_pages(pages, visual_review_mode)
    side_status = _side_visual_status(visual_ocr_status, profile) if visual_ocr_status is not None else None
    if side_status is not None:
        side_status["candidate_page_count"] = len(pages)
        side_status["ocr_page_count"] = len(selected_pages)
        side_status["selected_pages"] = selected_pages

    try:
        from ahcc.parser.ocr_fallback import extract_metrics_via_ocr
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR extractor unavailable for key metric tamper check: {}", exc)
        return []

    currency = None
    if getattr(profile, "metadata", None):
        currency = profile.metadata.get("currency")
    runtime_status: dict = {}
    items = extract_metrics_via_ocr(
        file_path,
        side=profile.side,
        pages=selected_pages,
        unit=(getattr(profile, "metadata", None) or {}).get("unit"),
        currency=currency,
        max_seconds=float(getattr(settings, "visual_ocr_max_seconds_per_side", 45.0) or 45.0),
        runtime_status=runtime_status,
    )
    if side_status is not None:
        side_status.update(runtime_status)
        side_status["ocr_page_count"] = len(runtime_status.get("processed_pages") or selected_pages)
        if runtime_status.get("timed_out"):
            visual_ocr_status["timed_out"] = True
    return items


def _call_default_ocr_extractor(
    profile: ReportProfile,
    *,
    visual_review_mode: VisualReviewMode,
    pages_override: list[int] | None = None,
    visual_ocr_status: dict | None = None,
) -> list[MetricItem]:
    try:
        return _default_ocr_extractor(
            profile,
            visual_review_mode=visual_review_mode,
            pages_override=pages_override,
            visual_ocr_status=visual_ocr_status,
        )
    except TypeError as exc:
        if "visual_ocr_status" not in str(exc):
            raise
        return _default_ocr_extractor(
            profile,
            visual_review_mode=visual_review_mode,
            pages_override=pages_override,
        )


def _new_visual_ocr_status(visual_review_mode: VisualReviewMode) -> dict:
    return {
        "mode": visual_review_mode,
        "engine": "not_used" if visual_review_mode == "off" else _available_ocr_engine(),
        "sides": {},
        "elapsed_seconds": 0.0,
    }


def _side_visual_status(visual_ocr_status: dict | None, profile: ReportProfile) -> dict:
    if visual_ocr_status is None:
        return {}
    side = getattr(profile.side, "value", str(profile.side))
    if side == ReportSide.A_SHARE.value:
        key = "A"
    elif side == ReportSide.H_SHARE.value:
        key = "H"
    else:
        key = side
    sides = visual_ocr_status.setdefault("sides", {})
    return sides.setdefault(key, {"candidate_page_count": 0, "ocr_page_count": 0})


def _available_ocr_engine() -> str:
    try:
        from ahcc.parser.ocr_fallback import _EASYOCR_AVAILABLE, _PADDLEOCR_AVAILABLE
    except Exception:  # noqa: BLE001
        return "none"
    if _PADDLEOCR_AVAILABLE:
        return "paddleocr"
    if _EASYOCR_AVAILABLE:
        return "easyocr"
    return "none"


def _runtime_ocr_skip_reason(profile: ReportProfile, visual_review_mode: VisualReviewMode) -> str | None:
    if visual_review_mode != "smart" or _available_ocr_engine() != "easyocr":
        return None
    doc = getattr(profile, "source_doc", None)
    file_path = Path(getattr(doc, "file_path", "") or "")
    total_pages = int(getattr(profile, "total_pages", 0) or getattr(doc, "total_pages", 0) or 0)
    skip_pages = int(getattr(settings, "visual_ocr_easyocr_skip_pages", 180) or 180)
    skip_mb = float(getattr(settings, "visual_ocr_easyocr_skip_mb", 20.0) or 20.0)
    size_mb = file_path.stat().st_size / 1024 / 1024 if file_path.exists() else 0.0
    if total_pages >= skip_pages or size_mb >= skip_mb:
        return "easyocr_large_pdf"
    return None


def _budgeted_visual_pages(pages: list[int], visual_review_mode: VisualReviewMode) -> list[int]:
    unique_pages = sorted({int(page) for page in pages if int(page) > 0})
    if visual_review_mode == "strict":
        limit = int(getattr(settings, "visual_ocr_strict_max_pages", 24) or 24)
    elif visual_review_mode == "smart":
        limit = int(getattr(settings, "visual_ocr_smart_max_pages", 8) or 8)
    else:
        limit = 0
    return unique_pages[: max(limit, 0)]


def _candidate_ocr_pages(profile: ReportProfile, visual_review_mode: VisualReviewMode = "smart") -> list[int]:
    total_pages = max(int(profile.total_pages or 0), 0)
    metric_items = _front_key_metric_items(profile)
    high_risk_pages = _high_risk_visual_pages(profile)
    pages = {
        item.page
        for item in metric_items
        if item.page and 1 <= item.page <= total_pages
    }
    pages.update(high_risk_pages)

    if visual_review_mode == "strict":
        pages.update(range(1, min(total_pages, STRICT_VISUAL_PAGE_LIMIT) + 1))
        return sorted(page for page in pages if 1 <= page <= total_pages)

    if pages:
        return _select_smart_visual_pages(metric_items, high_risk_pages, pages, total_pages)
    return []


def _select_smart_visual_pages(
    metric_items: list[MetricItem],
    high_risk_pages: set[int],
    pages: set[int],
    total_pages: int,
) -> list[int]:
    valid_pages = {page for page in pages if 1 <= page <= total_pages}
    if len(valid_pages) <= SMART_VISUAL_MAX_PAGES:
        return sorted(valid_pages)

    page_scores: dict[int, float] = {
        page: _smart_page_position_score(page)
        for page in valid_pages
    }
    for page in high_risk_pages:
        if page in page_scores:
            page_scores[page] += 55.0
    for item in metric_items:
        page = int(item.page or 0)
        if page not in page_scores:
            continue
        page_scores[page] += _smart_metric_item_page_score(item)

    priority_pages = _late_visual_topic_anchor_pages(metric_items, total_pages)
    selected_priority = sorted(page for page in priority_pages if page in valid_pages)[:SMART_VISUAL_MAX_PAGES]
    remaining_slots = SMART_VISUAL_MAX_PAGES - len(selected_priority)
    if remaining_slots <= 0:
        return sorted(selected_priority)

    selected = sorted(
        valid_pages - set(selected_priority),
        key=lambda page: (-page_scores.get(page, 0.0), page),
    )[:remaining_slots]
    return sorted({*selected_priority, *selected})


def _late_visual_topic_anchor_pages(metric_items: list[MetricItem], total_pages: int) -> set[int]:
    """Keep one late-report page per priority topic before score-based fill.

    Bank and note samples often place the visible edited value in the body of a
    note/cash-flow page while text extraction only sees the heading or year.
    Those pages score poorly but are exactly where visual OCR is needed.
    """

    first_by_key: dict[str, int] = {}
    for item in metric_items:
        page = int(item.page or 0)
        if page <= STRICT_VISUAL_PAGE_LIMIT or page > total_pages:
            continue
        if item.canonical_key not in LATE_NOTE_VISUAL_PRIORITY_KEYS:
            continue
        if not _is_late_visual_topic_anchor(item):
            continue
        current = first_by_key.get(item.canonical_key)
        if current is None or page < current:
            first_by_key[item.canonical_key] = page
    return set(first_by_key.values())


def _is_late_visual_topic_anchor(item: MetricItem) -> bool:
    text = _scope_search_text(item)
    compact = re.sub(r"\s+", "", text)
    section = ((item.evidence.section if item.evidence else "") or "").strip().lower()
    cash_flow_anchor_keys = {"investing_cash_flow", "operating_cash_flow", "cash_equivalents_end"}
    if section == "notes":
        return True
    if section in {"cf", "cash_flow_statement", "cash_flow"}:
        return item.canonical_key in cash_flow_anchor_keys
    if _has_any_scope_marker(compact, ("cashflow", "现金流量", "现金流")):
        return item.canonical_key in cash_flow_anchor_keys
    return _has_any_scope_marker(
        compact,
        (
            "附注",
            "注",
            "notes",
            "本集团本行",
        ),
    )


def _smart_page_position_score(page: int) -> float:
    if page <= FRONT_MATTER_PAGE_LIMIT:
        return 34.0
    if page <= STRICT_VISUAL_PAGE_LIMIT:
        return 16.0
    return 4.0


def _smart_metric_item_page_score(item: MetricItem) -> float:
    score = 20.0
    if item.source == "table":
        score += 8.0
    if item.page and item.page > STRICT_VISUAL_PAGE_LIMIT and item.canonical_key in LATE_NOTE_VISUAL_PRIORITY_KEYS:
        score += 74.0

    text = _scope_search_text(item).lower()
    compact = re.sub(r"\s+", "", text)

    if _has_any_scope_marker(
        text,
        (
            "notes",
            "note",
            "financial statements",
            "statement of financial position",
            "income statement",
            "cash flow statement",
            "\u9644\u6ce8",
            "\u8d22\u52a1\u62a5\u8868",
            "\u8d44\u4ea7\u8d1f\u503a\u8868",
            "\u5229\u6da6\u8868",
            "\u73b0\u91d1\u6d41\u91cf\u8868",
        ),
    ):
        score += 36.0
    if _has_any_scope_marker(
        compact,
        (
            "total",
            "subtotal",
            "carryingamount",
            "netamount",
            "endingbalance",
            "netcashflows",
            "cashflow",
            "cashflows",
            "\u5408\u8ba1",
            "\u5c0f\u8ba1",
            "\u8d26\u9762\u4ef7\u503c",
            "\u8d26\u9762\u4ef7\u503c\u5408\u8ba1",
            "\u51c0\u989d",
            "\u5e74\u672b\u4f59\u989d",
            "\u671f\u672b\u4f59\u989d",
            "\u73b0\u91d1\u6d41",
        ),
    ):
        score += 34.0
    if item.page and item.page <= FRONT_MATTER_PAGE_LIMIT:
        score += 18.0
    return score


def _high_risk_visual_pages(profile: ReportProfile) -> set[int]:
    doc = getattr(profile, "source_doc", None)
    if doc is None:
        return set()

    pages: set[int] = set()
    for table in getattr(doc, "tables", []) or []:
        text = " ".join(
            [
                getattr(getattr(table, "title", None), "zh", "") or "",
                getattr(getattr(table, "title", None), "en", "") or "",
                getattr(table, "section", "") or "",
                " ".join(getattr(cell, "text", "") or "" for cell in getattr(table, "cells", []) or []),
            ]
        )
        if _is_high_risk_visual_text(text) or _has_dense_decimal_or_percent_table(table):
            pages.add(int(table.page))

    for segment in getattr(doc, "texts", []) or []:
        text = " ".join([getattr(segment, "section", "") or "", getattr(segment, "text", "") or ""])
        if _is_high_risk_visual_text(text):
            pages.add(int(segment.page))
    return pages


def _is_high_risk_visual_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in HIGH_RISK_PAGE_TERMS)


def _has_dense_decimal_or_percent_table(table) -> bool:
    values = [getattr(cell, "text", "") or "" for cell in getattr(table, "cells", []) or []]
    numeric_like = sum(1 for text in values if "%" in text or _contains_decimal_number(text))
    return numeric_like >= 2


def _contains_decimal_number(text: str) -> bool:
    import re

    return bool(re.search(r"\d+\.\d+", text or ""))


def _front_key_metric_items(profile: ReportProfile) -> list[MetricItem]:
    items: list[MetricItem] = []
    for occ in profile.metrics:
        if not isinstance(occ, MetricOccurrences):
            continue
        items.extend(_front_key_metric_items_from_list(occ.all_occurrences))
    return items


def _front_key_metric_items_from_list(items: list[MetricItem]) -> list[MetricItem]:
    result: list[MetricItem] = []
    for item in items:
        if item.canonical_key not in KEY_METRIC_TAMPER_KEYS:
            continue
        if item.value is None:
            continue
        if item.confidence < 0.6:
            continue
        if item.source not in {"table", "generic_pattern", "text"}:
            continue
        result.append(item)
    return result


def _best_items_by_key(items: list[MetricItem]) -> dict[str, MetricItem]:
    best: dict[str, MetricItem] = {}
    for item in items:
        current = best.get(item.canonical_key)
        if current is None or _rank_item(item) > _rank_item(current):
            best[item.canonical_key] = item
    return best


def _rank_item(item: MetricItem) -> tuple[int, int, float, int]:
    visual = 1 if _is_ocr_item(item) else 0
    table = 1 if item.source == "table" else 0
    front_rank = -int(item.page or 9999)
    return (visual, table, item.confidence, front_rank)


def _is_ocr_item(item: MetricItem) -> bool:
    snippet = (item.evidence.snippet or "").lower()
    return item.source == "generic_pattern" and ("ocr" in snippet or "visual" in snippet)


def _visual_layer_mismatches(
    profile: ReportProfile,
    text_items: list[MetricItem],
    ocr_items: list[MetricItem],
) -> list[Diff]:
    text_by_key_page: dict[tuple[str, int], list[MetricItem]] = {}
    for item in text_items:
        if _is_ocr_item(item):
            continue
        text_by_key_page.setdefault((item.canonical_key, item.page), []).append(item)

    diffs: list[Diff] = []
    for visible in _front_key_metric_items_from_list(ocr_items):
        candidates = _visual_text_layer_candidates(visible, text_by_key_page)
        text_item = _best_text_layer_match(visible, candidates)
        if text_item is None:
            continue
        visible_value, text_value = _normalized_visual_text_values(visible, text_item)
        if visible_value is None or text_value is None:
            continue
        if _same_value(visible_value, text_value):
            continue
        delta = abs(visible_value - text_value)
        label = _label_for(visible, text_item)
        side_label = "A" if profile.side == ReportSide.A_SHARE else "H"
        evidence = [visible.evidence, text_item.evidence]
        canonical_key = _visual_diff_canonical_key(visible, text_item)
        diffs.append(
            Diff(
                diff_id=f"VISUAL_{side_label}_{canonical_key}_{visible.page}_{uuid.uuid4().hex[:4]}",
                diff_type=DiffType.INTERNAL,
                diff_scope=DiffScope.A_INTERNAL if profile.side == ReportSide.A_SHARE else DiffScope.H_INTERNAL,
                severity=DiffSeverity.HIGH,
                triage="real",
                canonical_key=canonical_key,
                topic=LocalizedString(zh=f"{side_label}股报告视觉层数值", en=f"{side_label} visual value"),
                summary=LocalizedString(
                    zh=(
                        f"{side_label}股报告第{visible.page}页{label}视觉层为 {visible_value:,.2f}，"
                        f"PDF文本层为 {text_value:,.2f}，疑似可见披露与可抽取文本不一致"
                    ),
                    en=(
                        f"{side_label}-side page {visible.page} {label}: visual OCR value "
                        f"{visible_value:,.2f} differs from PDF text-layer value {text_value:,.2f}"
                    ),
                ),
                a_value=visible_value,
                h_value=text_value,
                delta=delta,
                tolerance=0.0,
                evidence=evidence,
                rule_id="visual_text_layer_mismatch",
            )
        )
    return diffs


def _visual_text_layer_candidates(
    visible: MetricItem,
    text_by_key_page: dict[tuple[str, int], list[MetricItem]],
) -> list[MetricItem]:
    page = int(visible.page or 0)
    keys = [visible.canonical_key, *_compatible_visual_text_keys(visible.canonical_key)]
    seen: set[int] = set()
    candidates: list[MetricItem] = []
    for key in keys:
        for item in text_by_key_page.get((key, page), []):
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            candidates.append(item)
    return candidates


def _compatible_visual_text_keys(key: str) -> tuple[str, ...]:
    if key == "cash_equivalents":
        return ("cash_equivalents_end",)
    if key == "cash_equivalents_end":
        return ("cash_equivalents",)
    return ()


def _visual_keys_are_family_compatible(left: str, right: str) -> bool:
    return left == right or right in _compatible_visual_text_keys(left)


def _visual_diff_canonical_key(visible: MetricItem, text_item: MetricItem) -> str:
    if visible.canonical_key != text_item.canonical_key and _visual_keys_are_family_compatible(
        visible.canonical_key,
        text_item.canonical_key,
    ):
        return text_item.canonical_key
    return visible.canonical_key


def _best_text_layer_match(visible: MetricItem, candidates: list[MetricItem]) -> MetricItem | None:
    if not candidates:
        return None
    visible_label = _row_label_for_visual_match(visible)
    comparable = [
        candidate for candidate in candidates
        if _visual_text_values_are_comparable(visible, candidate)
        and _visual_row_labels_are_compatible(visible, candidate)
    ]
    if not comparable:
        return None
    candidates = comparable
    if len(candidates) == 1:
        return candidates[0]
    return max(
        candidates,
        key=lambda item: (
            _number_text_similarity(visible.value_text or str(visible.value or ""), item.value_text or str(item.value or "")),
            _row_label_similarity(visible_label, _row_label_for_visual_match(item)),
            item.confidence,
            -int(item.page or 9999),
        ),
    )


def _visual_text_values_are_comparable(visible: MetricItem, text_item: MetricItem) -> bool:
    visible_value, text_value = _normalized_visual_text_values(visible, text_item)
    if visible_value is None or text_value is None:
        return False
    if _same_value(visible_value, text_value):
        return False
    delta = abs(visible_value - text_value)
    base = max(abs(visible_value), abs(text_value), 1.0)
    ratio = delta / base
    if _requires_visual_digit_tamper_signature(text_item):
        similarity = _number_text_similarity(
            visible.value_text or str(visible.value or ""),
            text_item.value_text or str(text_item.value or ""),
        )
        if similarity < MIN_VISUAL_DIGIT_TAMPER_SIMILARITY:
            return False
    if ratio <= MAX_EXACT_RELATIVE_DELTA:
        return True
    if _small_scale_metric_delta_allowed(visible.canonical_key, delta):
        return _small_scale_visual_digits_are_compatible(visible, text_item)
    raw_delta = _raw_visual_text_delta(visible, text_item)
    if (
        raw_delta is not None
        and _small_scale_metric_delta_allowed(visible.canonical_key, raw_delta)
        and _small_scale_visual_digits_are_compatible(visible, text_item)
    ):
        return True
    section = ((text_item.evidence.section if text_item.evidence else "") or "").strip().lower()
    if section != "notes" and ratio <= 0.10:
        if section in {"mda", "revenue"}:
            return _number_text_similarity(
                visible.value_text or str(visible.value or ""),
                text_item.value_text or str(text_item.value or ""),
            ) >= MIN_VISUAL_DIGIT_TAMPER_SIMILARITY
        return True
    if section == "notes" and ratio <= 0.03:
        return True
    return False


def _normalized_visual_text_values(visible: MetricItem, text_item: MetricItem) -> tuple[float | None, float | None]:
    visible_value = _normalized_value(visible)
    text_value = _normalized_value(text_item)
    if visible_value is None or text_value is None:
        return None, None
    if (
        visible.canonical_key == text_item.canonical_key
        and visible.canonical_key in SIGNED_ABS_VISUAL_KEYS
        and visible_value < 0 < text_value
        and _number_text_similarity(
            visible.value_text or str(visible.value or ""),
            text_item.value_text or str(text_item.value or ""),
        ) >= 0.78
    ):
        return abs(visible_value), abs(text_value)
    return visible_value, text_value


def _small_scale_visual_digits_are_compatible(visible: MetricItem, text_item: MetricItem) -> bool:
    return _number_text_similarity(
        visible.value_text or str(visible.value or ""),
        text_item.value_text or str(text_item.value or ""),
    ) >= 0.50


def _raw_visual_text_delta(visible: MetricItem, text_item: MetricItem) -> float | None:
    if visible.value is None or text_item.value is None:
        return None
    visible_value = float(visible.value)
    text_value = float(text_item.value)
    if (
        visible.canonical_key == text_item.canonical_key
        and visible.canonical_key in SIGNED_ABS_VISUAL_KEYS
        and visible_value < 0 < text_value
    ):
        visible_value = abs(visible_value)
        text_value = abs(text_value)
    return abs(visible_value - text_value)


def _requires_visual_digit_tamper_signature(text_item: MetricItem) -> bool:
    section = ((text_item.evidence.section if text_item.evidence else "") or "").strip().lower()
    return section in {"notes", "related_party"}


def _number_text_similarity(left: str, right: str) -> float:
    left_digits = re.sub(r"\D+", "", left or "")
    right_digits = re.sub(r"\D+", "", right or "")
    if not left_digits or not right_digits:
        return 0.0
    return SequenceMatcher(None, left_digits, right_digits).ratio()


def _row_label_for_visual_match(item: MetricItem) -> str:
    snippet = (item.evidence.snippet if item.evidence else "") or ""
    patterns = (
        r"^\[(?:visual\s+overlay|visual|pdf\s+visual|ocr\s+visual)\s*[·|:：-]\s*([^\]]+?)\]",
        r"^\[OCR table\]\s*(.*?)\s*=",
        r"^\[OCR\s+([^\]]+)\]",
        r"^\[([^\]\|·]+?)\s*(?:[\|·]\s*[^\]]+)?\]",
    )
    for pattern in patterns:
        match = re.search(pattern, snippet, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return item.name.zh or item.name.en or item.canonical_key


def _normalize_row_label(text: str) -> str:
    normalized = (text or "").lower()
    normalized = re.sub(r"\b(?:ocr|table|visual|pdf|text|layer)\b", " ", normalized)
    normalized = re.sub(r"(?:19|20)\d{2}(?:-q[1-4])?", " ", normalized)
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    return normalized


def _row_label_similarity(a: str, b: str) -> float:
    left = _normalize_row_label(a)
    right = _normalize_row_label(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        ratio = max(ratio, min(len(left), len(right)) / max(len(left), len(right)))
    return ratio


def _visual_row_labels_are_compatible(visible: MetricItem, text_item: MetricItem) -> bool:
    visible_label = _row_label_for_visual_match(visible)
    text_label = _row_label_for_visual_match(text_item)
    if _row_label_similarity(visible_label, text_label) >= MIN_VISUAL_ROW_LABEL_SIMILARITY:
        return True
    if visible.canonical_key == text_item.canonical_key == "parent_equity":
        combined_label = _normalize_row_label(f"{visible_label} {text_label}")
        if "净资产" in combined_label and any(marker in combined_label for marker in ("权益", "所有者权益", "股东权益")):
            return True
    if visible.canonical_key == text_item.canonical_key == "customer_loans":
        combined_label = _normalize_row_label(f"{visible_label} {text_label}")
        if "贷款" in combined_label and "垫款" in combined_label and "账面价值" in combined_label:
            return True

    if visible.canonical_key != text_item.canonical_key and _visual_keys_are_family_compatible(
        visible.canonical_key,
        text_item.canonical_key,
    ):
        visible_value = _normalized_value(visible)
        text_value = _normalized_value(text_item)
        if visible_value is None or text_value is None:
            return False
        delta = abs(visible_value - text_value)
        base = max(abs(visible_value), abs(text_value), 1.0)
        if delta / base <= MAX_EXACT_RELATIVE_DELTA:
            return True

    section = ((text_item.evidence.section if text_item.evidence else "") or "").strip().lower()
    if section != "notes" or visible.canonical_key not in LATE_NOTE_VISUAL_PRIORITY_KEYS:
        return False
    if not _is_total_row_label(text_label):
        return False

    visible_value = _normalized_value(visible)
    text_value = _normalized_value(text_item)
    if visible_value is None or text_value is None:
        return False
    delta = abs(visible_value - text_value)
    base = max(abs(visible_value), abs(text_value), 1.0)
    if delta / base <= MAX_EXACT_RELATIVE_DELTA:
        return True
    return _number_text_similarity(visible.value_text or str(visible.value or ""), text_item.value_text or str(text_item.value or "")) >= 0.82


def _is_total_row_label(label: str) -> bool:
    normalized = _normalize_row_label(label)
    return any(marker in normalized for marker in ("合计", "小计", "账面价值合计", "total", "subtotal"))


def _exact_cross_report_mismatches(
    a_by_key: dict[str, MetricItem],
    h_by_key: dict[str, MetricItem],
) -> list[Diff]:
    diffs: list[Diff] = []
    for key in sorted(set(a_by_key) & set(h_by_key)):
        a_item = a_by_key[key]
        h_item = h_by_key[key]
        a_value = _normalized_value(a_item)
        h_value = _normalized_value(h_item)
        if a_value is None or h_value is None:
            continue
        if not _same_reporting_scope(a_item, h_item):
            continue
        if _same_value(a_value, h_value):
            continue
        delta = abs(a_value - h_value)
        ratio = delta / max(abs(a_value), abs(h_value), 1.0)
        if ratio > MAX_EXACT_RELATIVE_DELTA:
            continue
        llm_reason = _llm_exact_downgrade_reason(key, a_item, h_item, a_value, h_value)
        if llm_reason:
            diffs.append(_make_llm_review_diff(key, a_item, h_item, a_value, h_value, llm_reason))
            continue
        label = _label_for(a_item, h_item)
        evidence = [a_item.evidence, h_item.evidence]
        severity = DiffSeverity.MEDIUM if ratio <= 0.005 else DiffSeverity.HIGH
        diffs.append(
            Diff(
                diff_id=f"EXACT_{key}_{uuid.uuid4().hex[:6]}",
                diff_type=DiffType.NUMERIC,
                diff_scope=DiffScope.CROSS_REPORT,
                severity=severity,
                triage="unresolved",
                canonical_key=key,
                topic=LocalizedString(zh=f"关键指标精确差异：{label}", en=f"Exact key metric mismatch: {label}"),
                summary=LocalizedString(
                    zh=f"{label}: A股 {a_value:,.2f}，H股 {h_value:,.2f}，精确差异 {delta:,.2f}",
                    en=f"{label}: A={a_value:,.2f}, H={h_value:,.2f}, exact delta={delta:,.2f}",
                ),
                a_value=a_value,
                h_value=h_value,
                delta=delta,
                tolerance=0.0,
                evidence=evidence,
                rule_id="key_metric_exact_mismatch",
            )
        )
    return diffs


def _exact_cross_report_mismatches(
    a_items: list[MetricItem] | dict[str, MetricItem],
    h_items: list[MetricItem] | dict[str, MetricItem],
) -> list[Diff]:
    if isinstance(a_items, dict):
        a_items = list(a_items.values())
    if isinstance(h_items, dict):
        h_items = list(h_items.values())

    a_by_key = _items_by_key(a_items)
    h_by_key = _items_by_key(h_items)
    diffs: list[Diff] = []
    for key in sorted(set(a_by_key) & set(h_by_key)):
        candidates: list[tuple[float, int, int, MetricItem, MetricItem, float, float, float]] = []
        for a_item in a_by_key[key]:
            for h_item in h_by_key[key]:
                candidate = _exact_mismatch_candidate(key, a_item, h_item)
                if candidate is not None:
                    candidates.append(candidate)
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        for ratio, _a_page, _h_page, a_item, h_item, a_value, h_value, delta in candidates[:MAX_EXACT_DIFFS_PER_KEY]:
            llm_reason = _llm_exact_downgrade_reason(key, a_item, h_item, a_value, h_value)
            if llm_reason:
                diffs.append(_make_llm_review_diff(key, a_item, h_item, a_value, h_value, llm_reason))
                continue
            label = _label_for(a_item, h_item)
            severity = DiffSeverity.MEDIUM if ratio <= 0.005 else DiffSeverity.HIGH
            diffs.append(
                Diff(
                    diff_id=f"EXACT_{key}_{uuid.uuid4().hex[:6]}",
                    diff_type=DiffType.NUMERIC,
                    diff_scope=DiffScope.CROSS_REPORT,
                    severity=severity,
                    triage="unresolved",
                    canonical_key=key,
                    topic=LocalizedString(zh=f"关键指标精确差异：{label}", en=f"Exact key metric mismatch: {label}"),
                    summary=LocalizedString(
                        zh=f"{label}: A股 {a_value:,.2f}，H股 {h_value:,.2f}，精确差异 {delta:,.2f}",
                        en=f"{label}: A={a_value:,.2f}, H={h_value:,.2f}, exact delta={delta:,.2f}",
                    ),
                    a_value=a_value,
                    h_value=h_value,
                    delta=delta,
                    tolerance=0.0,
                    evidence=[a_item.evidence, h_item.evidence],
                    rule_id="key_metric_exact_mismatch",
                )
            )
    return diffs


def _items_by_key(items: list[MetricItem]) -> dict[str, list[MetricItem]]:
    grouped: dict[str, list[MetricItem]] = {}
    for item in items:
        grouped.setdefault(item.canonical_key, []).append(item)
    return grouped


def _exact_mismatch_candidate(
    key: str,
    a_item: MetricItem,
    h_item: MetricItem,
) -> tuple[float, int, int, MetricItem, MetricItem, float, float, float] | None:
    a_value = _normalized_value(a_item)
    h_value = _normalized_value(h_item)
    if a_value is None or h_value is None:
        return None
    if not _same_metric_value_role(a_item, h_item):
        return None
    if not _same_reporting_scope(a_item, h_item):
        return None
    if _same_value(a_value, h_value):
        return None
    delta = abs(a_value - h_value)
    ratio = delta / max(abs(a_value), abs(h_value), 1.0)
    if ratio > MAX_EXACT_RELATIVE_DELTA and not _small_scale_metric_delta_allowed(key, delta):
        return None
    return (ratio, int(a_item.page or 9999), int(h_item.page or 9999), a_item, h_item, a_value, h_value, delta)


def _small_scale_metric_delta_allowed(key: str, delta: float) -> bool:
    if key in {
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
    }:
        return delta <= MAX_SMALL_SCALE_ABSOLUTE_DELTA
    return False


def _same_metric_value_role(a_item: MetricItem, h_item: MetricItem) -> bool:
    a_role = _metric_value_role(a_item)
    h_role = _metric_value_role(h_item)
    return a_role == h_role == "main_value"


def _metric_value_role(item: MetricItem) -> str:
    header = _metric_value_header_text(item).lower()
    compact = re.sub(r"\s+", "", header)

    def has(*markers: str) -> bool:
        return any(marker in header or marker in compact for marker in markers)

    if has("note", "notes", "noteno", "\u9644\u6ce8", "\u9644\u6ce8\u7f16\u53f7", "\u9644\u6ce8\u53f7"):
        return "note_reference"
    if has("changeamount", "change amount", "increase/decrease", "\u589e\u51cf\u989d", "\u53d8\u52a8\u989d"):
        return "change_amount"
    if has("percentageoftotal", "percentage of total", "%of", "% of", "\u5360\u6bd4", "\u6bd4\u4f8b"):
        return "ratio_column"
    if has("maturity", "within3months", "within 3 months", "pastdue", "past due", "indefinite", "\u671f\u9650", "\u5df2\u903e\u671f", "\u65e0\u671f\u9650"):
        return "maturity_bucket"
    if has("averagebalance", "average balance", "\u5e73\u5747\u4f59\u989d"):
        return "average_balance"
    return "main_value"


def _metric_value_header_text(item: MetricItem) -> str:
    evidence = item.evidence
    snippet = evidence.snippet if evidence else ""
    match = re.search(r"\[[^\]]*?\s*\u00b7\s*([^\]]+)\]", snippet)
    if match:
        return match.group(1).strip()
    return snippet


def _metric_review_payload(item: MetricItem, normalized_value: float) -> dict:
    evidence = item.evidence
    return {
        "canonical_key": item.canonical_key,
        "label_zh": item.name.zh,
        "label_en": item.name.en,
        "value": item.value,
        "normalized_value": normalized_value,
        "value_text": item.value_text,
        "unit": item.unit,
        "period": item.period,
        "page": item.page,
        "section": evidence.section if evidence else None,
        "snippet": (evidence.snippet if evidence else "")[:500],
        "source": item.source,
    }


def _needs_llm_scope_review(a_item: MetricItem, h_item: MetricItem) -> bool:
    text = f"{_scope_search_text(a_item)} {_scope_search_text(h_item)}"
    return _has_any_scope_marker(
        text,
        (
            "经营指标",
            "主要财务指标",
            "财务指标",
            "operating indicator",
            "financial indicator",
            "key financial data",
        ),
    )


def _llm_exact_downgrade_reason(
    key: str,
    a_item: MetricItem,
    h_item: MetricItem,
    a_value: float,
    h_value: float,
) -> str | None:
    if not _needs_llm_scope_review(a_item, h_item):
        return None
    if not getattr(settings, "numeric_use_llm_semantic_review", True):
        return None
    if not (settings.deepseek_api_key or "").strip():
        return None

    payload = {
        "metric": key,
        "a": _metric_review_payload(a_item, a_value),
        "h": _metric_review_payload(h_item, h_value),
    }
    prompt = (
        "You are a financial reporting consistency reviewer. "
        "A key-metric exact-difference rule found a small numeric mismatch. "
        "Before classifying it as a real A/H difference, decide whether the two values are truly comparable. "
        "Use the page, section, period, table row/column context and snippets. "
        "Do not compare annual figures with quarterly, operating-indicator detail, segment, parent-company, "
        "fair-value, risk-exposure, shareholder, or other different-scope figures. "
        "You may only downgrade or require review; do not invent a difference. "
        "Return strict JSON: {\"comparable\":true|false,\"confidence\":0.0,\"reason\":\"short reason\",\"a_scope\":\"\",\"h_scope\":\"\"}.\n\n"
        f"Candidate:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    result = cached_call(
        "reason",
        [{"role": "user", "content": prompt}],
        json_mode=True,
        temperature=0.0,
        max_tokens=768,
    )
    if isinstance(result, str) and result.strip():
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    if not isinstance(result, dict):
        return None
    comparable = result.get("comparable")
    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if comparable is False and confidence >= getattr(settings, "numeric_llm_review_min_confidence", 0.8):
        return str(result.get("reason") or "LLM semantic review marked the candidate as non-comparable")[:240]
    return None


def _make_llm_review_diff(
    key: str,
    a_item: MetricItem,
    h_item: MetricItem,
    a_value: float,
    h_value: float,
    reason: str,
) -> Diff:
    label = _label_for(a_item, h_item)
    delta = abs(a_value - h_value)
    return Diff(
        diff_id=f"LLM_REVIEW_{key}_{uuid.uuid4().hex[:6]}",
        diff_type=DiffType.NUMERIC,
        diff_scope=DiffScope.CROSS_REPORT,
        severity=DiffSeverity.INFO,
        triage="unresolved",
        canonical_key=key,
        topic=LocalizedString(zh=f"LLM语义复核：{label}", en=f"LLM semantic review: {label}"),
        summary=LocalizedString(
            zh=f"{label}: DeepSeek 语义复核认为候选值可能不是同一报告口径，暂不判定为真实差异（{reason}）",
            en=f"{label}: DeepSeek semantic review marked the candidate as potentially non-comparable ({reason})",
        ),
        a_value=a_value,
        h_value=h_value,
        delta=delta,
        tolerance=0.0,
        evidence=[a_item.evidence, h_item.evidence],
        rule_id="llm_semantic_review",
    )


def _scope_search_text(item: MetricItem) -> str:
    evidence = item.evidence
    return " ".join(
        str(part or "")
        for part in (
            item.name.zh,
            item.name.en,
            item.period,
            item.value_text,
            evidence.section if evidence else None,
            evidence.snippet if evidence else None,
        )
    ).lower()


def _has_any_scope_marker(text: str, markers: tuple[str, ...]) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(marker in text or marker in compact for marker in markers)


def _is_quarterly_scope(item: MetricItem) -> bool:
    text = _scope_search_text(item)
    return _has_any_scope_marker(
        text,
        (
            "分季度",
            "季度",
            "第一季度",
            "一季度",
            "第1季度",
            "1季度",
            "第二季度",
            "二季度",
            "第2季度",
            "2季度",
            "第三季度",
            "三季度",
            "第3季度",
            "3季度",
            "第四季度",
            "四季度",
            "第4季度",
            "4季度",
            "quarterly",
            "firstquarter",
            "secondquarter",
            "thirdquarter",
            "fourthquarter",
        ),
    ) or bool(re.search(r"(?<![a-z0-9])q[1-4](?![a-z0-9])|(?<![a-z0-9])[1-4]q(?![a-z0-9])", text))


def _detail_scope(item: MetricItem) -> str:
    text = _scope_search_text(item)
    if _is_quarterly_scope(item):
        return "quarterly"
    if _has_any_scope_marker(text, ("分部", "业务分部", "经营分部", "segment")):
        return "segment"
    if _has_any_scope_marker(text, ("母公司", "本公司财务报表", "parent company", "company statement")):
        return "parent_company"
    if _has_any_scope_marker(text, ("公允价值", "fair value", "风险敞口", "risk exposure")):
        return "detail"
    return "annual_or_unspecified"


def _same_reporting_scope(a_item: MetricItem, h_item: MetricItem) -> bool:
    a_scope = _detail_scope(a_item)
    h_scope = _detail_scope(h_item)
    if a_scope != h_scope and "annual_or_unspecified" in {a_scope, h_scope}:
        return False
    if a_scope != h_scope:
        return False
    if a_item.period and h_item.period and a_item.period != h_item.period:
        return False
    return True


def _normalized_value(item: MetricItem) -> float | None:
    if item.value is None:
        return None
    return item.value * _unit_multiplier(item.unit)


def _unit_multiplier(unit: str | None) -> float:
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


def _same_value(a_value: float, h_value: float) -> bool:
    return abs(a_value - h_value) <= 1e-9


def _label_for(*items: MetricItem) -> str:
    for item in items:
        if item.name.zh:
            return item.name.zh
        if item.name.en:
            return item.name.en
    return items[0].canonical_key if items else "metric"


def _dedupe_diffs(diffs: list[Diff]) -> list[Diff]:
    seen: set[tuple[str | None, str | None, tuple[int, ...]]] = set()
    result: list[Diff] = []
    for diff in diffs:
        pages = tuple(sorted(ev.page for ev in diff.evidence if ev.page))
        marker = (diff.rule_id, diff.canonical_key, pages)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(diff)
    return result
