from __future__ import annotations

import shutil
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
        job: Job | None = None,
    ) -> Job:
        self._captured["company_name"] = company_name
        self._captured["check_mode"] = check_mode
        self._captured["bilingual_level"] = bilingual_level
        self._captured["job_id"] = job.job_id if job else None
        return Job(
            job_id=job.job_id if job else "j-api",
            company_name=company_name,
            check_mode=check_mode,
            a_file=a_file,
            h_file=h_file,
        )
