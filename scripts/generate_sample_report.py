"""生成样例 PDF/Excel 报告，用于 Apple + 金融风格视觉 QA。

运行：
    python scripts/generate_sample_report.py

输出：
    storage/sample-reports/sample-report.pdf
    storage/sample-reports/sample-report.xlsx
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ahcc.config import settings
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


def _make_diffs() -> list[Diff]:
    return [
        Diff(
            diff_id="D-001",
            diff_type=DiffType.NUMERIC,
            severity=DiffSeverity.CRITICAL,
            triage="real",
            topic=LocalizedString(zh="营业收入", en="Revenue"),
            summary=LocalizedString(zh="A/H 营业收入存在重大差异", en="Revenue mismatch"),
            diff_explanation=DiffExplanation(
                headline="营业收入 A/H 披露不一致",
                issue="A 股年报列示营业收入 1,000,000 千元；H 股年报列示 985,000 千元",
                location="A 第 120 页；H 第 118 页",
                items=[
                    DiffExplanationItem(
                        label="营业收入",
                        a_value=1_000_000_000,
                        h_value=985_000_000,
                        delta=-15_000_000,
                        a_page=120,
                        h_page=118,
                        a_snippet="营业收入 1,000,000 千元",
                        h_snippet="Revenue RMB985,000 thousand",
                    )
                ],
                review_hint="优先核对分部收入抵消与汇率换算口径。",
            ),
            a_value=1_000_000_000.0,
            h_value=985_000_000.0,
            delta=-15_000_000.0,
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=120, snippet="营业收入 1,000,000 千元"),
                Evidence(side=ReportSide.H_SHARE, page=118, snippet="Revenue RMB985,000 thousand"),
            ],
        ),
        Diff(
            diff_id="D-002",
            diff_type=DiffType.CROSS_CHECK,
            severity=DiffSeverity.HIGH,
            triage="real",
            topic=LocalizedString(zh="资产负债表勾稽", en="Balance sheet cross check"),
            summary=LocalizedString(zh="总资产不等于负债及权益合计", en="Assets mismatch"),
            a_value=50_000_000_000.0,
            h_value=49_800_000_000.0,
            delta=-200_000_000.0,
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=45, snippet="总资产 500 亿"),
                Evidence(side=ReportSide.H_SHARE, page=44, snippet="Total assets 498 亿"),
            ],
        ),
        Diff(
            diff_id="D-003",
            diff_type=DiffType.DISCLOSURE,
            severity=DiffSeverity.MEDIUM,
            triage="expected",
            topic=LocalizedString(zh="研发费用资本化政策", en="R&D capitalization"),
            summary=LocalizedString(zh="A/H 研发费用资本化披露口径存在可解释差异", en="Disclosure difference"),
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=156, snippet="研究阶段费用化，开发阶段符合条件资本化"),
                Evidence(side=ReportSide.H_SHARE, page=152, snippet="Research expensed, development capitalized if criteria met"),
            ],
        ),
        Diff(
            diff_id="D-004",
            diff_type=DiffType.STANDARD,
            severity=DiffSeverity.LOW,
            triage="unresolved",
            topic=LocalizedString(zh="固定资产折旧年限", en="Depreciation period"),
            summary=LocalizedString(zh="机器设备折旧年限区间披露不一致", en="Depreciation period disclosure"),
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=98, snippet="机器设备 5-10 年"),
                Evidence(side=ReportSide.H_SHARE, page=96, snippet="Plant and machinery 5-15 years"),
            ],
        ),
        Diff(
            diff_id="D-005",
            diff_type=DiffType.CHART,
            severity=DiffSeverity.INFO,
            triage="expected",
            topic=LocalizedString(zh="营收趋势图", en="Revenue trend chart"),
            summary=LocalizedString(zh="图表与表格数据一致", en="Chart-table consistent"),
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=12, snippet="图表：营收增长 8%"),
            ],
        ),
        Diff(
            diff_id="D-006",
            diff_type=DiffType.NUMERIC,
            severity=DiffSeverity.HIGH,
            triage="real",
            topic=LocalizedString(zh="归母净利润", en="Net profit attributable"),
            summary=LocalizedString(zh="归母净利润 A/H 列示口径不一致", en="Net profit mismatch"),
            a_value=82_000_000_000.0,
            h_value=80_500_000_000.0,
            delta=-1_500_000_000.0,
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=121, snippet="归母净利润 820 亿"),
                Evidence(side=ReportSide.H_SHARE, page=119, snippet="Profit attributable 805 亿"),
            ],
        ),
        Diff(
            diff_id="D-007",
            diff_type=DiffType.DISCLOSURE,
            severity=DiffSeverity.MEDIUM,
            triage="expected",
            topic=LocalizedString(zh="关联交易披露", en="Related party disclosure"),
            summary=LocalizedString(zh="关联交易披露详略程度存在可解释差异", en="Related party disclosure depth"),
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=188, snippet="关联交易明细 12 项"),
                Evidence(side=ReportSide.H_SHARE, page=184, snippet="Related party transactions summarized"),
            ],
        ),
        Diff(
            diff_id="D-008",
            diff_type=DiffType.CROSS_CHECK,
            severity=DiffSeverity.LOW,
            triage="unresolved",
            topic=LocalizedString(zh="现金流量表勾稽", en="Cash flow cross check"),
            summary=LocalizedString(zh="期末现金及现金等价物与资产负债表存在小额差异", en="Cash reconciliation"),
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=52, snippet="期末现金 130 亿"),
                Evidence(side=ReportSide.H_SHARE, page=50, snippet="Cash at end 129.8 亿"),
            ],
        ),
    ]


def _make_coverage() -> list[DisclosureCoverageItem]:
    return [
        DisclosureCoverageItem(
            coverage_id="C-001",
            category="metric",
            status="matched",
            topic=LocalizedString(zh="员工人数"),
            a_pages=[210],
            h_pages=[208],
            match_confidence=0.98,
            note="A/H 均披露员工人数 12,500 人，匹配。",
        ),
        DisclosureCoverageItem(
            coverage_id="C-002",
            category="narrative",
            status="a_only",
            topic=LocalizedString(zh="ESG 风险披露"),
            a_pages=[245],
            h_pages=[],
            match_confidence=0.0,
            note="仅 A 股年报披露气候变化相关风险。",
        ),
    ]


def _make_profile(side: str) -> dict:
    return {
        "doc_id": side,
        "total_pages": 260,
        "metric_occurrences": 48,
        "narrative_blocks": 32,
        "extraction_audit": {
            "scanned_pages": list(range(1, 261)),
            "coverage_ratio": 0.99,
            "blank_pages": [2, 260],
            "ocr_pages": [15, 16],
            "table_pages": list(range(40, 90)),
            "warnings": [],
        },
        "metrics": [
            {
                "canonical_key": "total_assets",
                "name": {"zh": "总资产"},
                "value": 500_000_000_000.0,
                "unit": "人民币百万元",
                "page": 45,
                "evidence": {"snippet": "总资产 500,000,000 千元"},
            }
        ],
        "narratives": [
            {
                "topic_key": "business_review",
                "topic_label": "业务回顾",
                "word_count": 3500,
                "detail_level": "detailed",
                "page_range": [20, 35],
                "summary": "本年度收入实现稳健增长，主要受核心业务驱动。",
            }
        ],
    }


def main() -> None:
    job = Job(
        job_id="sample-report",
        company_name="示例金融科技股份有限公司",
        check_mode="ah",
        a_file="A-annual-report.pdf",
        h_file="H-annual-report.pdf",
        status="done",
        diffs=_make_diffs(),
        coverage_items=_make_coverage(),
        profile_a=_make_profile("A"),
        profile_h=_make_profile("H"),
        comparison_summary={
            "total_diff_count": 8,
            "real_diff_count": 3,
            "expected_diff_count": 3,
            "unresolved_diff_count": 2,
            "coverage_count": 2,
            "warning_count": 0,
            "a_fact_count": 48,
            "h_fact_count": 46,
        },
        started_at=datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 25, 9, 3, 42, tzinfo=timezone.utc),
        duration_seconds=222.5,
    )

    out_dir = settings.storage_dir / "sample-reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "sample-report.pdf"
    xlsx_path = out_dir / "sample-report.xlsx"

    export_pdf(job, pdf_path)
    export_excel(job, xlsx_path)

    print("Sample report generated:")
    print(f"  PDF:   {pdf_path.resolve()}  ({pdf_path.stat().st_size:,} bytes)")
    print(f"  Excel: {xlsx_path.resolve()}  ({xlsx_path.stat().st_size:,} bytes)")

    try:
        os.startfile(str(pdf_path))
        os.startfile(str(xlsx_path))
    except Exception:
        pass


if __name__ == "__main__":
    main()
