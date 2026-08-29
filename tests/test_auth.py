"""注册 / 登录 / 会话 / 项目组分流与组内共享的端到端测试。

conftest 默认 AHCC_AUTH_DISABLED=1（存量测试旁路），本文件通过
monkeypatch.setattr(settings, "auth_disabled", False) 显式打开真实认证路径。

种子数据（models._seed_demo_accounts，密码均为 demo1234）：
- chu-stanley：SH/FS3 + SH/IPO 专项（一人多组）
- chen-yiran：SH/FS3（stanley 的同组同事）
- zhang-wei：BJ/FS1（异组，用于隔离验证）
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ahcc.api import main as api_main
from ahcc.config import settings
from ahcc.storage import models, repository

DEMO_PASSWORD = "demo1234"


@pytest.fixture
def workspace_tmp():
    path = Path("storage") / "test-artifacts" / f"auth-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def auth_client(monkeypatch, workspace_tmp):
    """真实认证模式 + 独立临时库；yield 一个未登录的 TestClient。"""
    monkeypatch.setattr(models, "_RECOVERED_SQLITE_PATH", workspace_tmp / "missing.db")
    monkeypatch.setattr(settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(settings, "sqlite_path", workspace_tmp / "ahcc.db")
    monkeypatch.setattr(settings, "auth_disabled", False)
    models.init_db()
    with TestClient(api_main.app) as client:
        yield client


def _login(client: TestClient, username: str, password: str = DEMO_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _register(client: TestClient, username: str, **overrides):
    payload = {
        "username": username,
        "password": "s3cret-pass",
        "display_name": "New Hire",
        "group_mode": "join",
        "project_group_id": "sh-fs3",
    }
    payload.update(overrides)
    return client.post("/api/auth/register", json=payload)


def _seed_job(job_id: str, owner_id: str, owner_name: str, group_id: str, group_name: str) -> None:
    with models.get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
            (job_id, company_name, check_mode, a_file, h_file, status, started_at,
             owner_user_id, owner_display_name, project_group_id, project_group_name,
             comparison_summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                f"{company_safe(job_id)} Co",
                "ah",
                "a.pdf",
                "h.pdf",
                "done",
                "2026-08-01T08:00:00",
                owner_id,
                owner_name,
                group_id,
                group_name,
                json.dumps({"result_version": 12}),
            ),
        )
        conn.commit()


def company_safe(job_id: str) -> str:
    return job_id.replace("-", " ").title()


def _seed_diff(diff_id: str, job_id: str) -> None:
    # payload_json 必须能通过 Diff.model_validate_json：任务详情接口在组校验前会先走
    # _repair_branch_diffs_if_needed（系统内部修复，enforce_group=False）加载全部差异
    payload = {
        "diff_id": diff_id,
        "diff_type": "numeric",
        "severity": "high",
        "triage": "real",
        "canonical_key": "revenue",
        "topic": {"zh": "营业收入", "en": "Revenue"},
        "summary": {"zh": "营业收入 A/H 不一致", "en": "Revenue mismatch"},
        "a_value": 100.0,
        "h_value": 120.0,
        "delta": 20.0,
    }
    with models.get_conn() as conn:
        conn.execute(
            """INSERT INTO diffs (diff_id, job_id, diff_type, severity, canonical_key, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                diff_id,
                job_id,
                "numeric",
                "high",
                "revenue",
                json.dumps(payload),
                "2026-08-01T08:01:00",
            ),
        )
        conn.commit()


# ── 1. 注册 ────────────────────────────────────────────────────────────────


def test_register_success_sets_cookie_and_session_payload(auth_client):
    response = _register(auth_client, "li-na", display_name="Li, Na")

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "ahcc_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie

    payload = response.json()
    assert payload["user"]["user_id"] == "li-na"
    assert payload["project_group"]["id"] == "sh-fs3"
    assert len(payload["memberships"]) == 1
    assert payload["memberships"][0]["group_id"] == "sh-fs3"
    assert payload["memberships"][0]["is_active"] is True

    # 注册即登录：同一 cookie 直接命中受保护端点
    current = auth_client.get("/api/session/current")
    assert current.status_code == 200
    assert current.json()["user"]["user_id"] == "li-na"


def test_register_duplicate_username_and_case_variant_conflict(auth_client):
    assert _register(auth_client, "new-hire").status_code == 200
    assert _register(auth_client, "new-hire").status_code == 409
    # 用户名大小写不敏感（user_id 统一小写）
    assert _register(auth_client, "NEW-Hire").status_code == 409
    # 与种子账号冲突
    assert _register(auth_client, "Chu-Stanley").status_code == 409


