"""任务路由 — 上传两份 PDF → 创建任务 → 查询进度/结果。"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from ahcc.config import settings
from ahcc.orchestrator import Orchestrator
from ahcc.report.excel import export_excel
from ahcc.report.pdf import export_pdf
from ahcc.schemas import Diff, Job
from ahcc.storage.repository import apply_current_user_context, get_diffs, get_job, list_jobs, save_job

router = APIRouter()

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@router.get("/history")
def list_jobs_endpoint(limit: int = 10, scope: str = "project") -> list[dict]:
    """列出历史核查任务。"""
    return list_jobs(limit, scope=scope)


@router.post("/", response_model=Job)
async def create_job(
    company_name: str = Form(...),
    check_mode: str = Form("ah"),
    bilingual_level: str = Form("fast"),
    a_file: UploadFile = File(...),
    h_file: UploadFile = File(...),
) -> Job:
    """创建并执行一个核查任务。"""
    normalized_company_name = company_name.strip()
    if not normalized_company_name or len(normalized_company_name) > 80:
        raise HTTPException(status_code=422, detail="company_name must be 1-80 characters")
    normalized_check_mode = (check_mode or "ah").strip()
    if normalized_check_mode not in {"ah", "h_bilingual"}:
        raise HTTPException(status_code=422, detail="check_mode must be ah or h_bilingual")
    normalized_bilingual_level = (bilingual_level or "fast").strip().lower()
    if normalized_bilingual_level not in {"fast", "strict"}:
        raise HTTPException(status_code=422, detail="bilingual_level must be fast or strict")

    upload_dir = settings.storage_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    a_prefix, h_prefix = ("H_ZH", "H_EN") if normalized_check_mode == "h_bilingual" else ("A", "H")
    # 只取文件名部分，剥离任何目录成分，防止 ../ 之类的路径穿越写出 upload_dir 之外
    a_name = Path(a_file.filename or "a.pdf").name
    h_name = Path(h_file.filename or "h.pdf").name
    a_path = upload_dir / f"{a_prefix}_{a_name}"
    h_path = upload_dir / f"{h_prefix}_{h_name}"
    with a_path.open("wb") as f:
        shutil.copyfileobj(a_file.file, f)
    with h_path.open("wb") as f:
        shutil.copyfileobj(h_file.file, f)

    job = await Orchestrator().run(
        str(a_path),
        str(h_path),
        normalized_company_name,
        normalized_check_mode,
        bilingual_level=normalized_bilingual_level,
    )
    job = apply_current_user_context(job)
    save_job(job)
    return job


@router.get("/{job_id}/diffs", response_model=list[Diff])
def list_diffs(job_id: str) -> list[Diff]:
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return get_diffs(job_id)


@router.get("/{job_id}")
def get_job_detail(job_id: str):
    """获取单个历史任务详情（含 diffs）。"""
    job_meta = get_job(job_id)
    if not job_meta:
        raise HTTPException(status_code=404, detail="job not found")
    job_meta["diffs"] = [d.model_dump() for d in get_diffs(job_id)]
    return job_meta


@router.get("/{job_id}/report.xlsx")
def download_excel(job_id: str):
    path = _regenerate_report(job_id, "report.xlsx", export_excel)
    return _no_cache_report_response(path, filename=f"AHCC-{job_id}.xlsx")


@router.get("/{job_id}/report.pdf")
def download_pdf(job_id: str):
    path = _regenerate_report(job_id, "report.pdf", export_pdf)
    return _no_cache_report_response(path, filename=f"AHCC-{job_id}.pdf")


def _load_job_for_report(job_id: str) -> Job:
    job_meta = get_job(job_id)
    if not job_meta:
        raise HTTPException(status_code=404, detail="job not found")
    payload = dict(job_meta)
    payload["diffs"] = get_diffs(job_id)
    return Job.model_validate(payload)


def _regenerate_report(job_id: str, report_name: str, exporter) -> Path:
    job = _load_job_for_report(job_id)
    out_dir = settings.storage_dir / "jobs" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    target = out_dir / report_name
    temp_path = target.with_name(f".{target.stem}.{uuid4().hex}{target.suffix}")
    try:
        exporter(job, temp_path)
        if not temp_path.is_file():
            raise RuntimeError(f"{report_name} exporter did not create a file")
        shutil.copyfile(temp_path, target)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _remove_temp_report(temp_path)
        logger.exception(f"[{job_id}] report regeneration failed: {report_name}")
        raise HTTPException(status_code=500, detail=f"{report_name} regeneration failed") from exc
    finally:
        _remove_temp_report(temp_path)
    return target


def _no_cache_report_response(path: Path, *, filename: str) -> FileResponse:
    response = FileResponse(path, filename=filename)
    response.headers.update(_NO_CACHE_HEADERS)
    return response


def _remove_temp_report(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning(f"Unable to remove temporary report file: {path}")
