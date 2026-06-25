"""导出当前最佳的一次运行结果作为演示兜底缓存。

用法：
    python scripts/demo_cache_dump.py --job-id abcd1234

演示当天若 API 故障，可设 .env 中 DEMO_MODE=true，UI 会直接读取 demo_cache.json 秒回放。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahcc.config import settings
from ahcc.storage.repository import get_diffs


def main(job_id: str = typer.Option(..., help="要缓存的任务 ID")) -> None:
    diffs = get_diffs(job_id)
    if not diffs:
        print(f"未找到 {job_id} 的差异记录")
        raise typer.Exit(1)

    payload = {
        "job_id": job_id,
        "diffs": [d.model_dump(mode="json") for d in diffs],
    }
    settings.demo_cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings.demo_cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"演示缓存已写入：{settings.demo_cache_path}（{len(diffs)} 条差异）")


if __name__ == "__main__":
    typer.run(main)
