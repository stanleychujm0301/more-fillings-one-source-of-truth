"""存量结果一次性离线升级 —— 把「读取时重算」永久变成「读取时直接读」。

背景（2026-09-04 事故三）
------------------------
`result_version < _NUMERIC_REBUILD_FLOOR` 的历史任务，每次被读到都要走
`_upgrade_legacy_job` → `run_numeric_checks_on_profiles` 重跑一遍数值检查。
结果只进程内记忆化，从不写回库，所以**每次重启服务都要重新付一遍**。

实测：前端 `loadHistory` 请求的是 `limit=30`，而 sh-fs3 组前 30 条里有 16 条
停在 v16（正好比 floor 低一级）：

    list_jobs(limit=10)  冷启动  0.12s    ← 上一版修复验证用的是这个默认值
    list_jobs(limit=30)  冷启动  188.20s  ← 前端实际请求的是这个
    list_jobs(limit=30)  热缓存  0.20s

而 history 在有任务运行时每 2.5 秒轮询一次，188 秒内会再堆进几十个同样的全量
重算，一起抢 GIL —— 静态文件要 1.5 秒、/health 要 28 秒、整站不可用。

本脚本把这些任务的升级结果算一次并**写回数据库**（summary / diffs /
coverage_items 三处都写），之后读取路径永远命中 `version >= 当前版本` 的快
路径，重启也不用重付。

写回的内容 = 今天读取路径本来就会返回给前端的内容（复用同一批 `_sanitize_*` /
`_upgrade_legacy_job` 函数），因此对使用者零行为变化，只是从「每次现算」变成
「存下来」。原始版本号记录在 `upgraded_from_result_version` 里留痕。

用法
----
```bash
# 先看会改什么，不落盘
python scripts/migrate_legacy_results.py --dry-run

# 真正执行（默认自动备份 storage/ahcc.db）
python scripts/migrate_legacy_results.py

# 已经自己备份过了，跳过
python scripts/migrate_legacy_results.py --no-backup
```

**执行前请先停掉后端服务**，避免与运行中的写入互相干扰。
脚本是幂等的：跑第二遍会报告 0 个待迁移任务。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ahcc.config import settings  # noqa: E402
from ahcc.storage import repository as repo  # noqa: E402
from ahcc.storage.models import get_conn, init_db  # noqa: E402


def _stored_result_version(summary_json: str | None) -> int:
    try:
        summary = json.loads(summary_json or "{}")
    except (TypeError, ValueError):
        return 0
    if not isinstance(summary, dict):
        return 0
    return int(summary.get("result_version") or 0)


def find_legacy_jobs() -> list[tuple[str, str, int]]:
    """返回 [(job_id, company_name, stored_version)]，只含读取时会触发重算的任务。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT job_id, company_name, comparison_summary_json FROM jobs ORDER BY started_at ASC"
        ).fetchall()
    legacy = []
    for row in rows:
        version = _stored_result_version(row["comparison_summary_json"])
        if version < repo._NUMERIC_REBUILD_FLOOR:
            legacy.append((row["job_id"], row["company_name"] or "", version))
    return legacy


def upgrade_one(job_id: str, stored_version: int, *, now: str) -> tuple[dict, list, list]:
    """算出该任务升级后的 (summary, diffs, coverage_items)。

    刻意复用读取路径的同一批函数：写回的必须**就是**今天前端已经看到的内容，
    否则这次迁移就不是「把现算变成存下来」，而是偷偷改了检出结果。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT comparison_summary_json, coverage_items_json FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    raw_summary = repo._load_json_field(row["comparison_summary_json"], {})
    raw_coverage = repo._load_json_field(row["coverage_items_json"], [])

    summary = repo._sanitize_summary_for_loaded_job(job_id, raw_summary)
    diffs = repo.get_diffs(job_id)
    # get_job() 在 version < 当前版本时才做这步；写回后版本会变成当前版本，
    # 所以必须在这里一并固化，否则 coverage 的 legacy 措辞修正会丢。
    coverage = repo._sanitize_legacy_coverage_items(raw_coverage)

    summary = dict(summary)
    summary["upgraded_from_result_version"] = stored_version
    summary["upgraded_at"] = now
    return summary, diffs, coverage


def persist_one(job_id: str, summary: dict, diffs: list, coverage: list, *, now: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET comparison_summary_json = ?, coverage_items_json = ? WHERE job_id = ?",
            (
                json.dumps(summary, ensure_ascii=False),
                json.dumps(coverage, ensure_ascii=False),
                job_id,
            ),
        )
        # 升级会**删掉**一部分旧的假差异，所以必须整组替换而不是 upsert，
        # 否则被淘汰的旧 numeric 差异会留在库里。
        conn.execute("DELETE FROM diffs WHERE job_id = ?", (job_id,))
        for diff in diffs:
            conn.execute(
                """INSERT INTO diffs
                (diff_id, job_id, diff_type, severity, canonical_key, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    diff.diff_id,
                    job_id,
                    diff.diff_type.value,
                    diff.severity.value,
                    diff.canonical_key,
                    diff.model_dump_json(),
                    now,
                ),
            )
        # get_conn() 不自动提交（isolation_level 默认，close() 会回滚）——
        # summary / diffs 必须在同一个事务里一起落盘。
        conn.commit()


def backup_db() -> Path:
    src = Path(settings.sqlite_path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = src.with_name(f"{src.name}.bak-legacy-migration-{stamp}")
    shutil.copy2(src, dst)
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只报告会改什么，不落盘")
    parser.add_argument("--no-backup", action="store_true", help="跳过自动备份（你已自行备份时用）")
    args = parser.parse_args()

    init_db()
    legacy = find_legacy_jobs()
    if not legacy:
        print(f"没有待迁移任务（全部 result_version >= {repo._NUMERIC_REBUILD_FLOOR}）。")
        return 0

    print(f"待迁移 {len(legacy)} 个任务（result_version < {repo._NUMERIC_REBUILD_FLOOR}）：")
    for job_id, company, version in legacy:
        print(f"  {job_id}  v{version:<3d} {company}")
    print()

    if args.dry_run:
        print("--dry-run：未写入任何内容。")
        return 0

    if not args.no_backup:
        dst = backup_db()
        print(f"已备份数据库 → {dst}")
        print()

    now = datetime.utcnow().isoformat()
    failures: list[tuple[str, str]] = []
    for index, (job_id, company, version) in enumerate(legacy, start=1):
        label = f"[{index}/{len(legacy)}] {job_id} v{version} {company}"
        print(f"{label} ... ", end="", flush=True)
        started = datetime.now()
        try:
            summary, diffs, coverage = upgrade_one(job_id, version, now=now)
            persist_one(job_id, summary, diffs, coverage, now=now)
        except Exception as exc:  # noqa: BLE001 — 单个任务失败不应中断整批
            failures.append((job_id, str(exc)))
            print(f"失败：{exc}")
            continue
        elapsed = (datetime.now() - started).total_seconds()
        print(f"完成 {len(diffs)} 条差异，用时 {elapsed:.1f}s")

    print()
    remaining = find_legacy_jobs()
    print(f"迁移后仍低于 floor 的任务：{len(remaining)}")
    if failures:
        print(f"失败 {len(failures)} 个：")
        for job_id, message in failures:
            print(f"  {job_id}: {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
