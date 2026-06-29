from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ahcc.api import main as api_main
from ahcc.api import routes_job
from ahcc.schemas import Language, ReportDocument, ReportSide, TextSegment


@pytest.fixture
def workspace_tmp():
    path = Path("storage") / "test-artifacts" / f"report-download-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _patch_report_job(monkeypatch, workspace_tmp: Path, job_id: str = "j-download") -> Path:
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(
        routes_job,
        "get_job",
        lambda requested_job_id: {
            "job_id": requested_job_id,
            "company_name": "Downloaded Project",
            "check_mode": "ah",
            "a_file": "a.pdf",
            "h_file": "h.pdf",
            "status": "done",
            "coverage_items": [],
            "comparison_summary": {"result_version": 11},
        }
        if requested_job_id == job_id
        else None,
    )
    monkeypatch.setattr(routes_job, "get_diffs", lambda requested_job_id: [])

    report_dir = workspace_tmp / "jobs" / job_id
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def test_pdf_download_regenerates_with_current_exporter_and_disables_cache(monkeypatch, workspace_tmp):
    job_id = "j-download"
    report_dir = _patch_report_job(monkeypatch, workspace_tmp, job_id)
    old_path = report_dir / "report.pdf"
    old_path.write_bytes(b"old-pdf")

    def fake_export_pdf(job, out_path):
        assert job.job_id == job_id
        assert job.company_name == "Downloaded Project"
        Path(out_path).write_bytes(b"latest-pdf")

    monkeypatch.setattr(routes_job, "export_pdf", fake_export_pdf, raising=False)

    with TestClient(api_main.app) as client:
        response = client.get(f"/api/jobs/{job_id}/report.pdf?template=latest")

    assert response.status_code == 200
    assert response.content == b"latest-pdf"
    assert old_path.read_bytes() == b"latest-pdf"
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_excel_download_regenerates_with_current_exporter_and_disables_cache(monkeypatch, workspace_tmp):
    job_id = "j-download"
    report_dir = _patch_report_job(monkeypatch, workspace_tmp, job_id)
    old_path = report_dir / "report.xlsx"
    old_path.write_bytes(b"old-xlsx")

    def fake_export_excel(job, out_path):
        assert job.job_id == job_id
        assert job.company_name == "Downloaded Project"
        Path(out_path).write_bytes(b"latest-xlsx")

    monkeypatch.setattr(routes_job, "export_excel", fake_export_excel, raising=False)

    with TestClient(api_main.app) as client:
        response = client.get(f"/api/jobs/{job_id}/report.xlsx?template=latest")

    assert response.status_code == 200
    assert response.content == b"latest-xlsx"
    assert old_path.read_bytes() == b"latest-xlsx"
    assert "no-store" in response.headers["cache-control"]


def test_report_download_generation_failure_does_not_serve_stale_file(monkeypatch, workspace_tmp):
    job_id = "j-download"
    report_dir = _patch_report_job(monkeypatch, workspace_tmp, job_id)
    old_path = report_dir / "report.pdf"
    old_path.write_bytes(b"old-pdf")

    def broken_export_pdf(job, out_path):
        raise RuntimeError("template unavailable")

    monkeypatch.setattr(routes_job, "export_pdf", broken_export_pdf, raising=False)

    with TestClient(api_main.app) as client:
        response = client.get(f"/api/jobs/{job_id}/report.pdf?template=latest")

    assert response.status_code == 500
    assert old_path.read_bytes() == b"old-pdf"


def test_job_detail_repairs_stored_branch_diffs_before_response(monkeypatch, workspace_tmp):
    job_id = "j-branch-repair"
    a_file, h_file = _branch_repair_source_files(workspace_tmp)
    saved: list[object] = []

    def fake_get_job(requested_job_id):
        if requested_job_id != job_id:
            return None
        if saved:
            job = saved[-1]
            return {
                "job_id": job.job_id,
                "company_name": job.company_name,
                "check_mode": job.check_mode,
                "a_file": job.a_file,
                "h_file": job.h_file,
                "status": job.status.value,
                "coverage_items": [],
                "comparison_summary": job.comparison_summary,
            }
        return _stale_branch_job_payload(job_id, a_file, h_file)

    def fake_get_diffs(requested_job_id):
        return saved[-1].diffs if saved else []

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job, "get_job", fake_get_job)
    monkeypatch.setattr(routes_job, "get_diffs", fake_get_diffs)
    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(routes_job, "_load_branch_repair_doc", _fake_branch_repair_doc, raising=False)

    with TestClient(api_main.app) as client:
        response = client.get(f"/api/jobs/{job_id}")

    payload = response.json()
    summary = payload["comparison_summary"]
    assert response.status_code == 200
    assert summary["real_diff_count"] == 1
    assert summary["total_diff_count"] == 1
    assert summary["branch_diff_count"] == 1
    assert summary["matched_branch_count"] == 3
    assert payload["diffs"][0]["rule_id"] == "branch_asset_scale_match"
    assert saved


