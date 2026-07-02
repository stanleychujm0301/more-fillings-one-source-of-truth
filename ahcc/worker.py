"""核查任务 worker 子进程入口。

    python -X utf8 -m ahcc.worker --job-dir storage/jobs/{job_id}

设计（对应"任务经常挂掉"的修复）：
- 任务在独立进程里跑 Orchestrator，父进程（API 服务）可在超时/卡死时直接 kill——
  asyncio.to_thread 里卡死的 pdfplumber/EasyOCR C 扩展线程在进程内无法终止，
  只有进程隔离能真正回收资源；native crash 也不再连累服务进程。
- 进程间协议全部走 job_dir 下的文件（原子写）：
    job.json        父进程写入：Job 字段 + bilingual_level/visual_review_mode
    heartbeat.json  worker 周期刷新 {ts, pid}；mtime 停更 = 卡死信号
    progress.json   每次进度回调刷新 {status, comparison_summary, updated_at}
    result.json     结束时写入完整 Job JSON（成功或失败均写）
    worker.log      任务级日志（含 traceback）
- worker 全程不写 SQLite（数据库保持 API 进程单写者，规避 journal_mode=MEMORY
  多进程写坏库的风险）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

_HEARTBEAT_INTERVAL_SECONDS = 15.0
_PARSE_HEARTBEAT_MIN_GAP_SECONDS = 5.0

# 心跳有两个写入方（后台心跳线程 + 解析循环内的同步 hook），Windows 上并发
# os.replace 同名文件会抛 WinError 32——加锁串行化，且临时文件名带线程标识。
_heartbeat_lock = threading.Lock()


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _touch_heartbeat(job_dir: Path) -> None:
    # 心跳是尽力而为：单次写失败（如杀毒软件短暂锁文件）不影响任务，下一拍重试
    with _heartbeat_lock:
        try:
            _atomic_write_text(
                job_dir / "heartbeat.json",
                json.dumps({"ts": time.time(), "pid": os.getpid()}),
            )
        except OSError:
            pass


def _start_heartbeat_thread(job_dir: Path) -> None:
    """后台心跳线程。若重型 C 扩展调用长期占住 GIL，本线程会被饿死、
    heartbeat.json 停更——父进程正是靠这个信号判定 worker 卡死并 kill。"""

    def _beat() -> None:
        while True:
            try:
                _touch_heartbeat(job_dir)
            except Exception:  # pragma: no cover - 心跳写失败不致命
                pass
            time.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    threading.Thread(target=_beat, daemon=True, name="ahcc-heartbeat").start()


def _install_parse_heartbeat(job_dir: Path) -> None:
    """解析长循环内的同步心跳：防止占 GIL 的逐页循环饿死后台心跳线程。限频写。"""
    from ahcc.parser.pdf_h_html import set_parse_heartbeat

    last: list[float] = [0.0]

    def _hook() -> None:
        now = time.monotonic()
        if now - last[0] >= _PARSE_HEARTBEAT_MIN_GAP_SECONDS:
            last[0] = now
            _touch_heartbeat(job_dir)

    set_parse_heartbeat(_hook)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AHCC job worker subprocess")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args(argv)
    job_dir = Path(args.job_dir)

    logger.add(
        job_dir / "worker.log",
        level="INFO",
        backtrace=True,
        diagnose=False,
        encoding="utf-8",
    )

    payload = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    bilingual_level = str(payload.get("bilingual_level") or "fast")
    visual_review_mode = str(payload.get("visual_review_mode") or "off")

    from ahcc.orchestrator import Orchestrator
    from ahcc.schemas import Job, JobStatus

    job = Job.model_validate(payload["job"])
    logger.info(f"[{job.job_id}] worker started pid={os.getpid()}")

    _start_heartbeat_thread(job_dir)
    _touch_heartbeat(job_dir)
    _install_parse_heartbeat(job_dir)

    def _progress_callback(updated: Job) -> None:
        try:
            _atomic_write_text(
                job_dir / "progress.json",
                json.dumps(
                    {
                        "status": updated.status.value,
                        "comparison_summary": updated.comparison_summary,
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        except Exception:  # pragma: no cover - 进度写失败不影响任务
            logger.exception(f"[{job.job_id}] progress write failed")

    try:
        completed = asyncio.run(
            Orchestrator().run(
                job.a_file,
                job.h_file,
                job.company_name,
                job.check_mode,
                bilingual_level=bilingual_level,
                visual_review_mode=visual_review_mode,
                job=job,
                progress_callback=_progress_callback,
            )
        )
    except Exception as exc:  # noqa: BLE001 - Orchestrator 自身兜底后仍可能有极端异常
        logger.exception(f"[{job.job_id}] worker crashed in orchestrator")
        completed = job.model_copy(
            update={
                "status": JobStatus.FAILED,
                "error": f"worker exception: {exc}",
                "finished_at": datetime.utcnow(),
                "duration_seconds": (datetime.utcnow() - job.started_at).total_seconds(),
            }
        )

    _atomic_write_text(job_dir / "result.json", completed.model_dump_json())
    logger.info(f"[{job.job_id}] worker finished status={completed.status.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
