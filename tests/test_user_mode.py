from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ahcc.api import main as api_main
from ahcc.api import routes_job
from ahcc.config import settings
from ahcc.schemas import Job
from ahcc.storage import models, repository


@pytest.fixture
def workspace_tmp():
    path = Path("storage") / "test-artifacts" / f"user-mode-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _use_temp_db(monkeypatch, workspace_tmp: Path) -> None:
    monkeypatch.setattr(models, "_RECOVERED_SQLITE_PATH", workspace_tmp / "missing.db")
    monkeypatch.setattr(settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(settings, "sqlite_path", workspace_tmp / "ahcc.db")
    models.init_db()


def test_init_db_seeds_demo_user_and_job_ownership_columns(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)

    with models.get_conn() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        user = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", ("chu-stanley",)).fetchone()

    assert "user_profiles" in tables
    assert {
        "owner_user_id",
        "owner_display_name",
        "project_group_id",
        "project_group_name",
    } <= job_columns
    assert user["display_name"] == "Chu, Stanley"
    assert user["office_line"] == "SH/FS3"
    assert user["project_group_id"] == "sh-fs3"


def test_sqlite_connection_tolerates_locked_journal_pragma(monkeypatch, workspace_tmp):
    class FakeConnection:
        row_factory = None

        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str):
            self.statements.append(statement)
            if "journal_mode" in statement or "synchronous" in statement:
                raise sqlite3.OperationalError("database is locked")
            return None

    fake = FakeConnection()
    monkeypatch.setattr(models.sqlite3, "connect", lambda *args, **kwargs: fake)

    conn = models._connect_sqlite(workspace_tmp / "locked.db")

    assert conn is fake
    assert fake.statements[0] == "PRAGMA busy_timeout=30000"
    assert "PRAGMA synchronous=NORMAL" in fake.statements


def test_get_conn_tolerates_locked_schema_check(monkeypatch, workspace_tmp):
    class FakeConnection:
        row_factory = None
        closed = False

        def execute(self, statement: str):
            return None

        def executescript(self, script: str):
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    monkeypatch.setattr(models, "_active_sqlite_path", lambda: workspace_tmp / "locked.db")
    monkeypatch.setattr(models, "_connect_sqlite", lambda path: fake)

    with models.get_conn() as conn:
        assert conn is fake

    assert fake.closed is True


def test_session_current_and_profile_update(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)

    with TestClient(api_main.app) as client:
        session = client.get("/api/session/current")
        assert session.status_code == 200
        assert session.json()["user"]["display_name"] == "Chu, Stanley"
        assert session.json()["user"]["project_group"]["id"] == "sh-fs3"

        update = client.patch(
            "/api/users/current",
            json={"display_name": "Chu, Stanley", "office_line": "SH/FS3", "role_title": "Audit Manager"},
        )
        assert update.status_code == 200
        assert update.json()["role_title"] == "Audit Manager"

        refreshed = client.get("/api/session/current")
        assert refreshed.json()["user"]["role_title"] == "Audit Manager"


def test_avatar_upload_accepts_images_and_rejects_invalid_files(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)

    with TestClient(api_main.app) as client:
        invalid = client.post(
            "/api/users/current/avatar",
            files={"avatar": ("avatar.txt", b"not-image", "text/plain")},
        )
        assert invalid.status_code == 415

        uploaded = client.post(
            "/api/users/current/avatar",
            files={"avatar": ("avatar.png", _small_png(), "image/png")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["avatar_url"] == "/api/users/current/avatar"

        avatar = client.get("/api/users/current/avatar")
        assert avatar.status_code == 200
        assert avatar.headers["content-type"].startswith("image/png")
        assert avatar.content == _small_png()


def test_create_job_assigns_current_user_and_project(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)
    saved: list[Job] = []

    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator())

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "Demo Co", "check_mode": "ah"},
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert response.json()["owner_user_id"] == "chu-stanley"
    assert response.json()["owner_display_name"] == "Chu, Stanley"
    assert response.json()["project_group_id"] == "sh-fs3"
    assert saved[0].owner_user_id == "chu-stanley"
    assert saved[0].project_group_id == "sh-fs3"


def test_history_scope_filters_project_and_current_user(monkeypatch, workspace_tmp):
    _use_temp_db(monkeypatch, workspace_tmp)
    repository.save_job(Job(job_id="mine", company_name="Mine Co", a_file="a.pdf", h_file="h.pdf"))

    with models.get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
            (job_id, company_name, check_mode, a_file, h_file, status, started_at,
             owner_user_id, owner_display_name, project_group_id, project_group_name, comparison_summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "team",
                "Team Co",
                "ah",
                "a.pdf",
                "h.pdf",
                "done",
                "2026-06-27T08:00:00",
                "teammate",
                "Team Mate",
                "sh-fs3",
                "SH/FS3",
                json.dumps({"result_version": 12}),
            ),
        )
        conn.execute(
            """INSERT INTO jobs
            (job_id, company_name, check_mode, a_file, h_file, status, started_at,
             owner_user_id, owner_display_name, project_group_id, project_group_name, comparison_summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "other",
                "Other Co",
                "ah",
                "a.pdf",
                "h.pdf",
                "done",
                "2026-06-27T08:01:00",
                "other-user",
                "Other User",
                "bj-fs1",
                "BJ/FS1",
                json.dumps({"result_version": 12}),
            ),
        )
        conn.commit()

    with TestClient(api_main.app) as client:
        project = client.get("/api/jobs/history?scope=project&limit=10")
        mine = client.get("/api/jobs/history?scope=mine&limit=10")
        other_detail = client.get("/api/jobs/other")
        other_diffs = client.get("/api/jobs/other/diffs")

    assert project.status_code == 200
    assert {item["job_id"] for item in project.json()} == {"team", "mine"}
    assert mine.status_code == 200
    assert [item["job_id"] for item in mine.json()] == ["mine"]
    assert other_detail.status_code == 404
    assert other_diffs.status_code == 404


def _small_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05"
        b"\xfe\x02\xfeA\x90\x84\x81\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class _FakeOrchestrator:
    async def run(
        self,
        a_file: str,
        h_file: str,
        company_name: str | None = None,
        check_mode: str = "ah",
        bilingual_level: str = "fast",
        visual_review_mode: str = "smart",
    ) -> Job:
        return Job(
            job_id="j-user",
            company_name=company_name,
            check_mode=check_mode,
            a_file=a_file,
            h_file=h_file,
        )
