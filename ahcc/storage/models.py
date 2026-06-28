"""SQLite 表结构（P3 实现）— 用最简的 sqlite3 + dataclass 实现，避免 ORM 复杂度。

表：
- jobs: 任务元信息
- diffs: 差异记录（JSON 字段存证据链）
- reviews: 审计师覆盖记录（"已审/可接受/需追问"）
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ahcc.config import settings
from ahcc.user_context import DEFAULT_USER_PROFILE


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
"""

_RECOVERED_SQLITE_PATH = Path("./scratch/ahcc.recovered.db")


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    # The Windows demo workspace has shown intermittent disk I/O errors when
    # SQLite creates rollback journal files. Keep the journal in memory so the
    # UI history remains readable and new job metadata can still be saved.
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


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
    _seed_demo_user_profile(conn)
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
    }.items():
        if column not in existing:
            conn.execute(ddl)


def _seed_demo_user_profile(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO user_profiles
        (user_id, display_name, office_line, role_title, project_group_id, project_group_name, avatar_path, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            DEFAULT_USER_PROFILE["user_id"],
            DEFAULT_USER_PROFILE["display_name"],
            DEFAULT_USER_PROFILE["office_line"],
            DEFAULT_USER_PROFILE["role_title"],
            DEFAULT_USER_PROFILE["project_group_id"],
            DEFAULT_USER_PROFILE["project_group_name"],
            DEFAULT_USER_PROFILE["avatar_path"],
        ),
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


@contextmanager
def get_conn():
    db_path = _active_sqlite_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect_sqlite(db_path)
    _ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
