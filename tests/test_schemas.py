"""schemas.py 单元测试 — 确保数据契约可正常序列化/反序列化。"""

from __future__ import annotations

from ahcc.schemas import (
    DataPoint,
    Diff,
    DiffExplanation,
    DiffExplanationItem,
    DiffSeverity,
    DiffType,
    Evidence,
    ExtractionAudit,
    Job,
    JobStatus,
    LocalizedString,
    ReportSide,
)


def test_localized_string_best():
    ls = LocalizedString(zh="资产总计", en="Total assets")
    assert ls.best() == "资产总计"
    assert LocalizedString(en="Total assets").best() == "Total assets"
    assert LocalizedString().best() == ""


def test_diff_serialization():
    diff = Diff(
        diff_id="d-001",
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.CRITICAL,
        topic=LocalizedString(zh="资产总计", en="Total assets"),
        summary=LocalizedString(zh="差异 100 万", en="Delta 1M"),
        a_value=100_000_000.0,
        h_value=99_000_000.0,
        delta=1_000_000.0,
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=45, snippet="资产总计 1 亿"),
            Evidence(side=ReportSide.H_SHARE, page=38, snippet="Total assets 99M"),
        ],
    )
    payload = diff.model_dump_json()
    restored = Diff.model_validate_json(payload)
    assert restored.diff_id == "d-001"
    assert len(restored.evidence) == 2
    assert restored.evidence[0].page == 45


def test_diff_explanation_is_optional_and_serialized():
    legacy_diff = Diff(
        diff_id="d-legacy",
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.MEDIUM,
        topic=LocalizedString(zh="营业收入", en="Revenue"),
        summary=LocalizedString(zh="旧版差异", en="Legacy diff"),
    )
    assert legacy_diff.diff_explanation is None

    diff = Diff(
        diff_id="d-explained",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        topic=LocalizedString(zh="利润分配", en="Profit distribution"),
        summary=LocalizedString(zh="利润分配股利总额不一致", en="Dividend total mismatch"),
        diff_explanation=DiffExplanation(
            headline="利润分配股利总额不一致",
            issue="A 披露共计股利人民币2,503,994千元；H 披露 RMB25,039,945 thousand",
            location="A 第453页；H 第453页",
            items=[
                DiffExplanationItem(
                    label="股利总额",
                    role="dividend_total",
                    a_value=2_503_994_000,
                    h_value=25_039_945_000,
                    delta=22_535_951_000,
                    a_page=453,
                    h_page=453,
                    a_snippet="共计股利人民币2,503,994千元",
                    h_snippet="total dividends amounting to RMB25,039,945 thousand",
                )
            ],
            review_hint="优先核对利润分配附注中的股利总额。",
        ),
    )

    restored = Diff.model_validate_json(diff.model_dump_json())

    assert restored.diff_explanation is not None
    assert restored.diff_explanation.headline == "利润分配股利总额不一致"
    assert restored.diff_explanation.items[0].role == "dividend_total"
    assert restored.diff_explanation.items[0].a_page == 453


def test_data_point_evidence_required():
    """每个 DataPoint 必须有 evidence — 这是项目硬约束。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DataPoint(
            name=LocalizedString(zh="资产", en="Assets"),
            canonical_key="total_assets",
            value=100.0,
            # evidence 缺失
        )


def test_legacy_aligning_status_maps_to_profiling():
    assert JobStatus("aligning") == JobStatus.PROFILING

    job = Job.model_validate(
        {
            "job_id": "j-legacy",
            "a_file": "a.pdf",
            "h_file": "h.pdf",
            "status": "aligning",
            "progress": [{"stage": "aligning", "percent": 40, "message": "旧任务进度"}],
        }
    )

    assert job.status == JobStatus.PROFILING
    assert job.progress[0].stage == JobStatus.PROFILING


def test_job_company_name_is_optional_and_serialized():
    legacy_job = Job(job_id="j-legacy", a_file="a.pdf", h_file="h.pdf")
    assert legacy_job.company_name is None
    assert legacy_job.check_mode == "ah"

    job = Job(job_id="j-company", company_name="招商证券", a_file="a.pdf", h_file="h.pdf")
    assert job.model_dump()["company_name"] == "招商证券"


def test_job_check_mode_serializes_h_bilingual():
    job = Job(job_id="j-bilingual", check_mode="h_bilingual", a_file="zh.pdf", h_file="en.pdf")

    assert job.model_dump()["check_mode"] == "h_bilingual"


def test_extraction_audit_serialization():
    audit = ExtractionAudit(
        total_pages=3,
        scanned_pages=[1, 2, 3],
        blank_pages=[2],
        table_pages=[3],
        coverage_ratio=1.0,
        warning_flags=["many_blank_pages"],
        warnings=["Page 2 produced no text."],
        engines={"text": "unit-test"},
    )

    restored = ExtractionAudit.model_validate_json(audit.model_dump_json())

    assert restored.total_pages == 3
    assert restored.scanned_pages == [1, 2, 3]
    assert restored.warning_flags == ["many_blank_pages"]
    assert restored.engines["text"] == "unit-test"
