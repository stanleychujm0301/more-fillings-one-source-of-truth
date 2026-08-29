"""SQLite 表结构（P3 实现）— 用最简的 sqlite3 + dataclass 实现，避免 ORM 复杂度。

表：
- jobs: 任务元信息
- diffs: 差异记录（JSON 字段存证据链）
- reviews: 审计师覆盖记录（"已审/可接受/需追问"）
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ahcc.auth import hash_password
from ahcc.config import settings
from ahcc.user_context import (
    CURRENT_PROJECT_GROUP_ID,
    CURRENT_PROJECT_GROUP_NAME,
    DEFAULT_USER_PROFILE,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    company_name TEXT,
    check_mode TEXT DEFAULT 'ah',
    owner_user_id TEXT,
    owner_display_name TEXT,
    project_group_id TEXT,
    project_group_name TEXT,
    a_file TEXT NOT NULL,
    h_file TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_seconds REAL,
    error TEXT,
    profile_a_json TEXT,
    profile_h_json TEXT,
    coverage_items_json TEXT,
    comparison_summary_json TEXT
);

CREATE TABLE IF NOT EXISTS diffs (
    diff_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    diff_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    canonical_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_diffs_job ON diffs(job_id);

CREATE TABLE IF NOT EXISTS reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    diff_id TEXT NOT NULL REFERENCES diffs(diff_id),
    status TEXT NOT NULL,
    note TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    office_line TEXT NOT NULL,
    role_title TEXT,
    project_group_id TEXT NOT NULL,
    project_group_name TEXT NOT NULL,
    avatar_path TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES user_profiles(user_id),
    active_group_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS project_groups (
    group_id TEXT PRIMARY KEY,
    group_name TEXT NOT NULL UNIQUE,
    created_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_group_memberships (
    user_id TEXT NOT NULL REFERENCES user_profiles(user_id),
    group_id TEXT NOT NULL REFERENCES project_groups(group_id),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (user_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_group ON user_group_memberships(group_id);
"""

_RECOVERED_SQLITE_PATH = Path("./scratch/ahcc.recovered.db")


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    if os.name == "nt":
        # The Windows demo workspace has shown intermittent disk I/O errors when
        # SQLite creates rollback journal files. Keep the journal in memory so the
        # UI history remains readable and new job metadata can still be saved.
        _execute_best_effort_pragma(conn, "PRAGMA journal_mode=MEMORY")
    else:
        # Linux deployments (Zeabur/Render, persistent disk) don't hit that bug —
        # use WAL so a killed/OOM'd process can't lose the last committed job update.
        _execute_best_effort_pragma(conn, "PRAGMA journal_mode=WAL")
    _execute_best_effort_pragma(conn, "PRAGMA synchronous=NORMAL")
    return conn


def _execute_best_effort_pragma(conn: sqlite3.Connection, statement: str) -> None:
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        if not _is_database_locked(exc):
            raise


