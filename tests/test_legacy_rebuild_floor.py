"""legacy 结果数值重建的版本下限（_NUMERIC_REBUILD_FLOOR）回归测试。

背景（2026-09-04 线上事故）：result_version 18→19 bump 后，全部历史任务同时
变为「待重建」——读一次历史列表，API 在事件循环内逐任务重跑
run_numeric_checks_on_profiles（每个约 1 分钟），20+ 分钟整站无响应。

修复：version ≥ 17（4-gate 质量整顿之后）的旧结果只补元数据并标
stale_result，不再触发数值重建；< 17 仍走原重建路径（针对整顿前 FP 风暴
的一次性迁移）。
"""

from __future__ import annotations

import ahcc.storage.repository as repository
from ahcc.storage.repository import (
    _NUMERIC_REBUILD_FLOOR,
    _CURRENT_RESULT_VERSION,
    _sanitize_summary_for_loaded_job,
)


def _summary(version: int) -> dict:
    return {
        "result_version": version,
        "real_diff_count": 3,
        "expected_diff_count": 0,
        "unresolved_diff_count": 2,
        "total_diff_count": 5,
        "extraction_engine_version": "2026-06-01.13",
    }


def test_rebuild_floor_constant() -> None:
    # 下限覆盖质量整顿（81840ea，v17）之后的所有版本
    assert _NUMERIC_REBUILD_FLOOR == 17
    assert _NUMERIC_REBUILD_FLOOR < _CURRENT_RESULT_VERSION


def test_v18_and_above_skip_numeric_rebuild(monkeypatch) -> None:
    """v18（及任何 ≥ floor 的旧版本）读取不得触发数值重建。"""
    calls: list[str] = []
    monkeypatch.setattr(
        repository, "_load_current_numeric_diffs", lambda job_id: calls.append(job_id) or ()
    )
    monkeypatch.setattr(
        repository, "_load_raw_diffs", lambda job_id: calls.append(f"raw:{job_id}") or []
    )

    sanitized = _sanitize_summary_for_loaded_job("job_v18", _summary(18))
    assert calls == [], "v18 读取不得触发重建路径（含 _load_raw_diffs）"
    assert sanitized["result_version"] == _CURRENT_RESULT_VERSION
    # 引擎版本过旧 → stale_result 提示用户重跑（信息不静默）
    assert sanitized["stale_result"] is True

    _sanitize_summary_for_loaded_job("job_v19", _summary(19))
    assert calls == []


def test_below_floor_still_rebuilds(monkeypatch) -> None:
    """< floor（整顿前的 FP 风暴结果）仍走原重建路径。"""
    calls: list[str] = []
    monkeypatch.setattr(
        repository,
        "_load_raw_diffs",
        lambda job_id: calls.append(job_id) or [],
    )

    _sanitize_summary_for_loaded_job("job_v16", _summary(16))
    assert calls == ["job_v16"], "v16 应进入重建路径（加载原始 diffs）"
