"""任务路由 — 上传两份 PDF → 创建任务 → 查询进度/结果。"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from ahcc.check.branch_disclosure import (
    branch_table_diagnostics,
    compare_branch_tables,
    load_branch_lightweight_doc,
)
from ahcc.config import settings
from ahcc.orchestrator import Orchestrator
from ahcc.parser.audit import EXTRACTION_ENGINE_VERSION, PARSER_VERSION
from ahcc.report.excel import export_excel
from ahcc.report.pdf import export_pdf
from ahcc.schemas import Diff, Job, JobStatus, ReportSide
from ahcc.storage.repository import (
    _CURRENT_RESULT_VERSION,
    apply_current_user_context,
    get_diffs,
    get_job,
    list_jobs,
    running_progress_summary,
    save_job,
    save_job_progress,
)

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
    visual_review_mode: str = Form("off"),
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
    normalized_visual_review_mode = (visual_review_mode or "off").strip().lower()
    if normalized_visual_review_mode not in {"off", "smart", "strict"}:
        raise HTTPException(status_code=422, detail="visual_review_mode must be off, smart or strict")

    upload_dir = settings.storage_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    job_id = uuid4().hex[:8]
    a_prefix, h_prefix = ("H_ZH", "H_EN") if normalized_check_mode == "h_bilingual" else ("A", "H")
    # 只取文件名部分，剥离任何目录成分，防止 ../ 之类的路径穿越写出 upload_dir 之外
    a_name = Path(a_file.filename or "a.pdf").name
    h_name = Path(h_file.filename or "h.pdf").name
    a_path = upload_dir / f"{job_id}_{a_prefix}_{a_name}"
    h_path = upload_dir / f"{job_id}_{h_prefix}_{h_name}"
    with a_path.open("wb") as f:
        shutil.copyfileobj(a_file.file, f)
    with h_path.open("wb") as f:
        shutil.copyfileobj(h_file.file, f)

    job = apply_current_user_context(
        Job(
            job_id=job_id,
            company_name=normalized_company_name,
            check_mode=normalized_check_mode,
            a_file=str(a_path),
            h_file=str(h_path),
            status=JobStatus.PENDING,
        )
    )
    save_job(job)
    from ahcc.api.job_runner import run_job  # 延迟导入避免循环

    asyncio.create_task(
        run_job(
            job,
            bilingual_level=normalized_bilingual_level,
            visual_review_mode=normalized_visual_review_mode,
        )
    )
    return job


async def _run_job_background(
    job: Job,
    *,
    bilingual_level: str = "fast",
    visual_review_mode: str = "off",
) -> None:
    def _persist_progress(updated_job: Job) -> None:
        save_job_progress(apply_current_user_context(updated_job))

    try:
        completed = await asyncio.wait_for(
            Orchestrator().run(
                job.a_file,
                job.h_file,
                job.company_name,
                job.check_mode,
                bilingual_level=bilingual_level,
                visual_review_mode=visual_review_mode,
                job=job,
                progress_callback=_persist_progress,
            ),
            timeout=settings.job_timeout_seconds,
        )
        save_job(apply_current_user_context(completed))
    except asyncio.TimeoutError:
        message = (
            f"job timeout: exceeded {int(settings.job_timeout_seconds)} seconds; "
            "please rerun the task with smaller files or a faster review mode."
        )
        logger.exception(f"[{job.job_id}] background job timed out")
        save_job(apply_current_user_context(_failed_background_job(job, message)))
    except asyncio.CancelledError:
        message = "background job interrupted: service stopped before the task completed; please rerun the task."
        logger.warning(f"[{job.job_id}] background job cancelled")
        save_job(apply_current_user_context(_failed_background_job(job, message)))
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[{job.job_id}] background job failed")
        save_job(apply_current_user_context(_failed_background_job(job, str(exc))))


def _failed_background_job(job: Job, message: str) -> Job:
    finished_at = datetime.utcnow()
    summary = dict(job.comparison_summary or {})
    if summary.get("current_stage"):
        summary.update(
            {
                "failure_stage": JobStatus.FAILED.value,
                "failure_message": message,
                "failed_at": finished_at.isoformat(),
            }
        )
    else:
        summary = running_progress_summary(
            summary,
            JobStatus.FAILED,
            0,
            message,
            now=finished_at,
        )
    return job.model_copy(
        update={
            "status": JobStatus.FAILED,
            "finished_at": finished_at,
            "duration_seconds": (finished_at - job.started_at).total_seconds(),
            "error": message,
            "comparison_summary": summary,
        }
    )


@router.get("/{job_id}/diffs", response_model=list[Diff])
def list_diffs(job_id: str) -> list[Diff]:
    _repair_branch_diffs_if_needed(job_id)
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return get_diffs(job_id)


@router.get("/{job_id}")
def get_job_detail(job_id: str):
    """获取单个历史任务详情（含 diffs）。"""
    _repair_branch_diffs_if_needed(job_id)
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
    _repair_branch_diffs_if_needed(job_id)
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


# 模块级别名，保留为可 monkeypatch 的钩子（测试用假文档替身，避免真实解析 PDF）。
_load_branch_repair_doc = load_branch_lightweight_doc


def _repair_branch_diffs_if_needed(job_id: str) -> bool:
    job_meta = get_job(job_id)
    if not job_meta:
        return False
    diffs = get_diffs(job_id)
    summary = dict(job_meta.get("comparison_summary") or {})
    if not _branch_repair_needed(job_meta, summary, diffs):
        return False

    a_path = Path(str(job_meta.get("a_file") or ""))
    h_path = Path(str(job_meta.get("h_file") or ""))
    if not a_path.is_file() or not h_path.is_file():
        return False

    try:
        doc_a = _load_branch_repair_doc(str(a_path), ReportSide.A_SHARE)
        doc_h = _load_branch_repair_doc(str(h_path), ReportSide.H_SHARE)
        branch_diffs = compare_branch_tables(doc_a, doc_h)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[{job_id}] branch diff repair failed")
        return False

    if not branch_diffs:
        return False

    branch_diffs = [diff.model_copy(update={"triage": "real"}) for diff in branch_diffs]
    existing_ids = {diff.diff_id for diff in diffs}
    merged_diffs = [
        *diffs,
        *(diff for diff in branch_diffs if diff.diff_id not in existing_ids),
    ]
    repaired_summary = _summary_with_repaired_branch_diffs(
        summary,
        merged_diffs,
        doc_a,
        doc_h,
        repaired_count=len(branch_diffs),
    )
    payload = dict(job_meta)
    payload["diffs"] = merged_diffs
    payload["comparison_summary"] = repaired_summary
    save_job(Job.model_validate(payload))
    logger.info(f"[{job_id}] repaired branch disclosure diffs: +{len(branch_diffs)}")
    return True


def _branch_repair_needed(job_meta: dict, summary: dict, diffs: list[Diff]) -> bool:
    if (job_meta.get("check_mode") or "ah") != "ah":
        return False
    if str(job_meta.get("status") or "").lower() != JobStatus.DONE.value:
        return False
    if any(diff.rule_id == "branch_asset_scale_match" for diff in diffs):
        return False
    if int(summary.get("branch_diff_count") or 0) != 0:
        return False
    if int(summary.get("a_branch_count") or 0) <= 0 or int(summary.get("h_branch_count") or 0) <= 0:
        return False
    return float(summary.get("branch_alignment_ratio") or 0.0) < 0.6


def _summary_with_repaired_branch_diffs(
    summary: dict,
    diffs: list[Diff],
    doc_a,
    doc_h,
    *,
    repaired_count: int,
) -> dict:
    repaired = dict(summary)
    repaired.update(branch_table_diagnostics(doc_a, doc_h, diffs))
    repaired["result_version"] = _CURRENT_RESULT_VERSION
    repaired["parser_version"] = PARSER_VERSION
    repaired["extraction_engine_version"] = EXTRACTION_ENGINE_VERSION
    repaired["current_extraction_engine_version"] = EXTRACTION_ENGINE_VERSION
    repaired["stale_result"] = False
    repaired["real_diff_count"] = sum(1 for diff in diffs if diff.triage == "real")
    repaired["expected_diff_count"] = sum(1 for diff in diffs if diff.triage == "expected")
    repaired["unresolved_diff_count"] = sum(1 for diff in diffs if diff.triage == "unresolved")
    repaired["total_diff_count"] = len(diffs)
    repaired["branch_repaired_from_source_files"] = True
    repaired["branch_repair_diff_count_added"] = repaired_count
    return repaired