def test_register_validation_errors(auth_client):
    # 弱密码（<6 位）
    assert _register(auth_client, "weak-pw", password="12345").status_code == 422
    # 非法用户名字符
    assert _register(auth_client, "bad name!").status_code == 422
    # join 不存在的项目组
    assert _register(auth_client, "ghost-joiner", project_group_id="no-such-group").status_code == 404
    # create 模式缺组名
    assert (
        _register(auth_client, "no-group-name", group_mode="create", project_group_name="  ").status_code == 422
    )


def test_register_create_new_group_auto_membership(auth_client):
    response = _register(
        auth_client,
        "wang-fang",
        display_name="Wang, Fang",
        group_mode="create",
        project_group_name="SZ/IPO 一组",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_group"]["name"] == "SZ/IPO 一组"
    assert payload["memberships"][0]["is_active"] is True
    # 新组出现在公开候选列表里
    groups = auth_client.get("/api/auth/groups").json()
    assert any(g["name"] == "SZ/IPO 一组" for g in groups)


# ── 2. 登录 / 登出 ─────────────────────────────────────────────────────────


def test_login_success_failure_and_logout(auth_client):
    # 用户名大小写不敏感
    ok = _login(auth_client, "CHU-Stanley")
    assert ok.status_code == 200
    assert ok.json()["user"]["user_id"] == "chu-stanley"
    assert ok.json()["project_group"]["id"] == "sh-fs3"
    assert {m["group_id"] for m in ok.json()["memberships"]} == {"sh-fs3", "sh-ipo"}

    # 密码错误与用户名不存在统一 401 文案（防枚举）
    bad_pw = _login(auth_client, "chu-stanley", "wrong-password")
    no_user = _login(auth_client, "no-such-user")
    assert bad_pw.status_code == 401
    assert no_user.status_code == 401
    assert bad_pw.json()["detail"] == no_user.json()["detail"]

    # 登出幂等，登出后会话失效
    assert auth_client.post("/api/auth/logout").status_code == 200
    assert auth_client.get("/api/session/current").status_code == 401
    assert auth_client.post("/api/auth/logout").status_code == 200


def test_unauthenticated_requests_are_rejected(auth_client):
    assert auth_client.get("/api/jobs/history").status_code == 401
    assert auth_client.get("/api/jobs/some-job").status_code == 401
    assert auth_client.get("/api/jobs/some-job/diffs").status_code == 401
    assert (
        auth_client.post("/api/reviews/", json={"diff_id": "d1", "status": "reviewed"}).status_code == 401
    )
    assert auth_client.patch("/api/users/current", json={"display_name": "X"}).status_code == 401
    assert auth_client.get("/api/session/current").status_code == 401
    assert auth_client.post("/api/session/active-group", json={"group_id": "sh-fs3"}).status_code == 401
    assert auth_client.get("/api/groups/my").status_code == 401
    assert auth_client.post("/api/groups/join", json={"mode": "join", "group_id": "sh-fs3"}).status_code == 401

    # 公开端点不受认证影响
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/api/auth/groups").status_code == 200
    groups = auth_client.get("/api/auth/groups").json()
    assert {g["id"] for g in groups} == {"sh-fs3", "sh-ipo", "bj-fs1"}
    assert next(g for g in groups if g["id"] == "sh-fs3")["member_count"] == 2


def test_expired_session_is_rejected(auth_client):
    assert _login(auth_client, "chu-stanley").status_code == 200
    assert auth_client.get("/api/session/current").status_code == 200

    with models.get_conn() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00'")
        conn.commit()

    assert auth_client.get("/api/session/current").status_code == 401


# ── 3. 组内共享 / 组间隔离 ─────────────────────────────────────────────────


def test_group_sharing_between_colleagues(auth_client):
    _seed_job("stanley-job", "chu-stanley", "Chu, Stanley", "sh-fs3", "SH/FS3")

    # 提交人本人可见
    assert _login(auth_client, "chu-stanley").status_code == 200
    project = auth_client.get("/api/jobs/history?scope=project&limit=10")
    assert "stanley-job" in {item["job_id"] for item in project.json()}
    auth_client.post("/api/auth/logout")

    # 同组同事 chen-yiran：项目组历史可见、详情可读
    assert _login(auth_client, "chen-yiran").status_code == 200
    project = auth_client.get("/api/jobs/history?scope=project&limit=10")
    assert {item["job_id"] for item in project.json()} == {"stanley-job"}
    assert project.json()[0]["owner_display_name"] == "Chu, Stanley"
    assert auth_client.get("/api/jobs/stanley-job").status_code == 200
    # 但"我的"范围不含同事任务
    mine = auth_client.get("/api/jobs/history?scope=mine&limit=10")
    assert mine.json() == []


def test_cross_group_isolation(auth_client):
    _seed_job("stanley-job", "chu-stanley", "Chu, Stanley", "sh-fs3", "SH/FS3")
    _seed_diff("stanley-diff", "stanley-job")

    assert _login(auth_client, "zhang-wei").status_code == 200
    # 历史为空
    assert auth_client.get("/api/jobs/history?scope=project&limit=10").json() == []
    # 详情 / diffs / 报告下载全部 404（不区分不存在与无权访问）
    assert auth_client.get("/api/jobs/stanley-job").status_code == 404
    assert auth_client.get("/api/jobs/stanley-job/diffs").status_code == 404
    assert auth_client.get("/api/jobs/stanley-job/report.pdf").status_code == 404
    assert auth_client.get("/api/jobs/stanley-job/report.xlsx").status_code == 404
    # 越权提交 review 同样 404
    review = auth_client.post(
        "/api/reviews/", json={"diff_id": "stanley-diff", "status": "reviewed"}
    )
    assert review.status_code == 404

    # 同组同事可以正常 review，reviewed_by 默认当前登录用户
    auth_client.post("/api/auth/logout")
    assert _login(auth_client, "chen-yiran").status_code == 200
    ok = auth_client.post("/api/reviews/", json={"diff_id": "stanley-diff", "status": "reviewed"})
    assert ok.status_code == 200
    with models.get_conn() as conn:
        row = conn.execute(
            "SELECT reviewed_by FROM reviews WHERE diff_id = ?", ("stanley-diff",)
        ).fetchone()
    assert row["reviewed_by"] == "Chen, Yiran"


# ── 4. 一人多组切换 ────────────────────────────────────────────────────────


def test_multi_group_switching_filters_views(auth_client):
    _seed_job("fs3-job", "chu-stanley", "Chu, Stanley", "sh-fs3", "SH/FS3")
    _seed_job("ipo-job", "chu-stanley", "Chu, Stanley", "sh-ipo", "SH/IPO 专项")

    assert _login(auth_client, "chu-stanley").status_code == 200
    # 默认激活 sh-fs3
    project = auth_client.get("/api/jobs/history?scope=project&limit=10")
    assert {item["job_id"] for item in project.json()} == {"fs3-job"}

    # 切到 SH/IPO 专项：只见该组任务，sh-fs3 的任务 404
    switched = auth_client.post("/api/session/active-group", json={"group_id": "sh-ipo"})
    assert switched.status_code == 200
    payload = switched.json()
    assert payload["project_group"]["id"] == "sh-ipo"
    assert next(m for m in payload["memberships"] if m["group_id"] == "sh-ipo")["is_active"] is True

    project = auth_client.get("/api/jobs/history?scope=project&limit=10")
    assert {item["job_id"] for item in project.json()} == {"ipo-job"}
    assert auth_client.get("/api/jobs/fs3-job").status_code == 404

    # 切回 sh-fs3 恢复
    assert auth_client.post("/api/session/active-group", json={"group_id": "sh-fs3"}).status_code == 200
    assert auth_client.get("/api/jobs/fs3-job").status_code == 200


def test_switch_group_requires_membership(auth_client):
    assert _login(auth_client, "chu-stanley").status_code == 200
    # 非成员 → 403
    denied = auth_client.post("/api/session/active-group", json={"group_id": "bj-fs1"})
    assert denied.status_code == 403
    # 不存在的组 → 404
    missing = auth_client.post("/api/session/active-group", json={"group_id": "no-such-group"})
    assert missing.status_code == 404
    # 激活组不受影响
    assert auth_client.get("/api/session/current").json()["project_group"]["id"] == "sh-fs3"


def test_active_group_persists_across_sessions(auth_client):
    assert _login(auth_client, "chu-stanley").status_code == 200
    assert auth_client.post("/api/session/active-group", json={"group_id": "sh-ipo"}).status_code == 200
    auth_client.post("/api/auth/logout")

    # 重新登录（新 session）：激活组回落到 profile 记录的上次激活组
    relogin = _login(auth_client, "chu-stanley")
    assert relogin.status_code == 200
    assert relogin.json()["project_group"]["id"] == "sh-ipo"


# ── 5. 加入 / 创建项目组（登录态） ─────────────────────────────────────────


def test_join_group_is_idempotent_and_switches_active(auth_client):
    assert _login(auth_client, "chen-yiran").status_code == 200

    first = auth_client.post("/api/groups/join", json={"mode": "join", "group_id": "sh-ipo"})
    assert first.status_code == 200
    assert first.json()["already_member"] is False
    # join 后自动切换为激活组
    assert first.json()["session"]["project_group"]["id"] == "sh-ipo"

    second = auth_client.post("/api/groups/join", json={"mode": "join", "group_id": "sh-ipo"})
    assert second.status_code == 200
    assert second.json()["already_member"] is True

    memberships = second.json()["session"]["memberships"]
    assert sorted(m["group_id"] for m in memberships) == ["sh-fs3", "sh-ipo"]

    # 不存在的组 → 404
    assert (
        auth_client.post("/api/groups/join", json={"mode": "join", "group_id": "nope"}).status_code == 404
    )

    # /api/groups/my 与 session memberships 一致
    my = auth_client.get("/api/groups/my")
    assert my.status_code == 200
    assert {g["group_id"] for g in my.json()} == {"sh-fs3", "sh-ipo"}
    assert next(g for g in my.json() if g["group_id"] == "sh-ipo")["is_active"] is True


def test_join_group_create_mode(auth_client):
    assert _login(auth_client, "zhang-wei").status_code == 200
    created = auth_client.post("/api/groups/join", json={"mode": "create", "group_name": "GZ/FS2"})
    assert created.status_code == 200
    assert created.json()["already_member"] is False
    assert created.json()["session"]["project_group"]["name"] == "GZ/FS2"
    # 同名再建（大小写/空白变体）→ 复用已有组而非报错
    again = auth_client.post("/api/groups/join", json={"mode": "create", "group_name": " gz/fs2 "})
    assert again.status_code == 200
    assert again.json()["group"]["group_name"] == "GZ/FS2"


# ── 6. 并发与迁移 ──────────────────────────────────────────────────────────


def test_concurrent_register_same_username_exactly_one_wins(auth_client):
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt() -> None:
        barrier.wait(timeout=10)
        try:
            repository.create_user(
                user_id="race-user",
                display_name="Race User",
                password="s3cret-pass",
                office_line="",
                role_title="",
                project_group_id="sh-fs3",
                project_group_name="SH/FS3",
            )
            outcomes.append("created")
        except repository.DuplicateUserError:
            outcomes.append("duplicate")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["created", "duplicate"]
    # 赢家的数据完整可用：能登录且只有一条 membership
    assert _login(auth_client, "race-user", "s3cret-pass").status_code == 200
    with models.get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM user_group_memberships WHERE user_id = ?", ("race-user",)
        ).fetchone()["c"]
    assert count == 1


