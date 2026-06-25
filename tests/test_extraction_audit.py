from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from ahcc.config import settings
from ahcc.parser.audit import EXTRACTION_ENGINE_VERSION, build_extraction_audit, classify_warning
from ahcc.parser import pdf_h_html
from ahcc.parser.pdf_h_html import _build_h_table_coverage
from ahcc.schemas import Language, ReportDocument, ReportSide
from ahcc.storage.repository import _attach_current_extraction_metadata, _sanitize_summary_for_loaded_job


def _cached_doc(file_path: Path, marker: str = "v1") -> ReportDocument:
    return ReportDocument(
        doc_id=f"doc-{marker}",
        side=ReportSide.H_SHARE,
        file_path=str(file_path),
        total_pages=1,
        primary_language=Language.EN,
        tables=[],
        texts=[],
        metadata={"marker": marker},
    )


@pytest.fixture
def workspace_tmp():
    path = Path("storage") / "test-artifacts" / f"parser-cache-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_low_table_page_coverage_depends_on_core_section_gaps() -> None:
    audit = build_extraction_audit(
        total_pages=352,
        scanned_pages=range(1, 353),
        text_pages=range(1, 353),
        table_pages=[12, 31, 42, 154, 164],
        warning_flags=[],
        warnings=[],
        table_coverage={
            "required_core_sections": ["bs", "pl", "cf", "equity"],
            "covered_core_sections": ["bs", "pl", "cf", "equity"],
            "missing_core_sections": [],
            "covered_key_note_sections": ["related_party"],
        },
    )

    assert "low_table_page_coverage" not in audit.warning_flags


def test_low_table_page_coverage_is_retained_when_core_sections_missing() -> None:
    audit = build_extraction_audit(
        total_pages=352,
        scanned_pages=range(1, 353),
        text_pages=range(1, 353),
        table_pages=[12, 31, 42, 154, 164],
        warning_flags=[],
        warnings=[],
        table_coverage={
            "required_core_sections": ["bs", "pl", "cf", "equity"],
            "covered_core_sections": ["bs", "pl"],
            "missing_core_sections": ["cf", "equity"],
            "covered_key_note_sections": [],
        },
    )

    assert "low_table_page_coverage" in audit.warning_flags
    assert any("missing structured sections" in message for message in audit.warnings)


def test_table_section_classification_gap_is_not_blocking_when_tables_are_substantial() -> None:
    audit = build_extraction_audit(
        total_pages=450,
        scanned_pages=range(1, 451),
        text_pages=range(1, 451),
        table_pages=range(1, 88),
        warning_flags=[],
        warnings=[],
        table_coverage={
            "required_core_sections": ["bs", "pl", "cf", "equity"],
            "covered_core_sections": [],
            "missing_core_sections": ["bs", "cf", "equity", "pl"],
            "covered_key_note_sections": ["related_party"],
            "table_page_count": 87,
        },
    )

    assert "low_table_page_coverage" not in audit.warning_flags
    assert "table_section_classification_gap" in audit.warning_flags
    details = {item["flag"]: item for item in audit.engines["warning_details"]}
    assert details["table_section_classification_gap"]["blocking"] is False


def test_engine_diagnostics_are_not_promoted_to_user_warnings() -> None:
    audit = build_extraction_audit(
        total_pages=10,
        scanned_pages=range(1, 11),
        text_pages=range(1, 11),
        table_pages=[2, 3, 4],
        warning_flags=[],
        warnings=[],
        engines={"camelot": {"attempted": True, "pages": [2, 3], "added_tables": 0}},
        table_coverage={
            "required_core_sections": ["bs", "pl", "cf", "equity"],
            "covered_core_sections": ["bs", "pl", "cf", "equity"],
            "missing_core_sections": [],
            "covered_key_note_sections": [],
        },
    )

    assert "camelot_no_tables" not in audit.warning_flags
    assert audit.engines["camelot"]["added_tables"] == 0
    assert "table_coverage" in audit.engines


def test_audit_records_engine_version_and_warning_classification() -> None:
    audit = build_extraction_audit(
        total_pages=2,
        scanned_pages=[1],
        text_pages=[1],
        table_pages=[],
    )

    assert audit.engines["extraction_engine_version"] == EXTRACTION_ENGINE_VERSION
    details = {item["flag"]: item for item in audit.engines["warning_details"]}
    assert details["page_scan_incomplete"]["blocking"] is True
    assert details["no_tables_extracted"]["category"] == "core_extraction"