def _is_database_locked(exc: sqlite3.OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def _active_sqlite_path() -> Path:
    if _RECOVERED_SQLITE_PATH.exists():
        return _RECOVERED_SQLITE_PATH
    return settings.sqlite_path


def init_db(db_path: Path | None = None) -> None:
    db_path = db_path or _active_sqlite_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite(db_path) as conn:
        _ensure_schema(conn)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_job_columns(conn)
    _ensure_user_profile_columns(conn)
    _rename_legacy_demo_accounts(conn)
    _seed_demo_accounts(conn)
    _backfill_memberships(conn)
    _backfill_job_ownership(conn)
    _ensure_indexes(conn)
    conn.commit()


def _ensure_job_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for column, ddl in {
        "company_name": "ALTER TABLE jobs ADD COLUMN company_name TEXT",
        "check_mode": "ALTER TABLE jobs ADD COLUMN check_mode TEXT DEFAULT 'ah'",
        "owner_user_id": "ALTER TABLE jobs ADD COLUMN owner_user_id TEXT",
        "owner_display_name": "ALTER TABLE jobs ADD COLUMN owner_display_name TEXT",
        "project_group_id": "ALTER TABLE jobs ADD COLUMN project_group_id TEXT",
        "project_group_name": "ALTER TABLE jobs ADD COLUMN project_group_name TEXT",
        "profile_a_json": "ALTER TABLE jobs ADD COLUMN profile_a_json TEXT",
        "profile_h_json": "ALTER TABLE jobs ADD COLUMN profile_h_json TEXT",
        "coverage_items_json": "ALTER TABLE jobs ADD COLUMN coverage_items_json TEXT",
        "comparison_summary_json": "ALTER TABLE jobs ADD COLUMN comparison_summary_json TEXT",
    }.items():
        if column not in existing:
            conn.execute(ddl)


def _ensure_user_profile_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(user_profiles)").fetchall()}
    for column, ddl in {
        "role_title": "ALTER TABLE user_profiles ADD COLUMN role_title TEXT",
        "avatar_path": "ALTER TABLE user_profiles ADD COLUMN avatar_path TEXT",
        "updated_at": "ALTER TABLE user_profiles ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        # 登录功能（注册/登录）新增：密码哈希三要素 + 注册时间。存量行得到 NULL，
        # 由 _backfill_demo_passwords 给演示账号定点回填 demo1234。
        "password_hash": "ALTER TABLE user_profiles ADD COLUMN password_hash TEXT",
        "password_salt": "ALTER TABLE user_profiles ADD COLUMN password_salt TEXT",
        "password_iterations": "ALTER TABLE user_profiles ADD COLUMN password_iterations INTEGER",
        "created_at": "ALTER TABLE user_profiles ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
    }.items():
        if column not in existing:
            conn.execute(ddl)


# ============ 演示账号种子（注册登录落地后替代单一演示用户） ============
# 统一演示密码，覆盖三条演示动线：
#   1. chu-stanley 同时属于 SH/FS3 与 SH/IPO 专项 —— 同一人多组切换
#   2. yu-jill 与 chu-stanley 同组 —— 组内结果共享
#   3. ni-andrew 在 BJ/FS1 —— 组间隔离（看不到 sh-fs3 的任务）
_DEMO_PASSWORD = "demo1234"
_DEMO_GROUPS: list[tuple[str, str]] = [
    ("sh-fs3", "SH/FS3"),
    ("sh-ipo", "SH/IPO 专项"),
    ("bj-fs1", "BJ/FS1"),
]
_DEMO_ACCOUNTS: list[dict[str, object]] = [
    {
        "user_id": DEFAULT_USER_PROFILE["user_id"],
        "display_name": DEFAULT_USER_PROFILE["display_name"],
        "office_line": DEFAULT_USER_PROFILE["office_line"],
        "role_title": "Senior Manager",
        "project_group_id": "sh-fs3",
        "memberships": ("sh-fs3", "sh-ipo"),
    },
    {
        "user_id": "yu-jill",
        "display_name": "Yu, Jill",
        "office_line": "SH/FS3",
        "role_title": "Audit Associate",
        "project_group_id": "sh-fs3",
        "memberships": ("sh-fs3",),
    },
    {
        "user_id": "ni-andrew",
        "display_name": "Ni, Andrew",
        "office_line": "BJ/FS1",
        "role_title": "Audit Manager",
        "project_group_id": "bj-fs1",
        "memberships": ("bj-fs1",),
    },
]

# 演示账号改名历史：老库里已经种入过旧 user_id，必须在种子之前就地改名，
# 否则新旧两套账号会同时存在（项目组成员数翻倍，演示时对不上）。
_RENAMED_DEMO_ACCOUNTS: list[tuple[str, str, str]] = [
    # (旧 user_id, 新 user_id, 新 display_name)
    ("chen-yiran", "yu-jill", "Yu, Jill"),
    ("zhang-wei", "ni-andrew", "Ni, Andrew"),
]


def _set_demo_password(conn: sqlite3.Connection, user_id: str) -> None:
    password_hash, password_salt, iterations = hash_password(_DEMO_PASSWORD)
    conn.execute(
        "UPDATE user_profiles SET password_hash = ?, password_salt = ?, password_iterations = ? WHERE user_id = ?",
        (password_hash, password_salt, iterations, user_id),
    )


def _seed_demo_accounts(conn: sqlite3.Connection) -> None:
    """幂等种入演示项目组 + 演示账号 + 成员关系。

    仅在新插入账号行（rowcount == 1）时计算 PBKDF2（约 100ms/账号），
    避免每次启动为已有账号白算哈希。
    """
    group_names = dict(_DEMO_GROUPS)
    first_user = str(_DEMO_ACCOUNTS[0]["user_id"])
    for group_id, group_name in _DEMO_GROUPS:
        conn.execute(
            "INSERT OR IGNORE INTO project_groups (group_id, group_name, created_by, created_at) VALUES (?, ?, ?, datetime('now'))",
            (group_id, group_name, first_user),
        )
    for account in _DEMO_ACCOUNTS:
        user_id = str(account["user_id"])
        active_group_id = str(account["project_group_id"])
        cursor = conn.execute(
            """INSERT OR IGNORE INTO user_profiles
            (user_id, display_name, office_line, role_title, project_group_id, project_group_name, avatar_path, updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                user_id,
                account["display_name"],
                account["office_line"],
                account["role_title"],
                active_group_id,
                group_names[active_group_id],
                DEFAULT_USER_PROFILE["avatar_path"],
            ),
        )
        if cursor.rowcount == 1:
            _set_demo_password(conn, user_id)
        for group_id in account["memberships"]:  # type: ignore[union-attr]
            conn.execute(
                "INSERT OR IGNORE INTO user_group_memberships (user_id, group_id, joined_at) VALUES (?, ?, datetime('now'))",
                (user_id, group_id),
            )
    _backfill_demo_passwords(conn)


def _rename_legacy_demo_accounts(conn: sqlite3.Connection) -> None:
    """把改名前种入的演示账号就地改名，连同其任务、成员关系、会话与复核署名一起迁移。

    必须在 _seed_demo_accounts 之前调用：种子用 INSERT OR IGNORE，若新账号先被插入，
    这里的改名会撞上主键冲突，老库便会同时留下新旧两套账号。
    """
    for old_id, new_id, new_display_name in _RENAMED_DEMO_ACCOUNTS:
        old = conn.execute(
            "SELECT display_name FROM user_profiles WHERE user_id = ?", (old_id,)
        ).fetchone()
        if old is None:
            continue
        exists = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ?", (new_id,)
        ).fetchone()
        if exists:
            # 新账号已存在（改名后又被种子重新插入过旧行等异常情形）：丢弃旧行，
            # 保留新账号，避免项目组里出现两个同一个人。
            conn.execute("DELETE FROM user_group_memberships WHERE user_id = ?", (old_id,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (old_id,))
            conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (old_id,))
            continue

        old_display_name = old["display_name"]
        conn.execute(
            "UPDATE user_profiles SET user_id = ?, display_name = ? WHERE user_id = ?",
            (new_id, new_display_name, old_id),
        )
        conn.execute(
            "UPDATE user_group_memberships SET user_id = ? WHERE user_id = ?", (new_id, old_id)
        )
        conn.execute("UPDATE sessions SET user_id = ? WHERE user_id = ?", (new_id, old_id))
        conn.execute("UPDATE project_groups SET created_by = ? WHERE created_by = ?", (new_id, old_id))
        conn.execute(
            "UPDATE jobs SET owner_user_id = ?, owner_display_name = ? WHERE owner_user_id = ?",
            (new_id, new_display_name, old_id),
        )
        # reviews 存的是展示名而非 user_id，一并迁移，历史复核记录的署名才不会停在旧名字
        conn.execute(
            "UPDATE reviews SET reviewed_by = ? WHERE reviewed_by = ?",
            (new_display_name, old_display_name),
        )


def _backfill_demo_passwords(conn: sqlite3.Connection) -> None:
    """存量库（登录功能上线前创建）里的演示账号没有密码——定点回填，使其可登录。"""
    for account in _DEMO_ACCOUNTS:
        user_id = str(account["user_id"])
        row = conn.execute(
            "SELECT password_hash FROM user_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row and not row["password_hash"]:
            _set_demo_password(conn, user_id)


def _backfill_memberships(conn: sqlite3.Connection) -> None:
    """老库用户（登录功能前）没有任何 membership 行——按其 profile 的项目组回填，
    并兜底保证该组在 project_groups 里有记录。"""
    rows = conn.execute(
        """SELECT p.user_id, p.project_group_id, p.project_group_name
        FROM user_profiles p
        WHERE NOT EXISTS (SELECT 1 FROM user_group_memberships m WHERE m.user_id = p.user_id)"""
    ).fetchall()
    for row in rows:
        group_id = row["project_group_id"] or CURRENT_PROJECT_GROUP_ID
        group_name = row["project_group_name"] or CURRENT_PROJECT_GROUP_NAME
        conn.execute(
            "INSERT OR IGNORE INTO project_groups (group_id, group_name, created_by, created_at) VALUES (?, ?, ?, datetime('now'))",
            (group_id, group_name, row["user_id"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_group_memberships (user_id, group_id, joined_at) VALUES (?, ?, datetime('now'))",
            (row["user_id"], group_id),
        )


def _backfill_job_ownership(conn: sqlite3.Connection) -> None:
    conn.execute(
        """UPDATE jobs
        SET owner_user_id = COALESCE(owner_user_id, ?),
            owner_display_name = COALESCE(owner_display_name, ?),
            project_group_id = COALESCE(project_group_id, ?),
            project_group_name = COALESCE(project_group_name, ?)
        WHERE owner_user_id IS NULL
           OR owner_display_name IS NULL
           OR project_group_id IS NULL
           OR project_group_name IS NULL""",
        (
            DEFAULT_USER_PROFILE["user_id"],
            DEFAULT_USER_PROFILE["display_name"],
            DEFAULT_USER_PROFILE["project_group_id"],
            DEFAULT_USER_PROFILE["project_group_name"],
        ),
    )


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner_user ON jobs(owner_user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_project_group ON jobs(project_group_id)")


# get_conn() used to run the full write-heavy _ensure_schema bootstrap (executescript +
# INSERT + UPDATE + commit) on *every* connection, even pure reads — under concurrency this
# opens an unnecessary write transaction per request and amplifies lock contention. Memoize
# by resolved db path so it only runs once per process per path; a failed attempt (e.g.
# transient "database is locked") is not memoized, so the next connection retries.
_ensured_paths: set[str] = set()
_ensured_paths_lock = threading.Lock()


def _ensure_schema_once(conn: sqlite3.Connection, db_path: Path) -> None:
    key = str(db_path.resolve())
    with _ensured_paths_lock:
        if key in _ensured_paths:
            return
    try:
        _ensure_schema(conn)
    except sqlite3.OperationalError as exc:
        if not _is_database_locked(exc):
            raise
        return
    with _ensured_paths_lock:
        _ensured_paths.add(key)


@contextmanager
def get_conn():
    db_path = _active_sqlite_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect_sqlite(db_path)
    try:
        _ensure_schema_once(conn, db_path)
        yield conn
    finally:
        conn.close()