def test_pdf_download_repairs_stored_branch_diffs_before_export(monkeypatch, workspace_tmp):
    job_id = "j-branch-report"
    a_file, h_file = _branch_repair_source_files(workspace_tmp)
    saved: list[object] = []

    def fake_get_job(requested_job_id):
        if requested_job_id != job_id:
            return None
        if saved:
            job = saved[-1]
            return {
                "job_id": job.job_id,
                "company_name": job.company_name,
                "check_mode": job.check_mode,
                "a_file": job.a_file,
                "h_file": job.h_file,
                "status": job.status.value,
                "coverage_items": [],
                "comparison_summary": job.comparison_summary,
            }
        return _stale_branch_job_payload(job_id, a_file, h_file)

    def fake_get_diffs(requested_job_id):
        return saved[-1].diffs if saved else []

    def fake_export_pdf(job, out_path):
        branch_count = sum(1 for diff in job.diffs if diff.rule_id == "branch_asset_scale_match")
        Path(out_path).write_bytes(f"branch={branch_count}".encode("ascii"))

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(routes_job.settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(routes_job, "get_job", fake_get_job)
    monkeypatch.setattr(routes_job, "get_diffs", fake_get_diffs)
    monkeypatch.setattr(routes_job, "save_job", saved.append)
    monkeypatch.setattr(routes_job, "_load_branch_repair_doc", _fake_branch_repair_doc, raising=False)
    monkeypatch.setattr(routes_job, "export_pdf", fake_export_pdf, raising=False)

    with TestClient(api_main.app) as client:
        response = client.get(f"/api/jobs/{job_id}/report.pdf?template=latest")

    assert response.status_code == 200
    assert response.content == b"branch=1"
    assert saved[-1].comparison_summary["branch_diff_count"] == 1


def _branch_repair_source_files(workspace_tmp: Path) -> tuple[Path, Path]:
    a_file = workspace_tmp / "a.pdf"
    h_file = workspace_tmp / "h.pdf"
    a_file.write_bytes(b"%PDF-a")
    h_file.write_bytes(b"%PDF-h")
    return a_file, h_file


def _stale_branch_job_payload(job_id: str, a_file: Path, h_file: Path) -> dict:
    return {
        "job_id": job_id,
        "company_name": "Branch Repair",
        "check_mode": "ah",
        "a_file": str(a_file),
        "h_file": str(h_file),
        "status": "done",
        "coverage_items": [],
        "comparison_summary": {
            "result_version": 11,
            "extraction_engine_version": "2026-06-01.4",
            "real_diff_count": 0,
            "expected_diff_count": 0,
            "unresolved_diff_count": 0,
            "total_diff_count": 0,
            "branch_diff_count": 0,
            "a_branch_count": 3,
            "h_branch_count": 3,
            "matched_branch_count": 1,
            "branch_alignment_ratio": 0.3333,
        },
    }


def _fake_branch_repair_doc(file_path: str, side: ReportSide) -> ReportDocument:
    is_a_side = side == ReportSide.A_SHARE
    text = (
        "北京分行 10 100,000 上海分行 8 80,000 广州分行 6 60,000"
        if is_a_side
        else "北京分行 10 120,000 上海分行 8 80,000 广州分行 6 60,000"
    )
    return ReportDocument(
        doc_id=side.value,
        side=side,
        file_path=file_path,
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id=f"{side.value}-branch",
                page=30 if is_a_side else 31,
                bbox=(0, 0, 1, 1),
                text=text,
                language=Language.ZH,
            )
        ],
    )