def test_chart_warning_is_auxiliary_not_blocking() -> None:
    detail = classify_warning("chart_detection_failed", "locked PNG")

    assert detail["category"] == "auxiliary_chart"
    assert detail["blocking"] is False


def test_h_table_coverage_infers_core_sections_from_page_text() -> None:
    coverage = _build_h_table_coverage(
        texts=[],
        table_pages={10, 11, 12, 13},
        page_texts={
            10: "Consolidated Statement of Financial Position total assets total liabilities total equity",
            11: "Consolidated Statement of Profit or Loss revenue profit for the year earnings per share",
            12: "Consolidated Statement of Cash Flows cash flows from operating activities investing activities",
            13: "Consolidated Statement of Changes in Equity share capital reserves retained profits",
        },
    )

    assert coverage["covered_core_sections"] == ["bs", "cf", "equity", "pl"]
    assert coverage["missing_core_sections"] == []
    assert coverage["inferred_core_sections"] == ["bs", "cf", "equity", "pl"]
    assert coverage["inference_sources"]["bs"] == [10]


def test_h_table_coverage_does_not_infer_core_sections_from_financial_notes() -> None:
    coverage = _build_h_table_coverage(
        texts=[],
        table_pages={20},
        page_texts={
            20: "Cash and bank balances interest rates 0.9% 1.8% 2.85% deposits at banks",
        },
    )

    assert coverage["inferred_core_sections"] == []
    assert coverage["missing_core_sections"] == ["bs", "cf", "equity", "pl"]


def test_loaded_old_summary_is_marked_stale() -> None:
    summary = _attach_current_extraction_metadata({"result_version": 3, "warnings": []})

    assert summary["stale_result"] is True
    assert summary["current_extraction_engine_version"] == EXTRACTION_ENGINE_VERSION
    assert summary["warnings"][0]["flag"] == "stale_extraction_engine"


def test_loaded_current_summary_is_not_marked_stale() -> None:
    summary = _attach_current_extraction_metadata(
        {
            "result_version": 3,
            "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "warnings": [],
        }
    )

    assert summary["stale_result"] is False
    assert summary["warning_count"] == 0


def test_loaded_history_downgrades_legacy_section_classification_gap_warning() -> None:
    summary = _attach_current_extraction_metadata(
        {
            "result_version": 10,
            "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "warnings": [
                {
                    "side": "H",
                    "flag": "low_table_page_coverage",
                    "message": "Core table extraction incomplete; missing structured sections: bs, cf, equity, pl.",
                    "category": "core_extraction",
                    "severity": "high",
                    "blocking": True,
                    "total_pages": 450,
                    "scanned_pages": 450,
                    "missing_pages": 0,
                    "blank_pages": 0,
                    "ocr_pages": 0,
                    "table_pages": 87,
                    "coverage_ratio": 1.0,
                }
            ],
        }
    )

    assert summary["warnings"][0]["flag"] == "table_section_classification_gap"
    assert summary["warnings"][0]["blocking"] is False
    assert summary["blocking_warning_count"] == 0


def test_loaded_legacy_h_bilingual_summary_requires_rerun_not_5000_real_diffs() -> None:
    summary = _sanitize_summary_for_loaded_job(
        "legacy-h-bilingual",
        {
            "result_version": 9,
            "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "check_mode": "h_bilingual",
            "real_diff_count": 5228,
            "unresolved_diff_count": 56,
            "total_diff_count": 5284,
            "coverage_count": 5132,
            "layout_diff_count": 5132,
            "table_row_diff_count": 5060,
            "paragraph_unpaired_count": 56,
            "numeric_diff_count": 152,
            "warning_count": 2,
            "warnings": [],
        },
    )

    assert summary["result_version"] == 11
    assert summary["stale_result"] is True
    assert summary["legacy_result_sanitized"] is True
    assert summary["legacy_result_requires_rerun"] is True
    assert summary["real_diff_count"] == 0
    assert summary["coverage_count"] == 0
    assert summary["table_row_diff_count"] == 0
    assert summary["numeric_diff_count"] == 0
    assert summary["total_diff_count"] == 0
    assert summary["warnings"][0]["flag"] == "stale_h_bilingual_result"


