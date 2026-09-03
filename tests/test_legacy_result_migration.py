"""存量结果离线迁移 + 数值重算并发去重的回归测试。

背景（2026-09-04 事故三，前两次修复都没盖住）：
`result_version < _NUMERIC_REBUILD_FLOOR` 的任务每次被读到都重跑
`run_numeric_checks_on_profiles`，结果只进程内记忆化、从不写回库。上一版修复
把 floor 定在 17 并用 `list_jobs(limit=10)` 验证「历史 0.09s」——但前端
`loadHistory` 请求的是 `limit=30`，而那 30 条里有 16 条停在 v16（正好比 floor
低一级）。实测 `list_jobs(limit=30)` 冷启动 188.20s，且 history 每 2.5 秒轮询
一次，裸 `lru_cache` 不合并并发调用，几十份全量重算一起抢 GIL → 整站不可用。

两道防线，各自的回归在这里锁住：
1. `scripts/migrate_legacy_results.py` 把升级结果**写回库**，之后读取路径永远
   命中快路径，重启也不用重付。
2. `_load_current_numeric_diffs` 的 per-job 锁保证同一任务同一时刻至多算一份。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import ahcc.storage.repository as repository
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
from ahcc.storage.repository import _CURRENT_RESULT_VERSION, save_job

_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate_legacy_results.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migrate_legacy_results", _MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_conn 在调用时读 settings.sqlite_path —— 打到临时库，别写真实 storage/ahcc.db。"""
    from ahcc.storage import models as storage_models

    monkeypatch.setattr(storage_models.settings, "sqlite_path", tmp_path / "test.db")


@pytest.fixture(autouse=True)
def _clear_numeric_cache() -> None:
    """进程级缓存会跨测试串味，每个用例前后都清空。"""
    repository._NUMERIC_DIFF_CACHE.clear()
    repository._NUMERIC_DIFF_LOCKS.clear()
    yield
    repository._NUMERIC_DIFF_CACHE.clear()
    repository._NUMERIC_DIFF_LOCKS.clear()


def _diff(diff_id: str, diff_type: DiffType) -> Diff:
    return Diff(
        diff_id=diff_id,
        diff_type=diff_type,
        severity=DiffSeverity.HIGH,
        triage="real",
        canonical_key=None,
        topic=LocalizedString(zh=diff_id, en=diff_id),
        summary=LocalizedString(zh="s", en="s"),
        a_value=1.0,
        h_value=2.0,
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=1, snippet="a"),
            Evidence(side=ReportSide.H_SHARE, page=1, snippet="h"),
        ],
        rule_id="r",
    )


def _legacy_job(job_id: str, version: int, diffs: list[Diff]) -> Job:
    return Job(
        job_id=job_id,
        company_name="光大银行 2025年 A/H",
        check_mode="ah",
        a_file="a.pdf",
        h_file="h.pdf",
        status=JobStatus.DONE,
        diffs=diffs,
        comparison_summary={
            "result_version": version,
            "real_diff_count": len(diffs),
            "expected_diff_count": 0,
            "unresolved_diff_count": 0,
            "total_diff_count": len(diffs),
            "extraction_engine_version": "2026-06-01.13",
        },
    )


def _stored_summary(job_id: str) -> dict:
    from ahcc.storage.models import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT comparison_summary_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    return json.loads(row["comparison_summary_json"])


def _stored_diff_ids(job_id: str) -> set[str]:
    from ahcc.storage.models import get_conn

    with get_conn() as conn:
        rows = conn.execute("SELECT diff_id FROM diffs WHERE job_id = ?", (job_id,)).fetchall()
    return {row["diff_id"] for row in rows}


# --------------------------------------------------------------------------
# 一、离线迁移：算一次，写回库
# --------------------------------------------------------------------------


def test_migration_persists_upgrade_and_stops_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """迁移后读取路径不得再触发数值重建 —— 这正是 188 秒的来源。"""
    init_db()
    save_job(_legacy_job("legacy16", 16, [_diff("OLD_NUM", DiffType.NUMERIC)]))
    migration = _load_migration_module()

    monkeypatch.setattr(
        repository, "_load_current_numeric_diffs", lambda job_id: (_diff("NEW_NUM", DiffType.NUMERIC),)
    )
    assert [item[0] for item in migration.find_legacy_jobs()] == ["legacy16"]

    summary, diffs, coverage = migration.upgrade_one("legacy16", 16, now="2026-09-04T00:00:00")
    migration.persist_one("legacy16", summary, diffs, coverage, now="2026-09-04T00:00:00")

    stored = _stored_summary("legacy16")
    assert stored["result_version"] == _CURRENT_RESULT_VERSION
    assert stored["upgraded_from_result_version"] == 16, "原始版本号必须留痕"

    # 迁移后再读：重建函数一次都不许被调用。
    calls: list[str] = []
    monkeypatch.setattr(
        repository, "_load_current_numeric_diffs", lambda job_id: calls.append(job_id) or ()
    )
    monkeypatch.setattr(
        repository, "_load_raw_diffs", lambda job_id: calls.append(f"raw:{job_id}") or []
    )
    repository._sanitize_summary_for_loaded_job("legacy16", stored)
    assert calls == [], "迁移后的任务读取仍触发重建 —— 188 秒会回来"


