"""取数质量度量 —— Phase 1 的验收工具。

背景
----
对光大银行真实任务下钻发现：**取数的召回是好的（答案清单 15/15 全部抓到），
但精度极差** —— A 侧 4218 条 occurrence 里，21% 的 (页码,数值) 位置被绑定到
多个 canonical_key，`customer_loans` 一个 key 有 356 次出现、286 个不同值，
值里混着 `12`/`31`（12月31日的日期碎片）、附注号、公告标题里的年份。

下游所有噪声（笛卡尔积重复、口径判不准、置信度不足）都是这个的后果。
本脚本把取数质量变成可度量、可回归的指标。

用法::

    # 从已存任务的画像快照统计（最快，不重新解析）
    python scripts/profile_quality.py --job 7f3a9c52

    # 从 PDF 重新解析并统计（改了抽取逻辑后用这个）
    python scripts/profile_quality.py --pdf "F:/.../A股年报.pdf" --side A

    # 带地面真值：检查答案清单里的原始值是否都被抓到（召回红线）
    python scripts/profile_quality.py --pdf "F:/.../A股年报.pdf" --side A \
        --answers "F:/.../错误清单_15处.xlsx"

    # 与基线对比（回归用）
    python scripts/profile_quality.py --pdf ... --baseline storage/eval/profile_quality_A.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

app = typer.Typer(add_completion=False, help="取数质量度量")

# 一个报表行项目最多 4 个合法取值：本期 / 上期 / 母公司本期 / 母公司上期
MAX_LEGITIMATE_VALUES_PER_KEY = 4
DISPERSION_ALERT_THRESHOLD = 6


def _occurrences_from_profile_summary(summary: dict) -> list[dict]:
    """从任务里存的 profile_summary 还原 occurrence 列表。"""
    out: list[dict] = []
    for m in summary.get("metrics") or []:
        key = m.get("canonical_key")
        for o in m.get("all_occurrences") or []:
            out.append({
                "canonical_key": key,
                "value": o.get("value"),
                "page": o.get("page"),
                "unit": o.get("unit"),
                "currency": o.get("currency"),
                "source": o.get("source"),
                "confidence": o.get("confidence"),
            })
    return out


def _occurrences_from_metric_items(items) -> list[dict]:
    """从 extract_metrics 的返回值（MetricOccurrences 或 MetricItem）还原。"""
    from ahcc.profile.models import MetricItem, MetricOccurrences

    out: list[dict] = []
    for entry in items:
        if isinstance(entry, MetricOccurrences):
            candidates = entry.all_occurrences or [entry.primary]
        elif isinstance(entry, MetricItem):
            candidates = [entry]
        else:
            continue
        for it in candidates:
            out.append({
                "canonical_key": it.canonical_key,
                "value": it.value,
                "page": it.page,
                "unit": it.unit,
                "currency": it.currency.value if it.currency else None,
                "source": it.source,
                "confidence": it.confidence,
            })
    return out


def compute_quality(occ: list[dict], *, label: str = "") -> dict[str, Any]:
    """核心指标计算。所有比例都以 occurrence 或 (页,值) 位置为分母。"""
    vals = [o for o in occ if o.get("value") is not None]
    n = len(vals) or 1

    # 多重绑定：同一个 (页码, 数值) 位置被挂到几个 canonical_key 上
    pos: dict[tuple, set] = defaultdict(set)
    for o in vals:
        if o.get("page"):
            pos[(o["page"], round(float(o["value"]), 4))].add(o["canonical_key"])
    multi = {k: v for k, v in pos.items() if len(v) > 1}
    worst_binding = sorted(multi.items(), key=lambda kv: -len(kv[1]))[:5]

    # 取值离散度：一个报表行项目不该有几十上百个不同取值
    by_key: dict[str, set] = defaultdict(set)
    for o in vals:
        by_key[o["canonical_key"]].add(round(float(o["value"]), 4))
    dispersion = sorted(((k, len(v)) for k, v in by_key.items()), key=lambda kv: -kv[1])
    over_dispersed = [kv for kv in dispersion if kv[1] > DISPERSION_ALERT_THRESHOLD]

    def share(pred) -> float:
        return sum(1 for o in vals if pred(float(o["value"]))) / n

    compressible = sum(len(v) for v in by_key.values()) - sum(
        min(len(v), MAX_LEGITIMATE_VALUES_PER_KEY) for v in by_key.values()
    )

    return {
        "label": label,
        "key_count": len(by_key),
        "occurrence_count": len(vals),
        "multi_bound_positions": len(multi),
        "total_positions": len(pos),
        "multi_bound_ratio": round(len(multi) / max(len(pos), 1), 4),
        "date_like_ratio": round(share(lambda v: v == int(v) and 1 <= abs(v) <= 31), 4),
        "tiny_value_ratio": round(share(lambda v: abs(v) < 100), 4),
        "small_value_ratio": round(share(lambda v: abs(v) < 1000), 4),
        "zero_value_ratio": round(share(lambda v: v == 0), 4),
        "over_dispersed_keys": len(over_dispersed),
        "max_dispersion": dispersion[0][1] if dispersion else 0,
        "compressible_occurrences": compressible,
        "source_distribution": _count(o.get("source") for o in vals),
        "confidence_distribution": _count(o.get("confidence") for o in vals),
        "unit_missing": sum(1 for o in vals if not o.get("unit")),
        "_worst_binding": [
            {"page": k[0], "value": k[1], "keys": sorted(v)[:8], "key_count": len(v)}
            for k, v in worst_binding
        ],
        "_worst_dispersion": [{"key": k, "distinct_values": c} for k, c in dispersion[:8]],
    }


def _count(seq) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in seq:
        out[str(v)] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def ground_truth_recall(occ: list[dict], answers_path: Path) -> dict[str, Any]:
    """答案清单里的每个「原始值」，取数是否在正确页面抓到了 —— 这是召回红线。"""
    from ahcc.eval.matcher import load_official_answer_key

    expected = load_official_answer_key(answers_path)
    misses: list[str] = []
    hits = 0
    for e in expected:
        try:
            target = abs(float(e.original_value.replace(",", "").strip("()")))
        except ValueError:
            continue
        found = any(
            o.get("page") and abs(o["page"] - e.page) <= 1
            and o.get("value") is not None
            and abs(abs(float(o["value"])) - target) < 0.51
            for o in occ
        )
        if found:
            hits += 1
        else:
            misses.append(f"P{e.page} {e.original_value} {e.description[:26]}")
    return {"expected": len(expected), "hit": hits, "missed": misses}


def _print_report(q: dict[str, Any], gt: Optional[dict], baseline: Optional[dict]) -> None:
    def delta(field: str, fmt: str = "{:,}") -> str:
        if not baseline or field not in baseline:
            return ""
        old, new = baseline[field], q[field]
        if old == new:
            return "  (=)"
        arrow = "↓" if new < old else "↑"
        return f"  ({arrow} 原 {fmt.format(old)})"

    print(f"\n=== 取数质量 {q['label']} ===")
    print(f"  canonical_key 数        : {q['key_count']:,}{delta('key_count')}")
    print(f"  occurrence 数           : {q['occurrence_count']:,}{delta('occurrence_count')}")
    print(f"  可压缩量（每 key 保留 {MAX_LEGITIMATE_VALUES_PER_KEY} 值）: {q['compressible_occurrences']:,}")
    print()
    print(f"  多重绑定位置            : {q['multi_bound_positions']:,} / {q['total_positions']:,}"
          f" = {q['multi_bound_ratio']*100:.1f}%{delta('multi_bound_ratio', '{:.4f}')}")
    print(f"  1~31 整数（日期/附注号）  : {q['date_like_ratio']*100:.1f}%{delta('date_like_ratio', '{:.4f}')}")
    print(f"  |值| < 100              : {q['tiny_value_ratio']*100:.1f}%")
    print(f"  |值| < 1000             : {q['small_value_ratio']*100:.1f}%{delta('small_value_ratio', '{:.4f}')}")
    print(f"  取值离散 >{DISPERSION_ALERT_THRESHOLD} 的 key      : {q['over_dispersed_keys']:,}"
          f"{delta('over_dispersed_keys')}   最大离散度 {q['max_dispersion']}")
    print(f"  unit 缺失               : {q['unit_missing']:,}")
    print()
    print(f"  source     : {q['source_distribution']}")
    print(f"  confidence : {q['confidence_distribution']}")

    if q["_worst_binding"]:
        print("\n  绑定最混乱的位置：")
        for b in q["_worst_binding"]:
            print(f"    P{b['page']} 值={b['value']:,.2f} -> {b['key_count']} 个 key: {b['keys'][:5]}")
    if q["_worst_dispersion"]:
        print("\n  取值最离散的 key：")
        for d in q["_worst_dispersion"]:
            print(f"    {d['key']:34s} {d['distinct_values']:4d} 个不同值")

    if gt:
        status = "PASS" if gt["hit"] == gt["expected"] else "FAIL"
        print(f"\n  [召回红线] 答案清单原始值命中: {gt['hit']}/{gt['expected']}  {status}")
        for m in gt["missed"]:
            print(f"    漏抓: {m}")


@app.command()
def main(
    job: Optional[str] = typer.Option(None, help="从已存任务的画像快照统计"),
    pdf: Optional[Path] = typer.Option(None, help="从 PDF 重新解析并统计"),
    side: str = typer.Option("A", help="A 或 H（--pdf 模式下使用）"),
    answers: Optional[Path] = typer.Option(None, help="答案清单 xlsx，用于召回红线检查"),
    baseline: Optional[Path] = typer.Option(None, help="基线 json，用于对比"),
    out: Optional[Path] = typer.Option(None, help="把本次结果写为 json（供下次做基线）"),
) -> None:
    """统计取数质量。"""
    base = json.loads(baseline.read_text(encoding="utf-8")) if baseline and baseline.exists() else None

    if job:
        from ahcc.storage.repository import get_job

        meta = get_job(job, enforce_group=False)
        if not meta:
            print(f"找不到任务 {job}")
            raise typer.Exit(1)
        for field, tag in (("profile_a", "A"), ("profile_h", "H")):
            summary = meta.get(field) or {}
            if not summary:
                continue
            occ = _occurrences_from_profile_summary(summary)
            q = compute_quality(occ, label=f"{job}/{tag}")
            gt = ground_truth_recall(occ, answers) if (answers and tag == "A") else None
            _print_report(q, gt, base if base and base.get("label", "").endswith(f"/{tag}") else None)
            if out:
                _write(out, q, gt, tag)
        return

    if not pdf:
        print("请提供 --job 或 --pdf")
        raise typer.Exit(1)

    from ahcc.parser import parse_report
    from ahcc.profile.extract_metrics import extract_metrics
    from ahcc.schemas import ReportSide

    report_side = ReportSide.A_SHARE if side.upper() == "A" else ReportSide.H_SHARE
    print(f"解析 {pdf.name} ...")
    doc = parse_report(str(pdf), report_side)
    items = extract_metrics(doc)
    occ = _occurrences_from_metric_items(items)
    q = compute_quality(occ, label=f"{pdf.stem[:28]}/{side.upper()}")
    gt = ground_truth_recall(occ, answers) if answers else None
    _print_report(q, gt, base)
    if out:
        _write(out, q, gt, side.upper())


def _write(out: Path, q: dict, gt: Optional[dict], tag: str) -> None:
    path = out if out.suffix == ".json" else out / f"profile_quality_{tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(q)
    if gt:
        payload["ground_truth"] = gt
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已写入 {path}")


if __name__ == "__main__":
    app()
