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
