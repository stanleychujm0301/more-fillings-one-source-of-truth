from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from openpyxl import load_workbook

from ahcc.report.excel import export_excel
from ahcc.report.pdf import export_pdf
from ahcc.schemas import (
    Diff,
    DiffExplanation,
    DiffExplanationItem,
    DiffSeverity,
    DiffType,
    DisclosureCoverageItem,
    Evidence,
    Job,
    LocalizedString,
    ReportSide,
)


def _explained_diff() -> Diff:
    return Diff(
        diff_id="d-explained",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        topic=LocalizedString(zh="利润分配", en="Profit distribution"),
        summary=LocalizedString(zh="事件事实不一致：利润分配", en="Event mismatch"),
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
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=453, snippet="共计股利人民币2,503,994千元"),
            Evidence(side=ReportSide.H_SHARE, page=453, snippet="total dividends amounting to RMB25,039,945 thousand"),
        ],
        rule_id="event_fact_match",
    )


def test_excel_export_strips_pdf_control_characters() -> None:
    dirty_text = "中国平安\x01综合金融模式\x02覆盖330个网点"
    job = Job(
        job_id="j-dirty",
        a_file="a.pdf",
        h_file="h.pdf",
        diffs=[
            Diff(
                diff_id="d-1",
                diff_type=DiffType.DISCLOSURE,
                severity=DiffSeverity.MEDIUM,
                triage="real",
                topic=LocalizedString(zh="经营情况"),
                summary=LocalizedString(zh=dirty_text),
                evidence=[
                    Evidence(side=ReportSide.A_SHARE, page=13, snippet=dirty_text),
                    Evidence(side=ReportSide.H_SHARE, page=15, snippet=dirty_text),
                ],
            )
        ],
        coverage_items=[
            DisclosureCoverageItem(
                coverage_id="c-1",
                category="event",
                status="matched",
                topic=LocalizedString(zh="渠道覆盖"),
                note=dirty_text,
            )
        ],
        profile_a={
            "doc_id": "A",
            "total_pages": 10,
            "metrics": [
                {
                    "canonical_key": "branch_count",
                    "name": {"zh": "网点数量"},
                    "value": 330,
                    "page": 13,
                    "evidence": {"snippet": dirty_text},
                }
            ],
        },
        profile_h={
            "doc_id": "H",
            "total_pages": 10,
            "narratives": [
                {
                    "topic_key": "operations",
                    "topic_label": "经营情况",
                    "word_count": 20,
                    "page_range": [15, 15],
                    "summary": dirty_text,
                }
            ],
        },
        comparison_summary={
            "warnings": [{"side": "H", "flag": "dirty_text", "message": dirty_text}],
        },
    )
    out_dir = Path("storage") / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report-{uuid4().hex}.xlsx"

    try:
        export_excel(job, out_path)

        workbook = load_workbook(out_path, read_only=True)
        try:
            illegal_chars = {"\x01", "\x02"}
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for value in row:
                        if isinstance(value, str):
                            assert not any(char in value for char in illegal_chars)
        finally:
            workbook.close()
    finally:
        if out_path.exists():
            try:
                out_path.unlink()
            except PermissionError:
                pass


def test_excel_export_writes_diff_explanation_columns() -> None:
    job = Job(job_id="j-explained", a_file="a.pdf", h_file="h.pdf", diffs=[_explained_diff()])
    out_dir = Path("storage") / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report-{uuid4().hex}.xlsx"

    try:
        export_excel(job, out_path)

        workbook = load_workbook(out_path, read_only=True)
        try:
            ws = workbook.active
            headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
            row = [cell.value for cell in next(ws.iter_rows(min_row=2, max_row=2))]
            assert "差异说明" in headers
            assert "A定位与取值" in headers
            assert "H定位与取值" in headers
            assert "审阅提示" in headers
            assert any("利润分配股利总额不一致" in str(value) for value in row)
            assert any("A 第453页" in str(value) and "2,503,994" in str(value) for value in row)
            assert any("H 第453页" in str(value) and "25,039,945" in str(value) for value in row)
        finally:
            workbook.close()
    finally:
        if out_path.exists():
            try:
                out_path.unlink()
            except PermissionError:
                pass


def test_excel_export_uses_bilingual_side_headers() -> None:
    job = Job(
        job_id="j-bilingual-export",
        check_mode="h_bilingual",
        a_file="zh.pdf",
        h_file="en.pdf",
        diffs=[_explained_diff()],
    )
    out_dir = Path("storage") / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report-{uuid4().hex}.xlsx"

    try:
        export_excel(job, out_path)

        workbook = load_workbook(out_path, read_only=True)
        try:
            ws = workbook["差异清单"]
            headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
            assert "中文定位与取值" in headers
            assert "英文定位与取值" in headers
        finally:
            workbook.close()
    finally:
        if out_path.exists():
            try:
                out_path.unlink()
            except PermissionError:
                pass


