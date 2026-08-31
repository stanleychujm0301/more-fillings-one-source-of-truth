"""证据链抽验工具（验收门槛 d）：核验差异条目的页码/数值/原文摘录是否真实落在 PDF 上。

为什么需要这个
----------------
系统每条差异都带 Evidence(page, snippet)，但「页码错一格」「数值单位没还原」
「snippet 是拼接出来的」这类问题只有打开 PDF 逐条核对才能发现。本工具对
已完成任务的差异做分层抽样，用 PyMuPDF 回到原文页面验证两件事：

1. **值在页**：diff 声称的 a_value/h_value（含 ×1/1e3/1e4/1e6/1e8 单位还原与
   千分位/小数位变体）能在证据页文本中找到；
2. **摘录在页**：evidence.snippet 的词条 ≥60% 能在证据页文本中找到。

用法::

    python scripts/evidence_spotcheck.py --job 056a4db5 --job b800b5a6
    python scripts/evidence_spotcheck.py --latest 3
    python scripts/evidence_spotcheck.py --job 056a4db5 --sample 40 --min-accuracy 0.95
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import fitz  # PyMuPDF
import typer

app = typer.Typer(add_completion=False, help="AHCC 证据链抽验")

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*|[一-鿿]")
_UNIT_SCALES = (1.0, 1e3, 1e4, 1e6, 1e8, 1e-3, 1e-4, 1e-6, 1e-8)


def _norm_text(text: str) -> str:
    """页面文本规范化：去空白与千分位，统一全角括号。"""
    return (
        text.replace(",", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("\n", "")
        .replace("（", "(")
        .replace("）", ")")
    )


def _value_candidates(value: float) -> set[str]:
    """一个数值在页面上可能出现的写法（含单位缩放还原）。"""
    out: set[str] = set()
    for scale in _UNIT_SCALES:
        v = value * scale
        for fmt in (f"{v:.2f}", f"{v:.1f}", f"{v:.0f}"):
            s = fmt.rstrip("0").rstrip(".") if "." in fmt else fmt
            if s and s not in ("-0", "0"):
                out.add(s.replace(",", ""))
                out.add(s.lstrip("-").replace(",", ""))  # 页面上负数常写作括号
    return out


def _value_on_page(value: float, page_norm: str) -> bool:
    return any(c in page_norm for c in _value_candidates(value))


def _snippet_on_page(snippet: str, page_norm: str, min_ratio: float = 0.6) -> bool:
    tokens = _TOKEN_RE.findall(snippet or "")
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in page_norm)
    return hits / len(tokens) >= min_ratio


def _page_text_cache(pdf_path: str):
    cache: dict[int, str] = {}
    doc = fitz.open(pdf_path)

    def get(page: int) -> str:
        if page not in cache:
            if 1 <= page <= doc.page_count:
                cache[page] = _norm_text(doc[page - 1].get_text())
            else:
                cache[page] = ""
        return cache[page]

    return get


def _load_job(job_id: str, storage: Path) -> tuple[dict, dict[str, str]] | None:
    """从 SQLite 存储装载任务（jobs 表取文件路径，diffs 表取 payload_json）。"""
    import sqlite3

    db = storage / "ahcc.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        job_row = conn.execute(
            "SELECT job_id, company_name, a_file, h_file FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not job_row:
            return None
        diff_rows = conn.execute(
            "SELECT payload_json FROM diffs WHERE job_id = ?", (job_id,)
        ).fetchall()
    finally:
        conn.close()
    job = {
        "job_id": job_row["job_id"],
        "company_name": job_row["company_name"],
        "a_file": job_row["a_file"],
        "h_file": job_row["h_file"],
        "diffs": [json.loads(r["payload_json"]) for r in diff_rows],
    }
    files = {
        "a_share": str(job_row["a_file"] or ""),
        "h_share": str(job_row["h_file"] or ""),
    }
    return job, files


def _stratified_sample(diffs: list[dict], limit: int) -> list[dict]:
    """按 rule_id 分层轮转抽样，保证每个规则都被抽到。"""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for d in diffs:
        buckets[str(d.get("rule_id") or "__none__")].append(d)
    picked: list[dict] = []
    idx = 0
    while len(picked) < limit and any(buckets.values()):
        keys = sorted(buckets.keys())
        if idx >= len(keys):
            idx = 0
        key = keys[idx % len(keys)]
        if buckets[key]:
            picked.append(buckets[key].pop(0))
        idx += 1
    return picked


@app.command()
def main(
    job: list[str] = typer.Option(None, help="任务 ID（可重复传多个）"),
    latest: int = typer.Option(0, help="抽验最近 N 个任务（与 --job 互斥）"),
    sample: int = typer.Option(40, help="每个任务最多抽样条数"),
    min_accuracy: float = typer.Option(0.95, help="验收门槛：总体准确率"),
    storage: Path = typer.Option(Path("storage"), help="storage 目录"),
    out: Path = typer.Option(Path("storage/eval/evidence_spotcheck.json"), help="结果 JSON 输出"),
) -> None:
    job_ids = list(job or [])
    if latest:
        import sqlite3

        db = storage / "ahcc.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            try:
                job_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT job_id FROM jobs WHERE status = 'done' "
                        "ORDER BY finished_at DESC LIMIT ?",
                        (latest,),
                    ).fetchall()
                ]
            finally:
                conn.close()
    if not job_ids:
        typer.echo("未指定任务：用 --job <id> 或 --latest N")
        raise typer.Exit(2)

    grand = {"value_ok": 0, "snippet_ok": 0, "both_ok": 0, "total": 0}
    per_rule: dict[str, dict[str, int]] = defaultdict(lambda: {"value_ok": 0, "snippet_ok": 0, "both_ok": 0, "total": 0})
    report_jobs = []

    for job_id in job_ids:
        loaded = _load_job(job_id, storage)
        if not loaded:
            typer.echo(f"[跳过] 任务 {job_id} 在存储中不存在")
            continue
        job_data, files = loaded
        diffs = job_data.get("diffs") or []
        sampled = _stratified_sample(diffs, sample)
        # Evidence.side 序列化为 "A"/"H"，文件槽位键为 a_share/h_share
        side_to_slot = {"A": "a_share", "H": "h_share", "a_share": "a_share", "h_share": "h_share"}
        page_getters = {
            side: _page_text_cache(path)
            for side, path in files.items()
            if path and Path(path).exists()
        }
        job_stat = {"value_ok": 0, "snippet_ok": 0, "both_ok": 0, "total": 0}

        for diff in sampled:
            rule = str(diff.get("rule_id") or "__none__")
            evidences = diff.get("evidence") or []
            claimed = {"a_share": diff.get("a_value"), "h_share": diff.get("h_value")}
            # 单侧内部差异的两条证据在同一侧：a_value/h_value 对应的是「第一处/第二处」，
            # 此时按证据列表顺序映射取值；跨报告差异仍按侧映射
            ev_sides = [str(ev.get("side") or "") for ev in evidences[:2]]
            same_side = len(ev_sides) == 2 and ev_sides[0] == ev_sides[1]
            ordered_values = [diff.get("a_value"), diff.get("h_value")]
            # 每条证据独立验证：证据在哪一侧，就核哪一侧的 PDF 与值
            checks = []
            for ev_i, ev in enumerate(evidences[:2]):
                slot = side_to_slot.get(str(ev.get("side") or ""))
                page = ev.get("page")
                snippet = ev.get("snippet") or ""
                if slot not in page_getters or not page:
                    continue
                page_norm = page_getters[slot](int(page))
                value = ordered_values[ev_i] if same_side else claimed.get(slot)
                v_ok = True if value is None else _value_on_page(float(value), page_norm)
                s_ok = _snippet_on_page(snippet, page_norm) if snippet else True
                checks.append((v_ok, s_ok))
            if not checks:
                continue
            value_ok = all(c[0] for c in checks)
            snippet_ok = all(c[1] for c in checks)
            for stat in (job_stat, per_rule[rule], grand):
                stat["total"] += 1
                stat["value_ok"] += int(value_ok)
                stat["snippet_ok"] += int(snippet_ok)
                stat["both_ok"] += int(value_ok and snippet_ok)

        report_jobs.append({"job_id": job_id, "company": job_data.get("company_name"), **job_stat})
        acc = job_stat["both_ok"] / job_stat["total"] if job_stat["total"] else 1.0
        typer.echo(f"[{job_id}] {job_data.get('company_name')}: 抽验 {job_stat['total']} 条，"
                   f"值在页 {job_stat['value_ok']}，摘录在页 {job_stat['snippet_ok']}，"
                   f"双通过 {job_stat['both_ok']}（{acc:.1%}）")

    typer.echo("\n按规则分布：")
    for rule, stat in sorted(per_rule.items()):
        acc = stat["both_ok"] / stat["total"] if stat["total"] else 1.0
        typer.echo(f"  {rule:40s} n={stat['total']:3d} 值 {stat['value_ok']:3d} 摘录 {stat['snippet_ok']:3d} 双通过 {acc:.1%}")

    overall = grand["both_ok"] / grand["total"] if grand["total"] else 1.0
    typer.echo(f"\n总体证据链准确率：{overall:.1%}（门槛 {min_accuracy:.0%}）")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"overall": overall, "grand": grand, "per_rule": dict(per_rule), "jobs": report_jobs},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(f"结果已写入 {out}")
    raise typer.Exit(0 if overall >= min_accuracy else 1)


if __name__ == "__main__":
    app()
