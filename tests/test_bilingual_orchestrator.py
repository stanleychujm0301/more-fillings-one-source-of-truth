from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

from ahcc.orchestrator import Orchestrator
from ahcc.config import settings
from ahcc.schemas import Language, ReportDocument, ReportSide
from ahcc.storage import models
from ahcc.storage.repository import save_job


def _doc(doc_id: str) -> ReportDocument:
    return ReportDocument(
        doc_id=doc_id,
        side=ReportSide.H_SHARE,
        file_path=f"{doc_id}.pdf",
        total_pages=1,
        primary_language=Language.ZH,
        texts=[],
    )


def test_h_bilingual_orchestrator_uses_h_parser_and_skips_profiles(monkeypatch):
    parsed_sides: list[ReportSide] = []
    profile_called = False
    captured_check: dict[str, object] = {}

    async def fake_parse(self, file_path: str, side: ReportSide):
        parsed_sides.append(side)
        return _doc(file_path)

    async def fail_build_profile(self, doc):
        nonlocal profile_called
        profile_called = True
        raise AssertionError("h_bilingual mode must not build profiles")

    async def fake_build_report(self, job):
        return None

    def fake_run_bilingual_checks(zh_doc, en_doc, *, semantic_evaluator=None, enable_semantic=False):
        captured_check["semantic_evaluator"] = semantic_evaluator
        captured_check["enable_semantic"] = enable_semantic
        from ahcc.check.bilingual import BilingualCheckResult

        return BilingualCheckResult(stats={})

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_profile", fail_build_profile)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)
    monkeypatch.setattr("ahcc.check.bilingual.run_bilingual_checks", fake_run_bilingual_checks)

    job = asyncio.run(
        Orchestrator().run(
            "h-zh.pdf",
            "h-en.pdf",
            company_name="申万宏源",
            check_mode="h_bilingual",
        )
    )

    assert parsed_sides == [ReportSide.H_SHARE, ReportSide.H_SHARE]
    assert profile_called is False
    assert job.check_mode == "h_bilingual"
    assert job.profile_a is None
    assert job.profile_h is None
    assert job.comparison_summary["check_mode"] == "h_bilingual"
    assert job.comparison_summary["side_labels"] == {"A": "H中文", "H": "H英文"}
    assert job.comparison_summary["bilingual_level"] == "fast"
    assert captured_check["semantic_evaluator"] is None
    assert captured_check["enable_semantic"] is False


def test_h_bilingual_strict_uses_semantic_evaluator(monkeypatch):
    captured_check: dict[str, object] = {}

    async def fake_parse(self, file_path: str, side: ReportSide):
        return _doc(file_path)

    async def fake_build_report(self, job):
        return None

    def fake_evaluator(pairs):
        return []

    def fake_run_bilingual_checks(zh_doc, en_doc, *, semantic_evaluator=None, enable_semantic=False):
        captured_check["semantic_evaluator"] = semantic_evaluator
        captured_check["enable_semantic"] = enable_semantic
        from ahcc.check.bilingual import BilingualCheckResult

        return BilingualCheckResult(stats={})

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)
    monkeypatch.setattr("ahcc.check.bilingual.evaluate_semantic_with_llm", fake_evaluator)
    monkeypatch.setattr("ahcc.check.bilingual.run_bilingual_checks", fake_run_bilingual_checks)

    job = asyncio.run(
        Orchestrator().run(
            "h-zh.pdf",
            "h-en.pdf",
            company_name="Shenwan",
            check_mode="h_bilingual",
            bilingual_level="strict",
        )
    )

    assert captured_check["semantic_evaluator"] is fake_evaluator
    assert captured_check["enable_semantic"] is True
    assert job.comparison_summary["bilingual_level"] == "strict"


def test_h_bilingual_dict_coverage_items_remain_export_and_storage_compatible(monkeypatch):
    workspace_tmp = Path("storage") / "test-artifacts" / f"bilingual-orch-{uuid4().hex}"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(models, "_RECOVERED_SQLITE_PATH", workspace_tmp / "missing.db")
    monkeypatch.setattr(settings, "sqlite_path", workspace_tmp / "ahcc.db")
    models.init_db()

    async def fake_parse(self, file_path: str, side: ReportSide):
        return _doc(file_path)

    async def fake_build_report(self, job):
        assert job.coverage_items[0].coverage_id == "bilingual:text:1"
        assert job.coverage_items[0].category == "narrative"
        assert job.coverage_items[0].status == "matched"

    def fake_run_bilingual_checks(zh_doc, en_doc, *, semantic_evaluator=None, enable_semantic=False):
        from ahcc.check.bilingual import BilingualCheckResult

        return BilingualCheckResult(
            coverage_items=[
                {
                    "coverage_id": "bilingual:text:1",
                    "category": "narrative",
                    "status": "matched",
                    "confidence": 0.95,
                    "zh_page": 3,
                    "en_page": 4,
                    "zh_text": "董事会报告",
                    "en_text": "Report of the Board",
                    "note": "matched disclosure unit",
                }
            ],
            stats={},
        )

    monkeypatch.setattr(Orchestrator, "_parse", fake_parse)
    monkeypatch.setattr(Orchestrator, "_build_report", fake_build_report)
    monkeypatch.setattr("ahcc.check.bilingual.run_bilingual_checks", fake_run_bilingual_checks)

    job = asyncio.run(
        Orchestrator().run(
            "h-zh.pdf",
            "h-en.pdf",
            company_name="Shenwan",
            check_mode="h_bilingual",
        )
    )

    assert job.status.value == "done"
    assert job.coverage_items[0].coverage_id == "bilingual:text:1"
    assert job.coverage_items[0].a_pages == [3]
    assert job.coverage_items[0].h_pages == [4]
    assert job.coverage_items[0].match_confidence == 0.95
    save_job(job)
    shutil.rmtree(workspace_tmp, ignore_errors=True)
