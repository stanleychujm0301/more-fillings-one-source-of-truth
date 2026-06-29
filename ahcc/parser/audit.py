"""Helpers for page-level extraction completeness audits."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ahcc.schemas import ExtractionAudit, ReportDocument, TextSegment

EXTRACTION_ENGINE_VERSION = "2026-06-01.7"
PARSER_VERSION = EXTRACTION_ENGINE_VERSION

_AUXILIARY_FLAGS = {
    "chart_detection_failed",
    "chart_engine_unavailable",
    "chart_image_save_failed",
}
_BLOCKING_FLAGS = {
    "empty_document",
    "parser_unavailable",
    "page_scan_incomplete",
    "low_page_coverage",
    "many_blank_pages",
    "no_tables_extracted",
    "low_table_page_coverage",
    "pdfplumber_text_failed",
    "pages_with_zero_segments",
}
_TABLE_ENGINE_FLAGS = {
    "table_page_failed",
    "camelot_failed",
    "ppstructure_failed",
}
_SECTION_CLASSIFICATION_FLAGS = {
    "table_section_classification_gap",
}


def build_extraction_audit(
    *,
    total_pages: int,
    scanned_pages: Iterable[int],
    text_pages: Iterable[int],
    table_pages: Iterable[int],
    ocr_pages: Iterable[int] | None = None,
    text_segments: Iterable[TextSegment] | None = None,
    warning_flags: Iterable[str] | None = None,
    warnings: Iterable[str] | None = None,
    engines: dict[str, Any] | None = None,
    table_coverage: dict[str, Any] | None = None,
) -> ExtractionAudit:
    expected = set(range(1, max(total_pages, 0) + 1))
    scanned = _valid_pages(scanned_pages, total_pages)
    text = _valid_pages(text_pages, total_pages)
    table = _valid_pages(table_pages, total_pages)
    ocr = _valid_pages(ocr_pages or [], total_pages)
    missing = sorted(expected - set(scanned))
    blank = sorted(set(scanned) - set(text))

    # 每页 segment 数诊断：扫描到文本但过滤后没有 segment 的页面
    segment_counts: dict[int, int] = {}
    if text_segments is not None:
        for seg in text_segments:
            if seg.page is not None and 1 <= seg.page <= total_pages:
                segment_counts[seg.page] = segment_counts.get(seg.page, 0) + 1
    pages_with_zero_segments = sorted(
        p for p in scanned
        if p not in blank and segment_counts.get(p, 0) == 0
    )

    flags = list(dict.fromkeys(warning_flags or []))
    notes = list(dict.fromkeys(warnings or []))
    coverage_ratio = round(len(scanned) / total_pages, 4) if total_pages else 0.0
    engine_payload = dict(engines or {})
    engine_payload.setdefault("parser_version", PARSER_VERSION)
    engine_payload.setdefault("extraction_engine_version", EXTRACTION_ENGINE_VERSION)
    table_coverage_payload = dict(table_coverage or {})
    if table_coverage_payload:
        engine_payload["table_coverage"] = table_coverage_payload

    if total_pages <= 0:
        _append_warning(flags, notes, "empty_document", "PDF page count is zero; extraction may have failed.")
    if missing:
        _append_warning(flags, notes, "page_scan_incomplete", f"{len(missing)} pages were not scanned.")
    if coverage_ratio < 0.98 and total_pages > 0:
        _append_warning(flags, notes, "low_page_coverage", f"Only {coverage_ratio:.1%} of pages were scanned.")
    if scanned and len(blank) / max(len(scanned), 1) > 0.1:
        _append_warning(flags, notes, "many_blank_pages", f"{len(blank)} scanned pages produced no text.")
    if pages_with_zero_segments:
        _append_warning(
            flags,
            notes,
            "pages_with_zero_segments",
            f"{len(pages_with_zero_segments)} scanned pages produced text but no retained segments: "
            f"{pages_with_zero_segments[:20]}{'...' if len(pages_with_zero_segments) > 20 else ''}.",
        )
    if not table:
        _append_warning(flags, notes, "no_tables_extracted", "No tables were extracted from this report.")
    elif table_coverage_payload:
        missing_core = list(table_coverage_payload.get("missing_core_sections") or [])
        missing_notes = list(table_coverage_payload.get("missing_key_note_sections") or [])
        if missing_core or missing_notes:
            missing_sections = ", ".join(str(item) for item in [*missing_core, *missing_notes])
            if missing_core and not missing_notes and _is_table_section_classification_gap(
                total_pages=total_pages,
                scanned_pages=scanned,
                text_pages=text,
                table_pages=table,
                table_coverage=table_coverage_payload,
            ):
                _append_warning(
                    flags,
                    notes,
                    "table_section_classification_gap",
                    "Core statement section labels incomplete; "
                    f"{len(table)} table pages were extracted but missing structured sections: {missing_sections}.",
                )
            else:
                _append_warning(
                    flags,
                    notes,
                    "low_table_page_coverage",
                    f"Core table extraction incomplete; missing structured sections: {missing_sections}.",
                )
    elif total_pages >= 100 and len(table) < max(2, int(total_pages * 0.02)):
        _append_warning(flags, notes, "low_table_page_coverage", f"Tables were found on only {len(table)} pages.")
    if ocr:
        _append_warning(flags, notes, "ocr_used", f"OCR fallback was used on {len(ocr)} pages.")

    engine_payload["warning_details"] = [
        classify_warning(flag, notes[idx] if idx < len(notes) else "")
        for idx, flag in enumerate(flags)
    ]

    return ExtractionAudit(
        total_pages=total_pages,
        scanned_pages=scanned,
        missing_pages=missing,
        blank_pages=blank,
        ocr_pages=ocr,
        table_pages=table,
        pages_with_zero_segments=pages_with_zero_segments,
        coverage_ratio=coverage_ratio,
        warning_flags=flags,
        warnings=notes,
        engines=engine_payload,
    )


def attach_audit(doc: ReportDocument, audit: ExtractionAudit) -> ReportDocument:
    doc.extraction_audit = audit
    doc.metadata["extraction_audit"] = audit.model_dump(mode="json")
    return doc


def add_audit_warning(
    doc: ReportDocument,
    flag: str,
    message: str,
    *,
    ocr_pages: Iterable[int] | None = None,
) -> None:
    raw = dict(doc.metadata.get("extraction_audit") or {})
    if doc.extraction_audit and not raw:
        raw = doc.extraction_audit.model_dump(mode="json")
    raw.setdefault("total_pages", doc.total_pages)
    raw.setdefault("scanned_pages", list(range(1, max(doc.total_pages, 0) + 1)))
    raw.setdefault("missing_pages", [])
    raw.setdefault("blank_pages", [])
    raw.setdefault("ocr_pages", [])
    raw.setdefault("table_pages", [])
    raw.setdefault("coverage_ratio", 1.0 if doc.total_pages else 0.0)
    raw.setdefault("warning_flags", [])
    raw.setdefault("warnings", [])
    raw.setdefault("engines", {})
    raw["engines"].setdefault("parser_version", PARSER_VERSION)
    raw["engines"].setdefault("extraction_engine_version", EXTRACTION_ENGINE_VERSION)

    _append_warning(raw["warning_flags"], raw["warnings"], flag, message)
    if ocr_pages is not None:
        pages = sorted(set(raw.get("ocr_pages") or []) | set(_valid_pages(ocr_pages, int(raw.get("total_pages") or 0))))
        raw["ocr_pages"] = pages
        if pages:
            _append_warning(raw["warning_flags"], raw["warnings"], "ocr_used", f"OCR fallback was used on {len(pages)} pages.")
    raw["engines"]["warning_details"] = [
        classify_warning(item_flag, raw["warnings"][idx] if idx < len(raw["warnings"]) else "")
        for idx, item_flag in enumerate(raw["warning_flags"])
    ]
    audit = ExtractionAudit.model_validate(raw)
    attach_audit(doc, audit)


def classify_warning(flag: str, message: str = "") -> dict[str, Any]:
    """Return stable UI/report metadata for an extraction warning flag."""
    normalized = (flag or "").strip()
    if normalized in _AUXILIARY_FLAGS or normalized.startswith("chart_"):
        category = "auxiliary_chart"
        severity = "low"
        blocking = False
    elif normalized in _BLOCKING_FLAGS:
        category = "core_extraction"
        severity = "critical" if normalized in {"empty_document", "parser_unavailable"} else "high"
        blocking = True
    elif normalized == "ocr_used":
        category = "ocr"
        severity = "medium"
        blocking = False
    elif normalized in _SECTION_CLASSIFICATION_FLAGS:
        category = "core_extraction"
        severity = "medium"
        blocking = False
    elif normalized in _TABLE_ENGINE_FLAGS or "camelot" in normalized or "ppstructure" in normalized:
        category = "table_engine"
        severity = "medium"
        blocking = False
    else:
        category = "extraction"
        severity = "medium"
        blocking = False
    return {
        "flag": normalized,
        "message": message or "",
        "category": category,
        "severity": severity,
        "blocking": blocking,
    }


def _valid_pages(pages: Iterable[int], total_pages: int) -> list[int]:
    result: set[int] = set()
    for page in pages:
        try:
            value = int(page)
        except (TypeError, ValueError):
            continue
        if value >= 1 and (total_pages <= 0 or value <= total_pages):
            result.add(value)
    return sorted(result)


def _is_table_section_classification_gap(
    *,
    total_pages: int,
    scanned_pages: list[int],
    text_pages: list[int],
    table_pages: list[int],
    table_coverage: dict[str, Any],
) -> bool:
    if total_pages <= 0 or not scanned_pages or not text_pages:
        return False
    if len(scanned_pages) / max(total_pages, 1) < 0.98:
        return False
    if len(set(scanned_pages) - set(text_pages)) / max(len(scanned_pages), 1) > 0.1:
        return False
    table_page_count = int(table_coverage.get("table_page_count") or len(table_pages))
    substantial_threshold = max(20, int(total_pages * 0.05))
    return table_page_count >= substantial_threshold


def _append_warning(flags: list[str], notes: list[str], flag: str, message: str) -> None:
    if flag not in flags:
        flags.append(flag)
    if message not in notes:
        notes.append(message)