def test_pdf_export_accepts_diff_explanation() -> None:
    job = Job(job_id="j-pdf-explained", a_file="a.pdf", h_file="h.pdf", diffs=[_explained_diff()])
    out_dir = Path("storage") / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report-{uuid4().hex}.pdf"

    try:
        export_pdf(job, out_path)

        assert out_path.exists()
        assert out_path.stat().st_size > 0
    finally:
        if out_path.exists():
            try:
                out_path.unlink()
            except PermissionError:
                pass


def _sample_diffs() -> list[Diff]:
    """构造覆盖各级别严重度与分流的差异，用于视觉冒烟测试。"""
    return [
        Diff(
            diff_id="d-critical",
            diff_type=DiffType.NUMERIC,
            severity=DiffSeverity.CRITICAL,
            triage="real",
            topic=LocalizedString(zh="营业收入"),
            summary=LocalizedString(zh="A/H 营业收入存在重大差异"),
            a_value=100_000_000.0,
            h_value=98_500_000.0,
            delta=-1_500_000.0,
            evidence=[Evidence(side=ReportSide.A_SHARE, page=12, snippet="营收 1 亿")],
        ),
        Diff(
            diff_id="d-high",
            diff_type=DiffType.CROSS_CHECK,
            severity=DiffSeverity.HIGH,
            triage="real",
            topic=LocalizedString(zh="总资产勾稽"),
            summary=LocalizedString(zh="总资产不等于负债加权益"),
            evidence=[Evidence(side=ReportSide.A_SHARE, page=34, snippet="总资产 50 亿")],
        ),
        Diff(
            diff_id="d-medium",
            diff_type=DiffType.DISCLOSURE,
            severity=DiffSeverity.MEDIUM,
            triage="expected",
            topic=LocalizedString(zh="研发费用资本化"),
            summary=LocalizedString(zh="披露口径差异"),
            evidence=[Evidence(side=ReportSide.H_SHARE, page=56, snippet="R&D")],
        ),
        Diff(
            diff_id="d-low",
            diff_type=DiffType.STANDARD,
            severity=DiffSeverity.LOW,
            triage="unresolved",
            topic=LocalizedString(zh="折旧年限"),
            summary=LocalizedString(zh="年限披露差异"),
            evidence=[Evidence(side=ReportSide.A_SHARE, page=78, snippet="5 年")],
        ),
    ]


def test_pdf_excel_smoke_generates_files() -> None:
    """导出 PDF/Excel 样例，验证文件非空且可加载。"""
    job = Job(
        job_id="j-smoke",
        company_name="示例股份",
        a_file="a.pdf",
        h_file="h.pdf",
        diffs=_sample_diffs(),
        coverage_items=[
            DisclosureCoverageItem(
                coverage_id="c-1",
                category="metric",
                status="matched",
                topic=LocalizedString(zh="员工人数"),
                note="匹配",
            )
        ],
        comparison_summary={
            "total_diff_count": 4,
            "real_diff_count": 2,
            "expected_diff_count": 1,
            "unresolved_diff_count": 1,
            "coverage_count": 1,
            "warning_count": 0,
        },
    )
    out_dir = Path("storage") / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"report-smoke-{uuid4().hex}.pdf"
    xlsx_path = out_dir / f"report-smoke-{uuid4().hex}.xlsx"

    try:
        export_pdf(job, pdf_path)
        export_excel(job, xlsx_path)
        assert pdf_path.exists() and pdf_path.stat().st_size > 0
        assert xlsx_path.exists() and xlsx_path.stat().st_size > 0

        workbook = load_workbook(xlsx_path, read_only=True)
        try:
            assert "核查总览" in workbook.sheetnames
            assert "差异清单" in workbook.sheetnames
        finally:
            workbook.close()
    finally:
        for p in (pdf_path, xlsx_path):
            if p.exists():
                try:
                    p.unlink()
                except PermissionError:
                    pass


def test_excel_conditional_formatting_applied() -> None:
    """检查「差异」列是否应用了数据条条件格式。"""
    job = Job(job_id="j-databar", a_file="a.pdf", h_file="h.pdf", diffs=_sample_diffs())
    out_dir = Path("storage") / "test-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report-databar-{uuid4().hex}.xlsx"

    try:
        export_excel(job, out_path)
        workbook = load_workbook(out_path)
        try:
            ws = workbook["差异清单"]
            delta_col = None
            for idx, cell in enumerate(ws[1], start=1):
                if cell.value == "差异":
                    delta_col = idx
                    break
            assert delta_col is not None
            assert len(ws.conditional_formatting) > 0
        finally:
            workbook.close()
    finally:
        if out_path.exists():
            try:
                out_path.unlink()
            except PermissionError:
                pass
