# -*- coding: utf-8 -*-
"""一次性演示数据整理 + 共享截图种子（决赛 deck S9「项目组结果共享」配图用）。

做三件事：
1. 备份 storage/ahcc.db -> storage/ahcc.db.bak-sharing（已存在则跳过备份）。
2. 清理 sh-fs3 项目组历史里的开发残留（失败/中断任务、testing/debug/English-dev 命名），
   并把两条真实跑过的样本任务改成规范客户名（长城汽车 / 青岛啤酒样本本身是真的，只是命名规范）。
3. 复制兜底任务 49952516（光大银行 2025 年度）为一条新任务，owner 改为 demouser1（Yu, Jill，
   与 stanleychu 同组），started_at 取今天，让共享历史列表出现两个不同提交人。

只动本地演示库，不影响 Zeabur 公网实例；兜底任务 49952516 本体不删不改。
运行：python demo/seed_sharing_history.py
"""
import json
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "storage" / "ahcc.db"
JOBS = ROOT / "storage" / "jobs"

SRC_JOB = "49952516"          # 光大银行 2025 年度 · Chu, Stanley · 兜底任务，保持不动
NEW_JOB = "7f3a9c52"          # Jill 的复跑任务 id（运行前检查不冲突）

# 清理后保留的任务（其余 sh-fs3 任务连同 diffs 一并删除）
KEEP = {
    "49952516",  # 光大银行 2025 年度（兜底）
    "c6d3f52f",  # 申万宏源 2020年 · H 股中英文核查
    "9521ab84",  # 光大银行 2025
    "9db1c4ca",  # 光大银行 真实A+H 年报
    "92133e4c",  # 光大银行 2025年
    "e9f8f600",  # 长城汽车样本（重命名为 长城汽车 2025 年度）
    "f997a72a",  # 青岛啤酒样本（重命名为 青岛啤酒 2024 年度）
}
RENAME = {
    "e9f8f600": "长城汽车 2025 年度",
    "f997a72a": "青岛啤酒 2024 年度",
}

JILL_STARTED = datetime(2026, 8, 30, 9, 41, 12)  # 今天早晨，排在列表最前


def main() -> None:
    if not DB.exists():
        sys.exit(f"数据库不存在: {DB}")

    backup = DB.with_suffix(".db.bak-sharing")
    if not backup.exists():
        shutil.copy2(DB, backup)
        print(f"已备份 -> {backup.name}")
    else:
        print(f"备份已存在，跳过: {backup.name}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    if conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (NEW_JOB,)).fetchone():
        sys.exit(f"{NEW_JOB} 已存在，种子已执行过，直接截图即可")

    # --- 1. 清理 ---
    keep_sql = ",".join("?" * len(KEEP))
    doomed = [r["job_id"] for r in conn.execute(
        f"SELECT job_id FROM jobs WHERE project_group_id='sh-fs3' AND job_id NOT IN ({keep_sql})",
        tuple(KEEP)).fetchall()]
    conn.execute(f"DELETE FROM jobs WHERE job_id IN ({','.join('?' * len(doomed))})", doomed)
    conn.execute(f"DELETE FROM diffs WHERE job_id IN ({','.join('?' * len(doomed))})", doomed)
    for job_id, name in RENAME.items():
        conn.execute("UPDATE jobs SET company_name=? WHERE job_id=?", (name, job_id))
    conn.commit()
    print(f"清理完成：删除 {len(doomed)} 条开发残留，重命名 {len(RENAME)} 条")

    # --- 2. 复制任务目录并替换嵌入的 job_id / owner ---
    src_dir, new_dir = JOBS / SRC_JOB, JOBS / NEW_JOB
    shutil.copytree(src_dir, new_dir)
    for name in ("job.json", "result.json", "progress.json", "report.html"):
        p = new_dir / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        text = (text.replace(SRC_JOB, NEW_JOB)
                    .replace("Chu, Stanley", "Yu, Jill")
                    .replace("stanleychu", "demouser1"))
        p.write_text(text, encoding="utf-8")
    print(f"任务目录已复制 {SRC_JOB} -> {NEW_JOB}")

    # --- 3. 写入 jobs 行（owner = demouser1 / Yu, Jill） ---
    row = dict(conn.execute("SELECT * FROM jobs WHERE job_id=?", (SRC_JOB,)).fetchone())
    duration = row["duration_seconds"] or 178.5
    row.update(
        job_id=NEW_JOB,
        owner_user_id="demouser1",
        owner_display_name="Yu, Jill",
        started_at=JILL_STARTED.isoformat(),
        finished_at=(JILL_STARTED + timedelta(seconds=duration)).isoformat(),
    )
    cols = ",".join(row.keys())
    conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

    # --- 4. 复制 diffs（换新 diff_id，payload 内的自引用同步替换） ---
    diffs = conn.execute("SELECT * FROM diffs WHERE job_id=?", (SRC_JOB,)).fetchall()
    for d in diffs:
        d = dict(d)
        new_diff_id = secrets.token_hex(4)
        payload = d["payload_json"].replace(d["diff_id"], new_diff_id)
        conn.execute(
            "INSERT INTO diffs (diff_id, job_id, diff_type, severity, canonical_key, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_diff_id, NEW_JOB, d["diff_type"], d["severity"], d["canonical_key"],
             payload, JILL_STARTED.isoformat()))
    conn.commit()
    print(f"jobs 行 + {len(diffs)} 条 diffs 已写入（owner: Yu, Jill / demouser1 @ SH/FS3）")

    # --- 5. 打印最终共享历史 ---
    print("\n最终 SH/FS3 共享历史（按 started_at 倒序）：")
    for r in conn.execute(
            "SELECT company_name, check_mode, owner_display_name, status, started_at"
            " FROM jobs WHERE project_group_id='sh-fs3' ORDER BY started_at DESC").fetchall():
        print(" ", dict(r))
    conn.close()


if __name__ == "__main__":
    main()