def test_h_pdf_parser_cache_hits_for_same_file(monkeypatch, workspace_tmp) -> None:
    pdf_path = workspace_tmp / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-same")
    calls: list[str] = []

    def fake_parse(file_path: str) -> ReportDocument:
        calls.append(file_path)
        return _cached_doc(Path(file_path), marker=str(len(calls)))

    monkeypatch.setattr(settings, "storage_dir", workspace_tmp / "storage")
    monkeypatch.setattr(pdf_h_html, "_parse_h_pdf", fake_parse)

    first = pdf_h_html.parse_h_pdf(str(pdf_path))
    second = pdf_h_html.parse_h_pdf(str(pdf_path))

    assert len(calls) == 1
    assert first.metadata["parser_cache"]["hit"] is False
    assert second.metadata["parser_cache"]["hit"] is True
    assert second.metadata["marker"] == "1"


def test_h_pdf_parser_cache_invalidates_when_file_changes(monkeypatch, workspace_tmp) -> None:
    pdf_path = workspace_tmp / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-v1")
    calls: list[str] = []

    def fake_parse(file_path: str) -> ReportDocument:
        calls.append(file_path)
        return _cached_doc(Path(file_path), marker=str(len(calls)))

    monkeypatch.setattr(settings, "storage_dir", workspace_tmp / "storage")
    monkeypatch.setattr(pdf_h_html, "_parse_h_pdf", fake_parse)

    first = pdf_h_html.parse_h_pdf(str(pdf_path))
    pdf_path.write_bytes(b"%PDF-v2")
    second = pdf_h_html.parse_h_pdf(str(pdf_path))

    assert len(calls) == 2
    assert first.metadata["parser_cache"]["hit"] is False
    assert second.metadata["parser_cache"]["hit"] is False
    assert second.metadata["marker"] == "2"


def test_h_pdf_parser_cache_corrupt_json_falls_back_to_parse(monkeypatch, workspace_tmp) -> None:
    pdf_path = workspace_tmp / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-same")
    calls: list[str] = []

    def fake_parse(file_path: str) -> ReportDocument:
        calls.append(file_path)
        return _cached_doc(Path(file_path), marker=str(len(calls)))

    monkeypatch.setattr(settings, "storage_dir", workspace_tmp / "storage")
    monkeypatch.setattr(pdf_h_html, "_parse_h_pdf", fake_parse)

    pdf_h_html.parse_h_pdf(str(pdf_path))
    cache_files = list((settings.storage_dir / "parser_cache" / "h_pdf").glob("*.json"))
    assert cache_files
    cache_files[0].write_text("{bad json", encoding="utf-8")

    second = pdf_h_html.parse_h_pdf(str(pdf_path))

    assert len(calls) == 2
    assert second.metadata["parser_cache"]["hit"] is False
    assert second.metadata["marker"] == "2"


def test_pages_with_zero_segments_are_flagged() -> None:
    """扫描到文本但经过过滤后没有保留 segment 的页面应被标记。"""
    from ahcc.schemas import TextSegment

    segments = [
        TextSegment(segment_id="s1", page=1, bbox=(0, 0, 1, 1), text="Valid paragraph.", language=Language.ZH, section="notes"),
        TextSegment(segment_id="s2", page=3, bbox=(0, 0, 1, 1), text="Another valid paragraph.", language=Language.ZH, section="notes"),
    ]
    audit = build_extraction_audit(
        total_pages=3,
        scanned_pages=[1, 2, 3],
        text_pages=[1, 2, 3],  # 第 2 页被判定有原始文本
        table_pages=[],
        text_segments=segments,  # 但过滤后第 2 页没有保留 segment
        warning_flags=[],
        warnings=[],
    )

    assert audit.pages_with_zero_segments == [2]
    assert "pages_with_zero_segments" in audit.warning_flags
    assert any("[2]" in message for message in audit.warnings)


def test_pages_with_zero_segments_ignore_blank_pages() -> None:
    """已被标记为 blank 的页面不应重复进入 pages_with_zero_segments。"""
    from ahcc.schemas import TextSegment

    segments = [
        TextSegment(segment_id="s1", page=1, bbox=(0, 0, 1, 1), text="Valid paragraph.", language=Language.ZH, section="notes"),
    ]
    audit = build_extraction_audit(
        total_pages=2,
        scanned_pages=[1, 2],
        text_pages=[1],  # 第 2 页是 blank
        table_pages=[],
        text_segments=segments,
        warning_flags=[],
        warnings=[],
    )

    assert audit.blank_pages == [2]
    assert audit.pages_with_zero_segments == []
    assert "pages_with_zero_segments" not in audit.warning_flags

