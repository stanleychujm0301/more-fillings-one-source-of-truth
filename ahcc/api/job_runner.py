"""任务执行调度：子进程隔离（默认）+ inline 回退。

subprocess 模式（settings.job_runner="subprocess"，生产默认）：
- 每个任务 spawn 一个 `python -m ahcc.worker` 子进程跑 Orchestrator；
- 父进程（API 服务）监督循环每 2 秒轮询 progress.json 并写库（DB 单写者）；
- 三种判死条件，任一触发即 kill 子进程并标记失败：
    1. 硬超时：elapsed > job_timeout_seconds
    2. 心跳失联：heartbeat.json mtime 停更超过 job_heartbeat_stale_seconds
       （EasyOCR/pdfplumber 卡死占住 GIL 时心跳线程被饿死，正是该信号）
    3. 异常退出：exit code != 0 且无 result.json
- asyncio.Semaphore(job_max_concurrency) 排队：排队中的任务保持 pending 状态。

inline 模式（JOB_RUNNER=inline，pytest / eval 脚本用）：
- 维持旧行为，任务在服务进程事件循环内执行（routes_job._run_job_background）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from loguru import logger

from ahcc.config import settings
from ahcc.schemas import Job, JobStatus
from ahcc.storage.repository import apply_current_user_context, save_job, save_job_progress

_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLL_INTERVAL_SECONDS = 2.0

# 每个事件循环一把信号量（asyncio.Semaphore 不能跨 loop 复用；测试会反复建 loop）
_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _semaphores.get(loop_id)
    if sem is None:
        sem = asyncio.Semaphore(max(1, int(settings.job_max_concurrency)))
        _semaphores[loop_id] = sem
    return sem


def _worker_command(job_dir: Path) -> list[str]:
    """worker 子进程命令行。独立成函数便于测试注入假 worker 脚本。"""
    return [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "ahcc.worker",
        "--job-dir",
        str(job_dir),
    ]


async def run_job(
    job: Job,
    *,
    bilingual_level: str = "fast",
    visual_review_mode: str = "off",
) -> None:
    """任务执行入口 —— routes_job.create_job 通过 asyncio.create_task 调用。"""
    if (settings.job_runner or "subprocess").strip().lower() != "subprocess":
        from ahcc.api.routes_job import _run_job_background  # 延迟导入避免循环

        await _run_job_background(
            job,
            bilingual_level=bilingual_level,
            visual_review_mode=visual_review_mode,
        )
        return

    try:
        async with _get_semaphore():
            await _run_subprocess(
                job,
                bilingual_level=bilingual_level,
                visual_review_mode=visual_review_mode,
            )
    except Exception as exc:  # noqa: BLE001 - 监督者自身异常也要把任务收尾
        logger.exception(f"[{job.job_id}] job supervisor failed")
        _save_failed(job, f"job supervisor failed: {exc}")


async def _run_subprocess(
    job: Job,
    *,
    bilingual_level: str,
    visual_review_mode: str,
) -> None:
    job_dir = settings.storage_dir / "jobs" / job.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("progress.json", "result.json", "heartbeat.json"):
        try:
            (job_dir / stale).unlink(missing_ok=True)
        except OSError:
            pass
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job": json.loads(job.model_dump_json()),
                "bilingual_level": bilingual_level,
                "visual_review_mode": visual_review_mode,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = await asyncio.create_subprocess_exec(
        *_worker_command(job_dir),
        cwd=str(_REPO_ROOT),
        env=env,
    )
    logger.info(f"[{job.job_id}] worker spawned pid={proc.pid}")
    started = time.monotonic()
    last_progress_text: str | None = None
    latest_job = job

    while True:
        try:
            await asyncio.wait_for(proc.wait(), timeout=_POLL_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

        latest_job, last_progress_text = _persist_progress_if_changed(
            job, job_dir, last_progress_text, latest_job
        )

        elapsed = time.monotonic() - started
        if elapsed > float(settings.job_timeout_seconds):
            await _kill(proc)
            message = (
                f"job timeout: exceeded {int(settings.job_timeout_seconds)} seconds; "
                "please rerun the task with smaller files or a faster review mode."
            )
            logger.warning(f"[{job.job_id}] worker killed: timeout after {elapsed:.0f}s")
            _save_failed(latest_job, message)
            _cleanup_heartbeat(job_dir)
            return

        heartbeat_age = _heartbeat_age_seconds(job_dir, started)
        if heartbeat_age > float(settings.job_heartbeat_stale_seconds):
            await _kill(proc)
            message = (
                "background job interrupted: worker heartbeat lost "
                f"(no progress for more than {int(settings.job_heartbeat_stale_seconds)} seconds); "
                "please rerun the task."
            )
            logger.warning(
                f"[{job.job_id}] worker killed: heartbeat stale for {heartbeat_age:.0f}s"
            )
            _save_failed(latest_job, message)
            _cleanup_heartbeat(job_dir)
            return

    # 子进程已退出 —— 收尾
    latest_job, _ = _persist_progress_if_changed(job, job_dir, last_progress_text, latest_job)
    result_path = job_dir / "result.json"
    if result_path.is_file():
        try:
            completed = Job.model_validate_json(result_path.read_text(encoding="utf-8"))
            save_job(apply_current_user_context(completed))
            logger.info(
                f"[{job.job_id}] worker completed status={completed.status.value} "
                f"exit={proc.returncode}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{job.job_id}] result.json unreadable")
            _save_failed(latest_job, f"worker result unreadable: {exc}")
    elif proc.returncode != 0:
        logger.error(f"[{job.job_id}] worker crashed exit={proc.returncode}")
        _save_failed(
            latest_job,
            f"background job failed: worker crashed (exit code {proc.returncode}); "
            f"see storage/jobs/{job.job_id}/worker.log",
        )
    else:
        _save_failed(latest_job, "background job failed: worker exited without result")
    _cleanup_heartbeat(job_dir)


def _persist_progress_if_changed(
    base_job: Job,
    job_dir: Path,
    last_text: str | None,
    latest_job: Job,
) -> tuple[Job, str | None]:
    """progress.json 有变化时把 status/comparison_summary 同步进 SQLite（父进程单写者）。"""
    progress_path = job_dir / "progress.json"
    try:
        text = progress_path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return latest_job, last_text
    if not text or text == last_text:
        return latest_job, last_text
    try:
        payload = json.loads(text)
        status = JobStatus(str(payload.get("status") or JobStatus.PENDING.value))
        updated = base_job.model_copy(
            update={
                "status": status,
                "comparison_summary": payload.get("comparison_summary") or {},
            }
        )
    except Exception:  # noqa: BLE001 - 半写状态/未知枚举，下轮重读
        return latest_job, last_text
    try:
        save_job_progress(apply_current_user_context(updated))
    except Exception:  # noqa: BLE001
        logger.exception(f"[{base_job.job_id}] progress persistence failed")
    return updated, text


def _heartbeat_age_seconds(job_dir: Path, started_monotonic: float) -> float:
    heartbeat = job_dir / "heartbeat.json"
    try:
        mtime = heartbeat.stat().st_mtime
    except OSError:
        # 心跳文件还没出现：按 spawn 起算（容忍 Windows 冷启动 import 的十几秒）
        return time.monotonic() - started_monotonic
    return max(0.0, time.time() - mtime)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:  # pragma: no cover
        logger.warning(f"worker pid={proc.pid} did not exit after kill")


def _save_failed(job: Job, message: str) -> None:
    from ahcc.api.routes_job import _failed_background_job  # 延迟导入避免循环

    save_job(apply_current_user_context(_failed_background_job(job, message)))


def _cleanup_heartbeat(job_dir: Path) -> None:
    try:
        (job_dir / "heartbeat.json").unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        pass
