"""测试全局配置。

JOB_RUNNER=inline：存量 API 测试假定任务在事件循环内执行（monkeypatch
routes_job.Orchestrator/save_job 即可拦截）；子进程模式会绕过这些补丁，
因此测试默认走 inline，子进程路径由 tests/test_job_runner.py 显式覆盖。
"""

from __future__ import annotations

import os

os.environ.setdefault("JOB_RUNNER", "inline")

from ahcc.config import settings  # noqa: E402  (需在设置环境变量后导入)

# settings 是模块级单例，可能在环境变量生效前已被其他导入路径实例化；显式覆写兜底。
settings.job_runner = "inline"
