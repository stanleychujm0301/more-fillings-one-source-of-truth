from __future__ import annotations

import asyncio
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ahcc.api import main as api_main
from ahcc.api import routes_job
from ahcc.config import settings
from ahcc.schemas import Job, JobStatus
from ahcc.storage import models, repository


@pytest.fixture
def workspace_tmp():
    path = Path("storage") / "test-artifacts" / f"job-company-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _use_temp_db(monkeypatch, workspace_tmp):
    monkeypatch.setattr(models, "_RECOVERED_SQLITE_PATH", workspace_tmp / "missing.db")
    monkeypatch.setattr(settings, "sqlite_path", workspace_tmp / "ahcc.db")
    models.init_db()


def test_repository_persists_company_name(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)

    with models.get_conn() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    assert "company_name" in columns
    assert "check_mode" in columns

    repository.save_job(
        Job(
            job_id="j-company",
            company_name="招商证券",
            check_mode="h_bilingual",
            a_file="a.pdf",
            h_file="h.pdf",
        )
    )

    assert repository.list_jobs()[0]["company_name"] == "招商证券"
    assert repository.list_jobs()[0]["check_mode"] == "h_bilingual"
    assert repository.get_job("j-company")["company_name"] == "招商证券"
    assert repository.get_job("j-company")["check_mode"] == "h_bilingual"


def test_mark_interrupted_running_jobs_skips_locked_database(monkeypatch):
    class LockedConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "get_conn", lambda: LockedConnection())

    assert repository.mark_interrupted_running_jobs_failed(now=datetime(2026, 1, 1)) == 0


def test_repository_recovers_empty_sqlite_file(monkeypatch, workspace_tmp):
    monkeypatch.setattr(models, "_RECOVERED_SQLITE_PATH", workspace_tmp / "missing.db")
    db_path = workspace_tmp / "ahcc.db"
    monkeypatch.setattr(settings, "sqlite_path", db_path)
    db_path.write_bytes(b"")

    assert repository.list_jobs() == []

    with models.get_conn() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    assert "jobs" in tables
    assert "diffs" in tables
    assert "reviews" in tables


def test_create_job_rejects_blank_company_name(monkeypatch, workspace_tmp):
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator({}))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "   "},
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 422


def test_create_job_rejects_unknown_check_mode(monkeypatch, workspace_tmp):
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator({}))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "招商证券", "check_mode": "wrong"},
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 422


