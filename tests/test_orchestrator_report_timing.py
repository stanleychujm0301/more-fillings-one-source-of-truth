"""回归：报告生成时，核查耗时/汇总必须已就绪（修复「核查耗时」空白的根因）。

根因是 _build_report 曾在 finished_at/duration_seconds/comparison_summary 之前调用，
导致预生成的 PDF/Excel 落盘时这些值仍为 None/空。这里用打桩在报告生成瞬间捕获 job 状态。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ahcc.config import settings
from ahcc.orchestrator import Orchestrator
from ahcc.schemas import Language, ReportDocument, ReportSide


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

    def fake_summary(self, job, a, b, *, module_warnings=None):
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
    assert captured["summary"] == {"_built": True}
    # 任务结束后字段仍在
    assert job.duration_seconds is not None
    assert job.status.value == "done"


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
