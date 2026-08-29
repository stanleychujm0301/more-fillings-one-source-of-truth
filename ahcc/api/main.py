"""FastAPI 应用入口（P1 实现）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from loguru import logger

from ahcc.api.deps import get_current_user
from ahcc.api.job_runner import current_running_job_id, queued_job_ids
from ahcc.api.routes_auth import router as auth_router
from ahcc.api.routes_groups import router as group_router
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

_GATE_COOKIE = "ahcc_gate"


@app.middleware("http")
async def _public_gate(request: Request, call_next):
    """答辩救急用的公网访问网关（见 config.Settings.api_auth_token 注释）。

    未配置 AHCC_API_AUTH_TOKEN 时完全不生效，本地开发/测试/eval 行为不变。
    """
    token = settings.api_auth_token
    if not token:
        return await call_next(request)

    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path != "/health":
        cookie_ok = request.cookies.get(_GATE_COOKIE) == token
        header_ok = request.headers.get("x-api-key") == token
        if not (cookie_ok or header_ok):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

    response = await call_next(request)
    if request.method == "GET" and request.cookies.get(_GATE_COOKIE) != token:
        response.set_cookie(_GATE_COOKIE, token, samesite="lax", path="/", max_age=86400)
    return response


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
        "job_queue": _job_queue_health(),
        "upload_max_mb": round(settings.upload_max_bytes / (1024 * 1024), 2),
    }


def _job_queue_health() -> dict:
    return {
        "running": current_running_job_id(),
        "queued": queued_job_ids(),
    }


def _ocr_health() -> dict:
    # 只探测包是否*可安装/已安装*（importlib.util.find_spec 不会真正执行模块代码），
    # 不 import ahcc.parser.ocr_fallback —— 该模块在导入期会直接 `import easyocr` /
    # `from paddleocr import PaddleOCR`，第一次调用 /health 就会付出数秒 import 开销和
    # 数百 MB 内存。变量名保持 _PADDLEOCR_AVAILABLE / _EASYOCR_AVAILABLE 不变。
    import importlib.util

    try:
        _PADDLEOCR_AVAILABLE = importlib.util.find_spec("paddleocr") is not None
        _EASYOCR_AVAILABLE = importlib.util.find_spec("easyocr") is not None
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


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _no_cache_ui_new_index()


@app.get("/index.html", include_in_schema=False)
def index_html() -> FileResponse:
    return _no_cache_ui_new_index()


# /api/auth/* 为公开端点（注册/登录/登出/组候选列表）；其余 API router 统一经
# get_current_user 依赖鉴权（未登录 401）。/health 与静态资源不在受保护 router 下，天然放行。
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(job_router, prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])
app.include_router(review_router, prefix="/api/reviews", tags=["reviews"], dependencies=[Depends(get_current_user)])
app.include_router(user_router, prefix="/api", tags=["users"], dependencies=[Depends(get_current_user)])
app.include_router(group_router, prefix="/api/groups", tags=["groups"], dependencies=[Depends(get_current_user)])

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