def test_create_job_trims_company_name_and_passes_it_to_orchestrator(monkeypatch, workspace_tmp):
    captured: dict[str, str | None] = {}
    saved: list[Job] = []

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator(captured))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "  招商证券  ", "check_mode": "h_bilingual"},
            files={
                "a_file": ("zh.pdf", b"%PDF-zh", "application/pdf"),
                "h_file": ("en.pdf", b"%PDF-en", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert captured["company_name"] == "招商证券"
    assert captured["check_mode"] == "h_bilingual"
    assert response.json()["company_name"] == "招商证券"
    assert response.json()["check_mode"] == "h_bilingual"
    assert saved[0].company_name == "招商证券"
    assert saved[0].check_mode == "h_bilingual"
    assert captured["bilingual_level"] == "fast"


def test_create_job_returns_pending_job_and_schedules_background_run(monkeypatch, workspace_tmp):
    scheduled: list[object] = []
    saved: list[Job] = []

    class _ScheduledTask:
        def __init__(self, coro):
            self.coro = coro
            coro.close()

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(
        routes_job.asyncio,
        "create_task",
        lambda coro: scheduled.append(_ScheduledTask(coro)) or scheduled[-1],
    )

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "  Render Demo  ", "check_mode": "ah"},
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["company_name"] == "Render Demo"
    assert payload["job_id"]
    assert len(saved) == 1
    assert saved[0].job_id == payload["job_id"]
    assert saved[0].status == JobStatus.PENDING
    assert scheduled


def test_repository_marks_stale_running_jobs_failed(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)
    now = datetime(2026, 7, 1, 12, 30, 0)
    stale_started = now - timedelta(minutes=30)
    fresh_started = now - timedelta(minutes=2)

    repository.save_job(
        Job(
            job_id="stale-running",
            company_name="Stale",
            a_file="a.pdf",
            h_file="h.pdf",
            status=JobStatus.CHECKING,
            started_at=stale_started,
            comparison_summary={
                "current_stage": "checking",
                "current_percent": 55,
                "current_message": "checking",
                "last_progress_at": stale_started.isoformat(),
            },
        )
    )
    repository.save_job(
        Job(
            job_id="fresh-running",
            company_name="Fresh",
            a_file="a.pdf",
            h_file="h.pdf",
            status=JobStatus.PARSING,
            started_at=fresh_started,
            comparison_summary={
                "current_stage": "parsing",
                "current_percent": 10,
                "current_message": "parsing",
                "last_progress_at": fresh_started.isoformat(),
            },
        )
    )
    repository.save_job(
        Job(
            job_id="done-job",
            company_name="Done",
            a_file="a.pdf",
            h_file="h.pdf",
            status=JobStatus.DONE,
            started_at=stale_started,
            finished_at=stale_started + timedelta(seconds=30),
        )
    )

    changed = repository.mark_stale_running_jobs_failed(stale_after_seconds=900, now=now)

    stale = repository.get_job("stale-running")
    fresh = repository.get_job("fresh-running")
    done = repository.get_job("done-job")
    assert changed == 1
    assert stale["status"] == "failed"
    assert stale["finished_at"] == now.isoformat()
    assert stale["duration_seconds"] == 1800
    assert "interrupted" in stale["error"]
    assert stale["comparison_summary"]["current_stage"] == "failed"
    assert stale["comparison_summary"]["current_percent"] == 0
    assert fresh["status"] == "parsing"
    assert done["status"] == "done"


def test_repository_marks_interrupted_running_jobs_failed(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)
    now = datetime(2026, 7, 1, 12, 30, 0)
    started = now - timedelta(minutes=2)

    repository.save_job(
        Job(
            job_id="interrupted-running",
            company_name="Interrupted",
            a_file="a.pdf",
            h_file="h.pdf",
            status=JobStatus.PARSING,
            started_at=started,
            comparison_summary={
                "current_stage": "parsing",
                "current_percent": 10,
                "current_message": "parsing",
                "last_progress_at": started.isoformat(),
            },
        )
    )

    changed = repository.mark_interrupted_running_jobs_failed(now=now)

    interrupted = repository.get_job("interrupted-running")
    assert changed == 1
    assert interrupted["status"] == "failed"
    assert interrupted["duration_seconds"] == 120
    assert "service restarted" in interrupted["error"]
    assert interrupted["comparison_summary"]["current_stage"] == "failed"


def test_lifespan_marks_stale_running_jobs_failed(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(api_main, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        api_main,
        "mark_interrupted_running_jobs_failed",
        lambda **kwargs: calls.append("interrupted_recovery") or 2,
    )

    async def run_lifespan():
        async with api_main.lifespan(api_main.app):
            calls.append("inside")

    asyncio.run(run_lifespan())

    assert calls == ["init_db", "interrupted_recovery", "inside"]


@pytest.mark.asyncio
async def test_background_run_reuses_queued_job_id_and_saves_final_job(monkeypatch):
    saved: list[Job] = []
    captured: dict[str, str | None] = {}
    queued = Job(
        job_id="queued-1",
        company_name="Queued Project",
        check_mode="h_bilingual",
        a_file="zh.pdf",
        h_file="en.pdf",
    )

    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator(captured))

    await routes_job._run_job_background(queued, bilingual_level="strict")

    assert captured["job_id"] == "queued-1"
    assert captured["company_name"] == "Queued Project"
    assert captured["check_mode"] == "h_bilingual"
    assert captured["bilingual_level"] == "strict"
    assert saved[-1].job_id == "queued-1"


@pytest.mark.asyncio
async def test_background_run_persists_progress_updates(monkeypatch):
    final_saved: list[Job] = []
    progress_saved: list[Job] = []
    queued = Job(
        job_id="progress-1",
        company_name="Progress Project",
        check_mode="ah",
        a_file="a.pdf",
        h_file="h.pdf",
    )

    class _ProgressOrchestrator:
        async def run(
            self,
            a_file: str,
            h_file: str,
            company_name: str | None = None,
            check_mode: str = "ah",
            bilingual_level: str = "fast",
            visual_review_mode: str = "smart",
            job: Job | None = None,
            progress_callback=None,
        ) -> Job:
            assert progress_callback is not None
            running = job.model_copy(
                update={
                    "status": JobStatus.CHECKING,
                    "comparison_summary": {
                        "current_stage": "checking",
                        "current_percent": 55,
                        "current_message": "checking",
                        "last_progress_at": "2026-07-01T12:00:00",
                    },
                }
            )
            progress_callback(running)
            return job.model_copy(update={"status": JobStatus.DONE})

    monkeypatch.setattr(routes_job, "save_job", final_saved.append)
    monkeypatch.setattr(routes_job, "save_job_progress", progress_saved.append)
    monkeypatch.setattr(routes_job, "Orchestrator", _ProgressOrchestrator)
    monkeypatch.setattr(routes_job.settings, "job_timeout_seconds", 30, raising=False)

    await routes_job._run_job_background(queued)

    assert len(progress_saved) == 1
    progress_job = progress_saved[0]
    assert progress_job.comparison_summary["current_stage"] == "checking"
    assert progress_job.comparison_summary["current_percent"] == 55
    assert final_saved[-1].status == JobStatus.DONE


@pytest.mark.asyncio
async def test_background_run_timeout_marks_job_failed(monkeypatch):
    saved: list[Job] = []
    queued = Job(
        job_id="timeout-1",
        company_name="Timeout Project",
        check_mode="ah",
        a_file="a.pdf",
        h_file="h.pdf",
    )

    class _SlowOrchestrator:
        async def run(self, *args, **kwargs) -> Job:
            running_job = kwargs.get("job")
            if running_job is not None:
                running_job.comparison_summary = {
                    "current_stage": "checking",
                    "current_percent": 75,
                    "current_message": "视觉 OCR 抽样复核",
                    "last_progress_at": "2026-07-01T12:00:00",
                }
            await asyncio.sleep(10)
            return queued.model_copy(update={"status": JobStatus.DONE})

    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(routes_job, "Orchestrator", _SlowOrchestrator)
    monkeypatch.setattr(routes_job.settings, "job_timeout_seconds", 0.01, raising=False)

    await routes_job._run_job_background(queued)

    assert saved[-1].status == JobStatus.FAILED
    assert saved[-1].finished_at is not None
    assert saved[-1].duration_seconds is not None
    assert "timeout" in saved[-1].error
    assert saved[-1].comparison_summary["current_stage"] == "checking"
    assert saved[-1].comparison_summary["current_percent"] == 75
    assert saved[-1].comparison_summary["current_message"] == "视觉 OCR 抽样复核"
    assert saved[-1].comparison_summary["failure_stage"] == "failed"
    assert "timeout" in saved[-1].comparison_summary["failure_message"]


def test_create_job_passes_strict_bilingual_level(monkeypatch, workspace_tmp):
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator(captured))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={
                "company_name": "Shenwan",
                "check_mode": "h_bilingual",
                "bilingual_level": "strict",
            },
            files={
                "a_file": ("zh.pdf", b"%PDF-zh", "application/pdf"),
                "h_file": ("en.pdf", b"%PDF-en", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert captured["check_mode"] == "h_bilingual"
    assert captured["bilingual_level"] == "strict"


def test_create_job_passes_strict_visual_review_mode(monkeypatch, workspace_tmp):
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator(captured))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={
                "company_name": "Visual Demo",
                "check_mode": "ah",
                "visual_review_mode": "strict",
            },
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert captured["check_mode"] == "ah"
    assert captured["visual_review_mode"] == "strict"


def test_create_job_defaults_to_visual_review_off(monkeypatch, workspace_tmp):
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator(captured))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={
                "company_name": "Qingdao Demo",
                "check_mode": "ah",
            },
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert captured["visual_review_mode"] == "off"


