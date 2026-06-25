from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ahcc.api.main import app


def test_static_html_points_file_mode_to_local_api() -> None:
    html = Path("ui/static/index.html").read_text(encoding="utf-8")

    assert "window.location.protocol === 'file:'" in html
    assert "http://127.0.0.1:8000" in html
    assert "const API_BASE =" in html


def test_api_allows_file_origin_for_direct_html_open() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/jobs/history?limit=10",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in {"*", "null"}
    assert "GET" in response.headers["access-control-allow-methods"]