def test_legacy_users_without_memberships_are_backfilled(auth_client, monkeypatch, workspace_tmp):
    # 模拟登录功能上线前的老库：用户存在但没有任何 membership 行
    with models.get_conn() as conn:
        conn.execute(
            """INSERT INTO user_profiles
            (user_id, display_name, office_line, role_title, avatar_path,
             project_group_id, project_group_name, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("legacy-user", "Legacy User", "SH/FS3", "Audit Associate", "", "sh-fs3", "SH/FS3", ""),
        )
        conn.execute("DELETE FROM user_group_memberships WHERE user_id = ?", ("chen-yiran",))
        conn.commit()

    models.init_db()  # 重跑 schema 链条（init_db 不受 once-per-path 守卫限制）

    with models.get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, group_id FROM user_group_memberships WHERE user_id IN (?, ?) ORDER BY user_id",
            ("legacy-user", "chen-yiran"),
        ).fetchall()
    assert [(row["user_id"], row["group_id"]) for row in rows] == [
        ("chen-yiran", "sh-fs3"),
        ("legacy-user", "sh-fs3"),
    ]


# ── 7. 旁路一致性 ──────────────────────────────────────────────────────────


def test_auth_disabled_bypass_returns_contract_complete_session(monkeypatch, workspace_tmp):
    monkeypatch.setattr(models, "_RECOVERED_SQLITE_PATH", workspace_tmp / "missing.db")
    monkeypatch.setattr(settings, "storage_dir", workspace_tmp)
    monkeypatch.setattr(settings, "sqlite_path", workspace_tmp / "ahcc.db")
    monkeypatch.setattr(settings, "auth_disabled", True)  # 与 conftest 默认一致，显式声明
    models.init_db()

    with TestClient(api_main.app) as client:
        current = client.get("/api/session/current")

    assert current.status_code == 200
    payload = current.json()
    assert payload["user"]["user_id"] == "chu-stanley"
    assert payload["project_group"]["id"] == "sh-fs3"
    # 旁路同样走 resolve_session_profile：memberships 契约不缺字段
    assert len(payload["memberships"]) >= 1
    assert any(m["group_id"] == "sh-fs3" and m["is_active"] for m in payload["memberships"])