def test_create_job_accepts_visual_review_off(monkeypatch, workspace_tmp):
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator(captured))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={
                "company_name": "Qingdao Demo",
                "check_mode": "ah",
                "visual_review_mode": "off",
            },
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert captured["visual_review_mode"] == "off"


def test_create_job_rejects_unknown_bilingual_level(monkeypatch, workspace_tmp):
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator({}))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={
                "company_name": "Shenwan",
                "check_mode": "h_bilingual",
                "bilingual_level": "deep",
            },
            files={
                "a_file": ("zh.pdf", b"%PDF-zh", "application/pdf"),
                "h_file": ("en.pdf", b"%PDF-en", "application/pdf"),
            },
        )

    assert response.status_code == 422


def test_create_job_rejects_unknown_visual_review_mode(monkeypatch, workspace_tmp):
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator({}))

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={
                "company_name": "Visual Demo",
                "check_mode": "ah",
                "visual_review_mode": "full",
            },
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 422


class _FakeOrchestrator:
    def __init__(self, captured):
        self._captured = captured

    async def run(
        self,
        a_file: str,
        h_file: str,
        company_name: str | None = None,
        check_mode: str = "ah",
        bilingual_level: str = "fast",
        visual_review_mode: str = "off",
        job: Job | None = None,
        progress_callback=None,
    ) -> Job:
        self._captured["company_name"] = company_name
        self._captured["check_mode"] = check_mode
        self._captured["bilingual_level"] = bilingual_level
        self._captured["visual_review_mode"] = visual_review_mode
        self._captured["job_id"] = job.job_id if job else None
        return Job(
            job_id=job.job_id if job else "j-api",
            company_name=company_name,
            check_mode=check_mode,
            a_file=a_file,
            h_file=h_file,
        )
