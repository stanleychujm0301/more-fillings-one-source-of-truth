"""/health 端点运行时行为测试（区别于 test_ui_new_app.py 里按源码 token 的静态断言）。"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from ahcc.api import main as api_main


def test_ocr_health_uses_find_spec_without_importing_ocr_fallback(monkeypatch):
    """B7: /health 曾经 `import ahcc.parser.ocr_fallback`，而该模块在导入期直接
    `import easyocr` / `from paddleocr import PaddleOCR`，首次调用 /health 就要付出数秒
    的 import 开销和数百 MB 内存。改为只用 importlib.util.find_spec 探测是否已安装，
    不应真正触发 ahcc.parser.ocr_fallback（及其重型依赖）的 import。"""
    monkeypatch.delitem(sys.modules, "ahcc.parser.ocr_fallback", raising=False)

    result = api_main._ocr_health()

    assert "ahcc.parser.ocr_fallback" not in sys.modules
    assert set(result) == {"ocr_engine_available", "paddleocr", "easyocr"}
    assert isinstance(result["ocr_engine_available"], bool)
    assert isinstance(result["paddleocr"], bool)
    assert isinstance(result["easyocr"], bool)


def test_health_endpoint_runtime_payload_shape(monkeypatch):
    """/health 的运行时响应应包含 visual_ocr 探测结果，且不应该抛异常。"""
    monkeypatch.setattr(api_main, "init_db", lambda: None)

    with TestClient(api_main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert set(payload["visual_ocr"]) == {"ocr_engine_available", "paddleocr", "easyocr"}


def test_health_endpoint_exposes_job_queue_and_upload_limit(monkeypatch):
    """B5: /health 应额外暴露排队状态（当前运行中的 job_id + 排队列表）与上传大小上限
    （MB），前者供运维/演示排查排队卡顿，后者供前端展示上传限制而不必硬编码。"""
    from ahcc.api import job_runner

    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(job_runner, "_running_job_id", "job-running-1")
    monkeypatch.setattr(job_runner, "_queued_job_ids", ["job-queued-1", "job-queued-2"])
    monkeypatch.setattr(api_main.settings, "upload_max_bytes", 80 * 1024 * 1024, raising=False)

    with TestClient(api_main.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_queue"] == {
        "running": "job-running-1",
        "queued": ["job-queued-1", "job-queued-2"],
    }
    assert payload["upload_max_mb"] == 80.0
