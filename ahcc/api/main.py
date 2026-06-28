"""FastAPI 应用入口（P1 实现）。"""

from __future__ import annotations

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
    init_db()
    # 启动即打印引擎版本，便于一眼发现「改了代码但服务没重启」的旧进程
    logger.info(
        f"AHCC 启动：extraction_engine={EXTRACTION_ENGINE_VERSION} result_version={RESULT_VERSION}"
    )
    yield


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
    }


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
