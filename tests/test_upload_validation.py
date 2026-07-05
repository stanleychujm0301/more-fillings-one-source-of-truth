"""B2: 上传接口加固 —— PDF 魔数校验 + 大小上限 + 清理残留文件 + 非阻塞分块写入。"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ahcc.api import main as api_main
from ahcc.api import routes_job
from ahcc.schemas import Job


@pytest.fixture
def workspace_tmp():
    path = Path("storage") / "test-artifacts" / f"upload-validation-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _FakeOrchestrator:
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
        return job


def _prepare(monkeypatch, workspace_tmp: Path) -> None:
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "save_job", lambda job: None)
    monkeypatch.setattr(routes_job, "Orchestrator", lambda: _FakeOrchestrator())


def _upload_dir_files(workspace_tmp: Path) -> list[Path]:
    upload_dir = workspace_tmp / "uploads"
    if not upload_dir.is_dir():
        return []
    return list(upload_dir.iterdir())


def test_create_job_rejects_non_pdf_content_and_leaves_no_residual_files(monkeypatch, workspace_tmp):
    """Non-%PDF- content must be rejected with 415, and no half-written file (for either
    upload in the pair) should be left behind."""
    _prepare(monkeypatch, workspace_tmp)

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "Upload Guard"},
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"not a pdf", "application/pdf"),
            },
        )

    assert response.status_code == 415
    assert _upload_dir_files(workspace_tmp) == []


def test_create_job_rejects_oversized_upload_and_leaves_no_residual_files(monkeypatch, workspace_tmp):
    """Uploads exceeding settings.upload_max_bytes must be rejected with 413, and any partial
    file written before the limit was hit must be cleaned up."""
    _prepare(monkeypatch, workspace_tmp)
    monkeypatch.setattr(routes_job.settings, "upload_max_bytes", 16, raising=False)

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "Upload Guard"},
            files={
                "a_file": ("a.pdf", b"%PDF-" + b"0" * 64, "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 413
    assert _upload_dir_files(workspace_tmp) == []


def test_create_job_accepts_small_valid_pdf(monkeypatch, workspace_tmp):
    """The existing success path (small %PDF- uploads) must keep working unchanged."""
    _prepare(monkeypatch, workspace_tmp)

    with TestClient(api_main.app) as client:
        response = client.post(
            "/api/jobs/",
            data={"company_name": "Upload Guard"},
            files={
                "a_file": ("a.pdf", b"%PDF-a", "application/pdf"),
                "h_file": ("h.pdf", b"%PDF-h", "application/pdf"),
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    files = _upload_dir_files(workspace_tmp)
    assert len(files) == 2
