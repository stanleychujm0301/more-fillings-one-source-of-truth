"""FastAPI 应用入口（P1 实现）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from loguru import logger

from ahcc.api.routes_job import router as job_router
from ahcc.api.routes_review import router as review_router
from ahcc.api.routes_user import router as user_router
from ahcc.config import settings
from ahcc.parser.audit import EXTRACTION_ENGINE_VERSION
from ahcc.storage.models import init_db
from ahcc.storage.repository import (
    mark_interrupted_running_jobs_failed,
    mark_stale_running_jobs_failed,
)

try:  # 结果 schema 版本（仅供 /health 自检；缺失不应影响启动）
    from ahcc.storage.repository import _CURRENT_RESULT_VERSION as RESULT_VERSION
except Exception:  # pragma: no cover
    RESULT_VERSION = None

STATIC_DIR = Path(__file__).resolve().parents[2] / "ui" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
UI_NEW_DIST = Path(__file__).resolve().parents[2] / "ui-new" / "dist"
UI_NEW_INDEX = UI_NEW_DIST / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    _setup_file_logging()
    init_db()
    _cleanup_orphan_workers()
    interrupted_count = mark_interrupted_running_jobs_failed()
    if interrupted_count:
        logger.warning(f"Marked interrupted running jobs as failed: {interrupted_count}")
    # 启动即打印引擎版本，便于一眼发现「改了代码但服务没重启」的旧进程
    logger.info(
        f"AHCC 启动：extraction_engine={EXTRACTION_ENGINE_VERSION} result_version={RESULT_VERSION}"
    )
    stale_task = asyncio.create_task(_mark_stale_jobs_periodically())
    try:
        yield
    finally:
        # 部分测试会全局 monkeypatch asyncio.create_task 以拦截任务调度，
        # 此时返回的桩对象没有 .cancel()；只在拿到真实 Task 时才取消。
        if hasattr(stale_task, "cancel"):
            stale_task.cancel()


def _setup_file_logging() -> None:
    """任务/请求的 traceback 落盘 —— 此前 loguru 只写 stderr，重启后无法追查任务为何失败。"""
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "server.log",
            rotation="20 MB",
            retention=10,
            level=settings.log_level,
            backtrace=True,
            diagnose=False,
            enqueue=True,
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover - 日志失败不应阻断启动
        logger.warning(f"file logging setup failed: {exc}")


async def _mark_stale_jobs_periodically() -> None:
    """兜底：监督者自身异常退出时，超时未收尾的 running 任务由此标记失败。"""
    stale_after = float(settings.job_timeout_seconds) + 120.0
    while True:
        await asyncio.sleep(60)
        try:
            count = mark_stale_running_jobs_failed(stale_after_seconds=stale_after)
            if count:
                logger.warning(f"Marked stale running jobs as failed: {count}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            logger.warning(f"stale job sweep failed: {exc}")


def _cleanup_orphan_workers() -> None:
    """服务重启后清理上一进程遗留的 worker 子进程（按 heartbeat.json 记录的 pid）。"""
    jobs_dir = settings.storage_dir / "jobs"
    if not jobs_dir.is_dir():
        return
    import json

    for heartbeat in jobs_dir.glob("*/heartbeat.json"):
        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except Exception:
            continue
        if pid <= 0:
            continue
        try:
            import psutil  # type: ignore

            proc = psutil.Process(pid)
            if "ahcc.worker" in " ".join(proc.cmdline()):
                proc.kill()
                logger.warning(f"killed orphan worker pid={pid} ({heartbeat.parent.name})")
        except ImportError:
            # 无 psutil 时不盲杀 pid（可能已被复用），交由任务超时兜底
            return
        except Exception:
            continue
        finally:
            try:
                heartbeat.unlink(missing_ok=True)
            except OSError:
                pass


app = FastAPI(
    title="AHCC — A+H Consistency Checker",
    description="KPMG 黑客松 Challenge #1 — A+H 股年报数据一致性核查 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """健康检查，并回显当前进程实际加载的引擎版本（用于核对是否为旧进程）。"""
    return {
        "status": "ok",
        "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
        "result_version": RESULT_VERSION,
        "branch_repair_version": 1,
        "visual_ocr": _ocr_health(),
        "storage": _storage_health(),
    }


def _ocr_health() -> dict:
    try:
        from ahcc.parser.ocr_fallback import _EASYOCR_AVAILABLE, _PADDLEOCR_AVAILABLE
    except Exception:  # pragma: no cover
        return {
            "ocr_engine_available": False,
            "paddleocr": False,
            "easyocr": False,
        }
    return {
        "ocr_engine_available": bool(_PADDLEOCR_AVAILABLE or _EASYOCR_AVAILABLE),
        "paddleocr": bool(_PADDLEOCR_AVAILABLE),
        "easyocr": bool(_EASYOCR_AVAILABLE),
    }


def _storage_health() -> dict:
    storage_dir = settings.storage_dir
    sqlite_path = settings.sqlite_path
    return {
        "storage_dir": str(storage_dir),
        "sqlite_path": str(sqlite_path),
        "storage_dir_exists": storage_dir.exists(),
        "sqlite_parent_exists": sqlite_path.parent.exists(),
        "storage_on_var_data": _path_is_under(storage_dir, Path("/var/data")),
        "sqlite_on_var_data": _path_is_under(sqlite_path, Path("/var/data")),
    }


def _path_is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _no_cache_index() -> FileResponse:
    response = FileResponse(INDEX_HTML)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _no_cache_ui_new_index()


@app.get("/index.html", include_in_schema=False)
def index_html() -> FileResponse:
    return _no_cache_ui_new_index()


app.include_router(job_router, prefix="/api/jobs", tags=["jobs"])
app.include_router(review_router, prefix="/api/reviews", tags=["reviews"])
app.include_router(user_router, prefix="/api", tags=["users"])

if (UI_NEW_DIST / "assets").is_dir():
    app.mount("/app/assets", StaticFiles(directory=str(UI_NEW_DIST / "assets")), name="ui-new-assets")


def _no_cache_ui_new_index() -> FileResponse:
    if not UI_NEW_INDEX.is_file():
        raise HTTPException(status_code=404, detail="React UI has not been built")
    response = FileResponse(UI_NEW_INDEX)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/app", include_in_schema=False)
def ui_new_app() -> FileResponse:
    return _no_cache_ui_new_index()


@app.get("/app/", include_in_schema=False)
def ui_new_app_slash() -> FileResponse:
    return _no_cache_ui_new_index()


@app.get("/app/{full_path:path}", include_in_schema=False)
def ui_new_hash_fallback(full_path: str) -> FileResponse:
    return _no_cache_ui_new_index()

# 用基于 __file__ 的绝对路径，避免非项目根目录启动 uvicorn 时找不到目录而崩溃
if STATIC_DIR.is_dir():
    app.mount("/legacy", StaticFiles(directory=str(STATIC_DIR), html=True), name="legacy-static")
else:  # pragma: no cover - 仅在前端资源缺失时触发
    from loguru import logger

    logger.warning(f"静态前端目录不存在，跳过挂载: {STATIC_DIR}")