def test_migration_replaces_diff_rows_instead_of_merging() -> None:
    """升级会淘汰旧的假差异；必须整组替换，upsert 会把被淘汰的行留在库里。"""
    init_db()
    save_job(
        _legacy_job(
            "legacy16",
            16,
            [_diff("OLD_NUM", DiffType.NUMERIC), _diff("KEEP_DISC", DiffType.DISCLOSURE)],
        )
    )
    migration = _load_migration_module()

    summary, diffs, coverage = migration.upgrade_one("legacy16", 16, now="2026-09-04T00:00:00")
    migration.persist_one("legacy16", summary, diffs, coverage, now="2026-09-04T00:00:00")

    stored = _stored_diff_ids("legacy16")
    assert "OLD_NUM" not in stored, "整顿前的旧 numeric 假差异必须被替换掉"
    assert "KEEP_DISC" in stored, "非 numeric 通道的检出不得在迁移中丢失"


def test_migration_is_idempotent() -> None:
    """跑第二遍必须是空操作 —— 否则重复执行会反复重写存量结果。"""
    init_db()
    save_job(_legacy_job("legacy16", 16, [_diff("D1", DiffType.DISCLOSURE)]))
    migration = _load_migration_module()

    summary, diffs, coverage = migration.upgrade_one("legacy16", 16, now="2026-09-04T00:00:00")
    migration.persist_one("legacy16", summary, diffs, coverage, now="2026-09-04T00:00:00")

    assert migration.find_legacy_jobs() == []


def test_migration_leaves_current_version_jobs_untouched() -> None:
    """v19 任务本来就走快路径，不该被迁移脚本扫进来。"""
    init_db()
    save_job(_legacy_job("current19", _CURRENT_RESULT_VERSION, [_diff("D1", DiffType.DISCLOSURE)]))
    migration = _load_migration_module()

    assert migration.find_legacy_jobs() == []


# --------------------------------------------------------------------------
# 二、并发去重：同一任务同一时刻至多算一份
# --------------------------------------------------------------------------


def test_concurrent_rebuild_computes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """裸 lru_cache 只在算完后才写缓存，2.5 秒轮询会堆出几十份同样的全量重算。"""
    compute_calls: list[str] = []

    def _slow_compute(job_id: str) -> tuple[Diff, ...]:
        compute_calls.append(job_id)
        time.sleep(0.3)  # 模拟真实重算的长耗时窗口，让并发调用确实撞上
        return (_diff("NUM", DiffType.NUMERIC),)

    monkeypatch.setattr(repository, "_compute_current_numeric_diffs", _slow_compute)

    results: list[tuple[Diff, ...]] = []
    barrier = threading.Barrier(8)

    def _worker() -> None:
        barrier.wait()  # 8 个线程同时进入，最大化竞态
        results.append(repository._load_current_numeric_diffs("job1"))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert compute_calls == ["job1"], f"同一任务被并发重算 {len(compute_calls)} 次，锁没生效"
    assert len(results) == 8
    assert all(len(item) == 1 for item in results), "等锁的调用方必须拿到同一份结果"


def test_concurrent_rebuild_isolates_different_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """per-job 锁不得退化成全局锁：不同任务必须能并行重算。"""
    active = 0
    peak = 0
    guard = threading.Lock()

    def _slow_compute(job_id: str) -> tuple[Diff, ...]:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.3)
        with guard:
            active -= 1
        return ()

    monkeypatch.setattr(repository, "_compute_current_numeric_diffs", _slow_compute)

    threads = [
        threading.Thread(target=repository._load_current_numeric_diffs, args=(f"job{i}",))
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak > 1, "不同任务被串行化了 —— 锁的粒度退化成全局"


def test_rebuild_result_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """记忆化本身不能在加锁重构中丢掉。"""
    compute_calls: list[str] = []
    monkeypatch.setattr(
        repository,
        "_compute_current_numeric_diffs",
        lambda job_id: compute_calls.append(job_id) or (),
    )

    repository._load_current_numeric_diffs("job1")
    repository._load_current_numeric_diffs("job1")
    repository._load_current_numeric_diffs("job1")

    assert compute_calls == ["job1"]
