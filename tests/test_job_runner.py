"""子进程任务调度（ahcc.api.job_runner）的单测。

用假 worker 脚本（python -c）替换真实 worker，验证监督者协议：
- inline 模式委托给 routes_job._run_job_background（存量行为）
- 正常退出：读 result.json 并 save_job
- 心跳失联：kill 并标记 heartbeat lost
- 非零退出：标记 worker crashed
- 硬超时：kill 并标记 job timeout
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ahcc.api import job_runner
from ahcc.schemas import Job, JobStatus


def _job(job_id: str = "jr-test-1") -> Job:
    return Job(
        job_id=job_id,
        company_name="Runner Test",
        check_mode="ah",
        a_file="a.pdf",
        h_file="h.pdf",
        status=JobStatus.PENDING,
    )


@pytest.fixture()
def runner_env(monkeypatch, tmp_path):
    """把 storage 指到 tmp、去掉用户上下文、捕获 save_job/save_job_progress。"""
    saved: list[Job] = []
    progress: list[Job] = []
    monkeypatch.setattr(job_runner.settings, "storage_dir", tmp_path)
    monkeypatch.setattr(job_runner, "save_job", saved.append)
    monkeypatch.setattr(job_runner, "save_job_progress", progress.append)
    monkeypatch.setattr(job_runner, "apply_current_user_context", lambda job: job)
    monkeypatch.setattr(job_runner, "_POLL_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(job_runner.settings, "job_runner", "subprocess")
    monkeypatch.setattr(job_runner.settings, "job_max_concurrency", 1)
    monkeypatch.setattr(job_runner.settings, "job_timeout_seconds", 60.0)
    monkeypatch.setattr(job_runner.settings, "job_heartbeat_stale_seconds", 60.0)
    job_runner._semaphores.clear()
    return {"saved": saved, "progress": progress, "tmp": tmp_path}


def _fake_worker(monkeypatch, script_body: str) -> None:
    """把 worker 命令替换为内联 python 脚本；{job_dir} 占位符在 spawn 时代入。"""

    def _command(job_dir: Path) -> list[str]:
        script = script_body.replace("__JOB_DIR__", repr(str(job_dir)))
        return [sys.executable, "-X", "utf8", "-c", script]

    monkeypatch.setattr(job_runner, "_worker_command", _command)


@pytest.mark.asyncio
async def test_inline_mode_delegates_to_routes_job(monkeypatch):
    calls: list[tuple] = []

    async def fake_background(job, *, bilingual_level="fast", visual_review_mode="off"):
        calls.append((job.job_id, bilingual_level, visual_review_mode))

    import ahcc.api.routes_job as routes_job

    monkeypatch.setattr(job_runner.settings, "job_runner", "inline")
    monkeypatch.setattr(routes_job, "_run_job_background", fake_background)

    await job_runner.run_job(_job(), bilingual_level="strict", visual_review_mode="smart")

    assert calls == [("jr-test-1", "strict", "smart")]


@pytest.mark.asyncio
async def test_subprocess_success_saves_result_job(monkeypatch, runner_env):
    _fake_worker(
        monkeypatch,
        """
import json
from pathlib import Path
job_dir = Path(__JOB_DIR__)
payload = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
job = payload['job']
job['status'] = 'done'
(job_dir / 'heartbeat.json').write_text('{"ts": 0, "pid": 1}', encoding='utf-8')
(job_dir / 'progress.json').write_text(json.dumps({'status': 'checking', 'comparison_summary': {'current_stage': 'checking'}}), encoding='utf-8')
(job_dir / 'result.json').write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
""",
    )

    await job_runner.run_job(_job())

    saved = runner_env["saved"]
    assert len(saved) == 1
    assert saved[0].status == JobStatus.DONE
    assert saved[0].job_id == "jr-test-1"


@pytest.mark.asyncio
async def test_subprocess_heartbeat_lost_kills_and_marks_failed(monkeypatch, runner_env):
    monkeypatch.setattr(job_runner.settings, "job_heartbeat_stale_seconds", 0.5)
    _fake_worker(
        monkeypatch,
        """
import time
time.sleep(60)  # 不写心跳、不退出 —— 模拟卡死
""",
    )

    await job_runner.run_job(_job())

    saved = runner_env["saved"]
    assert len(saved) == 1
    assert saved[0].status == JobStatus.FAILED
    assert "heartbeat lost" in (saved[0].error or "")


@pytest.mark.asyncio
async def test_subprocess_crash_marks_failed_with_exit_code(monkeypatch, runner_env):
    _fake_worker(
        monkeypatch,
        """
import sys
sys.exit(3)
""",
    )

    await job_runner.run_job(_job())

    saved = runner_env["saved"]
    assert len(saved) == 1
    assert saved[0].status == JobStatus.FAILED
    assert "worker crashed (exit code 3)" in (saved[0].error or "")


@pytest.mark.asyncio
async def test_subprocess_timeout_kills_and_marks_failed(monkeypatch, runner_env):
    monkeypatch.setattr(job_runner.settings, "job_timeout_seconds", 0.5)
    _fake_worker(
        monkeypatch,
        """
import json, time
from pathlib import Path
job_dir = Path(__JOB_DIR__)
for _ in range(600):  # 持续写心跳但永不结束 —— 只有硬超时能拦住
    (job_dir / 'heartbeat.json').write_text('{"ts": 0, "pid": 1}', encoding='utf-8')
    time.sleep(0.1)
""",
    )

    await job_runner.run_job(_job())

    saved = runner_env["saved"]
    assert len(saved) == 1
    assert saved[0].status == JobStatus.FAILED
    assert "timeout" in (saved[0].error or "")


@pytest.mark.asyncio
async def test_subprocess_progress_is_persisted(monkeypatch, runner_env):
    _fake_worker(
        monkeypatch,
        """
import json, time
from pathlib import Path
job_dir = Path(__JOB_DIR__)
(job_dir / 'heartbeat.json').write_text('{"ts": 0, "pid": 1}', encoding='utf-8')
(job_dir / 'progress.json').write_text(json.dumps({'status': 'parsing', 'comparison_summary': {'current_stage': 'parsing', 'current_percent': 10}}), encoding='utf-8')
time.sleep(0.5)  # 留给监督者至少一轮轮询
payload = json.loads((job_dir / 'job.json').read_text(encoding='utf-8'))
job = payload['job']
job['status'] = 'done'
(job_dir / 'result.json').write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
""",
    )

    await job_runner.run_job(_job())

    progress = runner_env["progress"]
    assert progress, "supervisor should persist at least one progress update"
    assert progress[0].status == JobStatus.PARSING
    assert progress[0].comparison_summary.get("current_percent") == 10
    assert runner_env["saved"][0].status == JobStatus.DONE
