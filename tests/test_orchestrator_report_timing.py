"""回归：报告生成时，核查耗时/汇总必须已就绪（修复「核查耗时」空白的根因）。

根因是 _build_report 曾在 finished_at/duration_seconds/comparison_summary 之前调用，
导致预生成的 PDF/Excel 落盘时这些值仍为 None/空。这里用打桩在报告生成瞬间捕获 job 状态。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from ahcc.config import settings
from ahcc.llm.client import record_llm_failure
from ahcc.orchestrator import Orchestrator
from ahcc.schemas import Job, Language, ReportDocument, ReportSide


def _doc(doc_id: str) -> ReportDocument:
    return ReportDocument(
        doc_id=doc_id,
        side=ReportSide.H_SHARE,
        file_path=f"{doc_id}.pdf",
        total_pages=1,
        primary_language=Language.ZH,
        texts=[],
    )


def test_ah_report_generated_after_duration_and_summary(monkeypatch):
    """A+H 主路径：_build_report 调用瞬间 duration_seconds 与 comparison_summary 已就绪。"""
    captured: dict[str, object] = {}

    monkeypatch.setattr(settings, "demo_mode", True)  # 跳过图表检测/核对，简化打桩

    async def fake_parse(self, file_path, side):
        return _doc(file_path)

    async def fake_build_profile(self, doc):
        return SimpleNamespace(profile_summary={}, metrics=[], narratives=[])

    async def fake_numeric(self, a, b):
        return []

    async def fake_standard(self, a, b):
        return []

    async def fake_disclosure(self, a, b):
        return []

    async def fake_coverage(self, a, b):
        return ([], [])

    def fake_summary(self, job, a, b, *, visual_review_mode="smart", module_warnings=None):
        return {"_built": True}

    async def fake_build_report(self, job):
        captured["duration"] = job.duration_seconds
        captured["finished_at"] = job.finished_at
        captured["summary"] = dict(job.comparison_summary)

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_profile", fake_build_profile)
    monkeypatch.setattr(Orchestrator, "_check_numeric_profiles", fake_numeric)
    monkeypatch.setattr(Orchestrator, "_check_standard_profiles", fake_standard)
    monkeypatch.setattr(Orchestrator, "_check_disclosure_profiles", fake_disclosure)
    monkeypatch.setattr(Orchestrator, "_build_disclosure_coverage", fake_coverage)
    monkeypatch.setattr(Orchestrator, "_build_comparison_summary", fake_summary)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)

    job = asyncio.run(Orchestrator().run("a.pdf", "h.pdf", company_name="X", check_mode="ah"))

    # 报告生成瞬间：耗时已结算、汇总已构建
    assert captured["duration"] is not None and captured["duration"] >= 0
    assert captured["finished_at"] is not None
    assert captured["summary"]["_built"] is True
    assert captured["summary"]["visual_ocr_status"]["mode"] == "off"
    # 任务结束后字段仍在
    assert job.duration_seconds is not None
    assert job.status.value == "done"


def test_llm_call_failures_are_summarized_into_module_warnings(monkeypatch):
    """B10: cached_call 失败会静默返回 {}，语义复核/差异比对实际空转，但界面此前没有任何
    警示。任何在本任务运行期间被记录的 LLM 失败（ahcc.llm.client.record_llm_failure）都必须
    在报告结算前被汇总进 module_warnings，传给 _build_comparison_summary。"""
    captured: dict[str, object] = {}
    monkeypatch.setattr(settings, "demo_mode", True)

    async def fake_parse(self, file_path, side):
        return _doc(file_path)

    async def fake_build_profile(self, doc):
        return SimpleNamespace(profile_summary={}, metrics=[], narratives=[])

    async def fake_numeric(self, a, b):
        # 模拟数值检查内部某次 cached_call 失败（比如 API Key 未配置）。
        record_llm_failure("llm-fail-job", "deepseek conn error")
        return []

    async def fake_standard(self, a, b):
        return []

    async def fake_disclosure(self, a, b):
        return []

    async def fake_coverage(self, a, b):
        return ([], [])

    def fake_summary(self, job, a, b, *, visual_review_mode="smart", module_warnings=None):
        captured["module_warnings"] = list(module_warnings or [])
        return {"_built": True}

    async def fake_build_report(self, job):
        return None

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_profile", fake_build_profile)
    monkeypatch.setattr(Orchestrator, "_check_numeric_profiles", fake_numeric)
    monkeypatch.setattr(Orchestrator, "_check_standard_profiles", fake_standard)
    monkeypatch.setattr(Orchestrator, "_check_disclosure_profiles", fake_disclosure)
    monkeypatch.setattr(Orchestrator, "_build_disclosure_coverage", fake_coverage)
    monkeypatch.setattr(Orchestrator, "_build_comparison_summary", fake_summary)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)

    job = Job(job_id="llm-fail-job", a_file="a.pdf", h_file="h.pdf")

    asyncio.run(Orchestrator().run("a.pdf", "h.pdf", company_name="X", check_mode="ah", job=job))

    warnings = captured["module_warnings"]
    assert any(w.get("flag") == "llm_calls_failed" for w in warnings)
    matching = [w for w in warnings if w.get("flag") == "llm_calls_failed"]
    assert "1" in matching[0]["message"]
    assert "LLM" in matching[0]["message"]


def test_bilingual_report_generated_after_duration_and_summary(monkeypatch):
    """双语路径：报告生成瞬间 duration/summary 已就绪，且 report_seconds 报告后回填进 summary。"""
    captured: dict[str, object] = {}

    async def fake_parse(self, file_path, side):
        return _doc(file_path)

    async def fake_build_report(self, job):
        captured["duration"] = job.duration_seconds
        captured["summary_mode"] = job.comparison_summary.get("check_mode")

    def fake_run_bilingual_checks(zh_doc, en_doc, *, semantic_evaluator=None, enable_semantic=False):
        from ahcc.check.bilingual import BilingualCheckResult

        return BilingualCheckResult(stats={})

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)
    monkeypatch.setattr("ahcc.check.bilingual.run_bilingual_checks", fake_run_bilingual_checks)

    job = asyncio.run(
        Orchestrator().run("zh.pdf", "en.pdf", check_mode="h_bilingual")
    )

    # 报告生成瞬间：耗时已结算、汇总已构建（check_mode 可取到）
    assert captured["duration"] is not None
    assert captured["summary_mode"] == "h_bilingual"
    # report_seconds 经 phase_timings 引用在报告后回填进 summary
    assert "report_seconds" in job.comparison_summary["phase_timings"]


def test_chart_detection_uses_configured_page_cap(monkeypatch):
    """Chart detection is auxiliary and must not scan every page before core checks can finish."""
    calls: list[int | None] = []

    def fake_detect_charts(pdf_path, out_dir, max_pages=None):
        calls.append(max_pages)
        return []

    monkeypatch.setattr(settings, "chart_detection_max_pages", 24, raising=False)
    monkeypatch.setattr("ahcc.parser.chart_detect.detect_charts", fake_detect_charts)

    pdf = Path("storage/test-artifacts/chart-cap-sample.pdf")
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path=str(pdf),
        total_pages=330,
        primary_language=Language.ZH,
        texts=[],
    )

    result = asyncio.run(Orchestrator()._detect_charts(doc, "chart-cap-job"))

    assert result.charts == []
    assert calls == [24]


def test_chart_vlm_pipeline_is_opt_in_and_does_not_block_core_result(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "enable_chart_vlm_check", False)

    async def fake_parse(self, file_path, side):
        return _doc(file_path)

    async def fail_detect_charts(self, doc, job_id):
        raise AssertionError("chart detection should be skipped unless chart VLM is enabled")

    async def fake_build_profile(self, doc):
        return SimpleNamespace(profile_summary={}, metrics=[], narratives=[])

    async def fake_numeric(self, a, b):
        return []

    async def fake_standard(self, a, b):
        return []

    async def fake_disclosure(self, a, b):
        return []

    async def fake_tamper(self, a, b, *, visual_review_mode="smart"):
        return []

    async def fake_coverage(self, a, b):
        return ([], [])

    async def fail_chart_check(self, a, b):
        raise AssertionError("chart VLM check should be skipped unless explicitly enabled")

    async def fake_build_report(self, job):
        return None

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_detect_charts", fail_detect_charts)
    monkeypatch.setattr(Orchestrator, "_build_profile", fake_build_profile)
    monkeypatch.setattr(Orchestrator, "_check_numeric_profiles", fake_numeric)
    monkeypatch.setattr(Orchestrator, "_check_standard_profiles", fake_standard)
    monkeypatch.setattr(Orchestrator, "_check_disclosure_profiles", fake_disclosure)
    monkeypatch.setattr(Orchestrator, "_check_key_metric_tamper", fake_tamper)
    monkeypatch.setattr(Orchestrator, "_build_disclosure_coverage", fake_coverage)
    monkeypatch.setattr(Orchestrator, "_check_chart", fail_chart_check)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)

    job = asyncio.run(Orchestrator().run("a.pdf", "h.pdf", company_name="X", check_mode="ah"))

    assert job.status.value == "done"


def test_auxiliary_semantic_checks_are_opt_in_and_do_not_block_core_result(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "enable_standard_check", False)
    monkeypatch.setattr(settings, "enable_disclosure_coverage_check", False)
    monkeypatch.setattr(settings, "enable_chart_vlm_check", False)

    async def fake_parse(self, file_path, side):
        return _doc(file_path)

    async def fake_build_profile(self, doc):
        return SimpleNamespace(profile_summary={}, metrics=[], narratives=[])

    async def fake_numeric(self, a, b):
        return []

    async def fail_standard(self, a, b):
        raise AssertionError("standard RAG should be skipped unless explicitly enabled")

    async def fake_disclosure(self, a, b):
        return []

    async def fake_tamper(self, a, b, *, visual_review_mode="smart"):
        return []

    async def fail_coverage(self, a, b):
        raise AssertionError("disclosure coverage should be skipped unless explicitly enabled")

    async def fake_build_report(self, job):
        return None

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_profile", fake_build_profile)
    monkeypatch.setattr(Orchestrator, "_check_numeric_profiles", fake_numeric)
    monkeypatch.setattr(Orchestrator, "_check_standard_profiles", fail_standard)
    monkeypatch.setattr(Orchestrator, "_check_disclosure_profiles", fake_disclosure)
    monkeypatch.setattr(Orchestrator, "_check_key_metric_tamper", fake_tamper)
    monkeypatch.setattr(Orchestrator, "_build_disclosure_coverage", fail_coverage)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)

    job = asyncio.run(Orchestrator().run("a.pdf", "h.pdf", company_name="X", check_mode="ah"))

    assert job.status.value == "done"
    assert job.coverage_items == []
