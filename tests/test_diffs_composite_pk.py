"""diffs 表复合主键（job_id, diff_id）迁移与防「抢行」回归测试。

背景（2026-09-04 实测数据丢失）：旧结构 diff_id 全局 PRIMARY KEY，而分支机构
差异的 diff_id 是固定命名（BRANCH_上海分行 等）。每个新光大任务保存时
INSERT OR REPLACE 用同名 diff_id 把上一个任务的同名行整体改写 job_id ——
旧任务的分支差异在库里凭空消失（三个光大任务只剩最新一个有 40 条分支差异）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from ahcc.schemas import (
    Diff,
    DiffSeverity,
    DiffType,
    Evidence,
    Job,
    JobStatus,
    LocalizedString,
    ReportSide,
)
from ahcc.storage.models import init_db
from ahcc.storage.repository import get_diffs, save_job


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """repository 的 get_conn 在调用时读 settings.sqlite_path —— 打到临时库，
    避免测试写进真实 storage/ahcc.db。"""
    from ahcc.storage import models as storage_models

    monkeypatch.setattr(storage_models.settings, "sqlite_path", tmp_path / "test.db")


def _job(job_id: str, diffs: list[Diff]) -> Job:
    return Job(
        job_id=job_id,
        company_name="光大银行 2025年 A/H",
        check_mode="ah",
        a_file="a.pdf",
        h_file="h.pdf",
        status=JobStatus.DONE,
        diffs=diffs,
        comparison_summary={
            "result_version": 19,
            "real_diff_count": len(diffs),
            "expected_diff_count": 0,
            "unresolved_diff_count": 0,
            "total_diff_count": len(diffs),
            "extraction_engine_version": "2026-09-01.14",
        },
    )


def _branch_diff(diff_id: str) -> Diff:
    return Diff(
        diff_id=diff_id,
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.HIGH,
        triage="real",
        canonical_key=None,
        topic=LocalizedString(zh="分支机构资产规模：上海分行", en="branch"),
        summary=LocalizedString(zh="s", en="s"),
        a_value=443188.0,
        h_value=39540.0,
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=130, snippet="上海分行 443,188"),
            Evidence(side=ReportSide.H_SHARE, page=129, snippet="上海分行 39,540"),
        ],
        rule_id="branch_asset_scale_match",
    )


def test_same_diff_id_across_jobs_no_steal() -> None:
    """两个任务使用相同 diff_id（固定命名的分支差异）——互不抢行。"""
    init_db()
    save_job(_job("job1", [_branch_diff("BRANCH_上海分行")]))
    save_job(_job("job2", [_branch_diff("BRANCH_上海分行")]))

    assert len(get_diffs("job1")) == 1, "job2 保存后 job1 的差异必须还在"
    assert len(get_diffs("job2")) == 1
    assert get_diffs("job1")[0].a_value == 443188.0


def test_migration_from_global_diff_id_pk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧库（diff_id 全局 PK）启动时自动迁移为复合主键，行数据保留。"""
    from ahcc.storage import models as storage_models

    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(storage_models.settings, "sqlite_path", db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, company_name TEXT, check_mode TEXT DEFAULT 'ah',
            owner_user_id TEXT, owner_display_name TEXT, project_group_id TEXT,
            project_group_name TEXT, a_file TEXT NOT NULL, h_file TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            duration_seconds REAL, error TEXT, profile_a_json TEXT,
            profile_h_json TEXT, coverage_items_json TEXT, comparison_summary_json TEXT
        );
        CREATE TABLE diffs (
            diff_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            diff_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            canonical_key TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO jobs (job_id, a_file, h_file, status, started_at)
            VALUES ('oldjob', 'a.pdf', 'h.pdf', 'done', '2026-09-01T00:00:00');
        """
    )
    legacy_diff = _branch_diff("BRANCH_上海分行")
    conn.execute(
        "INSERT INTO diffs (diff_id, job_id, diff_type, severity, canonical_key, payload_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "BRANCH_上海分行",
            "oldjob",
            "disclosure",
            "high",
            None,
            legacy_diff.model_dump_json(),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    init_db()  # 启动迁移（fixture 已把 sqlite_path 指向 legacy.db）

    verify = sqlite3.connect(db_path)
    pk = sorted((row[5], row[1]) for row in verify.execute("PRAGMA table_info(diffs)").fetchall() if row[5])
    assert [name for _, name in pk] == ["job_id", "diff_id"], f"复合主键未生效: {pk}"
    count = verify.execute("SELECT COUNT(*) FROM diffs WHERE job_id='oldjob'").fetchone()[0]
    verify.close()
    assert count == 1, "迁移必须保留存量行"

    # 迁移后的库：同名 diff_id 不再跨任务互抢。oldjob 的行是迁移来的旧 payload
    # （无 result_version 上下文），直接按行数验证；新 job 走完整保存/读取。
    save_job(_job("job_new", [_branch_diff("BRANCH_上海分行")]))
    raw = sqlite3.connect(db_path)
    stolen = raw.execute(
        "SELECT COUNT(*) FROM diffs WHERE job_id='oldjob' AND diff_id='BRANCH_上海分行'"
    ).fetchone()[0]
    raw.close()
    assert stolen == 1, "新任务保存不得抢走 oldjob 的行"
    assert len(get_diffs("job_new")) == 1
