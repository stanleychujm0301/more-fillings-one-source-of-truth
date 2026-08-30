"""样本评估脚本 —— 量化召回率 / 误报率。

2026-08 重建：旧版本只有一条「植入错误样本 + 全量 diff 同时算召回和误报」的通道，
存在两个结构性问题：

1. **指标制度性地激励关掉检查项。** precision = hit/(hit+fp)，而样本 A/H 对里本来就
   存在大量不在主办方 15 条清单里的真实差异，它们全被记成误报。于是"多开一个检查项"
   必然让 precision 下降 —— 这正是 `ahcc/config.py` 里 5 个开关全 False 的原因。
   现在**召回与误报彻底拆开**，用不同的数据通道算，互不干扰。

2. **7 组真实 A/H 样本产出 0 个指标。** 没有答案文件时整个 job 结果被丢弃，
   而用户抱怨的误报全部发生在这 7 组上。现在无答案时改为导出 FP 上界与抽检工作簿。

四种运行模式
------------

```bash
# 1) 植入错误样本的召回（主办方 sample/ 目录，有答案清单）
python scripts/eval_samples.py recall --samples-dir "F:/毕马威黑客松/样本测试/sample"

# 2) 真实 A/H 样本的误报上界 + 人工抽检工作簿（无答案）
python scripts/eval_samples.py fp --samples-dir "F:/毕马威黑客松/样本测试"

# 3) 自一致性探针：同一份 PDF 自配对，跨报告差异必须为 0
python scripts/eval_samples.py self-check --pdf "F:/.../A 中国平安2025年年度报告.pdf"

# 4) 注入式召回：三种造错方式分别统计（这才是真实召回）
python scripts/eval_samples.py inject --pdf "F:/.../光大银行_2025年H股年报.pdf" --count 90
```

`--overlay-only` 快速模式只跑文本层叠加检测，结果写入 `eval_baseline_overlay.md`，
**不会**覆盖全 pipeline 基线 —— 旧版本两种模式写同一个文件且不记录模式，
是 README 把 overlay-only 的 100% 误标成全 pipeline 结果的直接原因。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahcc.eval import EvalReport, evaluate, export_eval_excel, load_answer_key, print_report
from ahcc.eval.inject import export_injection_manifest, inject_errors, records_to_expected
from ahcc.eval.probes import export_fp_workbook, self_consistency_report
from ahcc.orchestrator import Orchestrator

app = typer.Typer(add_completion=False, help="AHCC 准确率评估")


# ============================================================
# 运行环境快照（写进基线，保证指标可复现）
# ============================================================

def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _enabled_checks() -> dict[str, bool]:
    from ahcc.config import settings

    return {
        name: bool(getattr(settings, name))
        for name in (
            "enable_standard_check",
            "enable_disclosure_coverage_check",
            "enable_a_share_table_extraction",
            "enable_profile_ocr_fallback",
            "enable_chart_vlm_check",
            "enable_text_overlay_check",
        )
    }


def _run_context(mode: str) -> dict:
    return {
        "mode": mode,
        "git_commit": _git_commit(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "enabled_checks": _enabled_checks(),
    }


# ============================================================
# 样本发现
# ============================================================

def _run_pair(a_path: Path, h_path: Path, *, overlay_only: bool = False):
    if overlay_only:
        from types import SimpleNamespace

        from ahcc.check.text_overlay_tamper import run_text_overlay_checks

        diffs = run_text_overlay_checks(str(a_path), str(h_path))
        return SimpleNamespace(diffs=diffs)
    return asyncio.run(Orchestrator().run(str(a_path), str(h_path)))


_A_SIDE_RE = re.compile(r"(^|[^a-z])a([^a-z]|$)|a股|ａ股", re.IGNORECASE)
_H_SIDE_RE = re.compile(r"(^|[^a-z])h([^a-z]|$)|h股|ｈ股", re.IGNORECASE)


def _pick_side(pdfs: list[Path], pattern: re.Pattern) -> Optional[Path]:
    """按「独立的 A/H 字母或 A股/H股」匹配，避免旧实现里裸 'a' in name
    把 'Annual Report' / 'China' 之类的文件名误判成 A 侧。"""
    for p in pdfs:
        if pattern.search(p.stem):
            return p
    return None


def _find_answer(directory: Path) -> Optional[Path]:
    for name in ("answer.xlsx", "answers.xlsx", "samples_answer_key.xlsx"):
        p = directory / name
        if p.exists():
            return p
    xlsx = sorted(directory.glob("*.xlsx"))
    return xlsx[0] if xlsx else None


def _flat_sample_pairs(directory: Path) -> list[tuple[str, Path, Path, Path]]:
    """平铺目录配对：按 `{公司}A股年报_错误清单_*.xlsx` 锚定公司名。"""
    pairs: list[tuple[str, Path, Path, Path]] = []
    pdfs = sorted(directory.glob("*.pdf"))
    for xlsx in sorted(directory.glob("*错误清单*.xlsx")):
        company = re.sub(r"(20\d{2}\s*年?)?A股年报.*$", "", xlsx.stem).strip("_ ")
        if not company:
            continue
        a_pdf = next((p for p in pdfs if p.name.startswith(company) and "含错误" in p.name), None)
        h_pdf = next((p for p in pdfs if p.name.startswith(company) and "H股" in p.name.upper()), None)
        if not a_pdf or not h_pdf:
            print(f"[跳过] {company}：A={'有' if a_pdf else '无'} H={'有' if h_pdf else '无'}")
            continue
        pairs.append((company, a_pdf, h_pdf, xlsx))
    return pairs


def _subdir_pairs(directory: Path) -> list[tuple[str, Path, Path, Optional[Path]]]:
    """子目录式：每个子目录一对 A/H。"""
    pairs: list[tuple[str, Path, Path, Optional[Path]]] = []
    for sub in sorted(p for p in directory.iterdir() if p.is_dir()):
        pdfs = sorted(sub.glob("*.pdf"))
        if len(pdfs) < 2:
            continue
        a = _pick_side(pdfs, _A_SIDE_RE)
        h = _pick_side(pdfs, _H_SIDE_RE)
        if a and h and a.resolve() == h.resolve():
            h = None
        if not a or not h:
            print(f"[跳过] {sub.name} 无法识别 A/H（A={'有' if a else '无'} H={'有' if h else '无'}）")
            continue
        pairs.append((sub.name, a, h, _find_answer(sub)))
    return pairs


# ============================================================
# 模式 1：植入错误召回
# ============================================================

def _eval_one(
    pair_id: str,
    a_path: Path,
    h_path: Path,
    answers_path: Optional[Path],
    out_dir: Path,
    *,
    overlay_only: bool = False,
) -> Optional[EvalReport]:
    print(f"\n=== {pair_id} ===")
    start = time.time()
    try:
        job = _run_pair(a_path, h_path, overlay_only=overlay_only)
    except Exception as exc:  # noqa: BLE001
        print(f"  [失败] 任务执行异常：{exc}")
        return None
    elapsed = time.time() - start
    print(f"处理时长：{elapsed:.1f} 秒，识别差异 {len(job.diffs)} 条")

    if not answers_path or not answers_path.exists():
        print(f"  [无答案] 改走误报通道，导出抽检工作簿")
        bounds = export_fp_workbook(pair_id, job.diffs, out_dir / f"{pair_id}_fp.xlsx")
        print(f"  FP 上界：{bounds}")
        return None

    expected = load_answer_key(answers_path)
    if pair_id and any(e.pair_id for e in expected):
        expected = [e for e in expected if e.pair_id == pair_id or not e.pair_id]
    report = evaluate(job.diffs, expected, pair_id=pair_id)
    print_report(report)
    out_path = out_dir / f"{pair_id}_eval.xlsx"
    export_eval_excel(report, out_path)
    print(f"  评估明细已导出：{out_path}")
    return report


def _write_baseline(reports: list[EvalReport], out_dir: Path, context: dict) -> None:
    """写基线。**模式与运行环境必须记录在文件里** —— 旧版本两种模式写同一个文件
    且不标注模式，是 100% 被误读成全 pipeline 结果的根本原因。"""
    mode = context["mode"]
    filename = "eval_baseline_overlay.md" if mode == "overlay-only" else "eval_baseline.md"
    md = out_dir / filename

    checks = "、".join(k for k, v in context["enabled_checks"].items() if v) or "（无）"
    lines = [
        f"# 样本评估基线（{mode}）",
        "",
        f"- 运行模式：**{mode}**",
        f"- git commit：`{context['git_commit']}`",
        f"- 生成时间：{context['generated_at']}",
        f"- 启用的检查项：{checks}",
        "",
    ]
    if mode == "overlay-only":
        lines += [
            "> **此基线只覆盖 `text_overlay_tamper` 一条路径**，不代表跨报告 A/H 一致性核查的能力。",
            "> 该检查器针对的是「错误值叠加在原值上、原值仍留在文本层」这一种特定制作方式；",
            "> 换一种造错方式（直接改文本层、表格换位）它完全失效 —— 见 `inject` 模式的分方式召回。",
            "",
        ]

    lines += [
        "| 样本对 | 预期 | 检出 | 可见 | 命中 | 被压制 | hard误报 | soft误报 | 自称可解释 | 召回率 | 精确率 | 漏检率 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        miss = round(1 - r.recall, 4)
        lines.append(
            f"| {r.pair_id} | {r.expected_count} | {r.detected_count} | {r.visible_diff_count} | "
            f"{r.hit_count} | {r.detected_but_suppressed_count} | {r.hard_fp_count} | "
            f"{r.soft_fp_count} | {r.suppressed_count} | {r.recall * 100:.1f}% | "
            f"{r.precision * 100:.1f}% | {miss * 100:.1f}% |"
        )
    total_exp = sum(r.expected_count for r in reports)
    total_hit = sum(r.hit_count for r in reports)
    total_hard = sum(r.hard_fp_count for r in reports)
    total_sup = sum(r.detected_but_suppressed_count for r in reports)
    recall = total_hit / total_exp if total_exp else 0.0
    precision = total_hit / (total_hit + total_hard) if (total_hit + total_hard) else 0.0
    lines.append(
        f"| **加权合计** | {total_exp} | - | - | {total_hit} | {total_sup} | {total_hard} | - | - | "
        f"**{recall * 100:.1f}%** | **{precision * 100:.1f}%** | **{(1 - recall) * 100:.1f}%** |"
    )

    # 按 rule_id 分组的召回 —— 避免单一路径的 100% 掩盖其余路径的 0%
    merged: dict[str, list[int]] = {}
    for r in reports:
        for rule_id, (hits, total) in r.recall_by_rule_id.items():
            bucket = merged.setdefault(rule_id, [0, 0])
            bucket[0] += hits
            bucket[1] += total
    if merged:
        lines += ["", "## 按 rule_id 分组的命中分布", "",
                  "| rule_id | 命中 | 参与匹配 |", "|---|---|---|"]
        for rule_id, (hits, total) in sorted(merged.items(), key=lambda kv: -kv[1][1]):
            lines.append(f"| `{rule_id}` | {hits} | {total} |")

    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n基线指标已写入：{md}")


@app.command("recall")
def cmd_recall(
    samples_dir: Optional[Path] = typer.Option(None, help="样本根目录（子目录式或平铺式）"),
    pair: Optional[str] = typer.Option(None, help="单对模式：A,H 文件路径，逗号分隔"),
    answers: Optional[Path] = typer.Option(None, help="单对模式：预期答案 Excel"),
    out: Path = typer.Option(Path("storage/eval"), help="输出目录"),
    overlay_only: bool = typer.Option(False, "--overlay-only", help="只跑文本层叠加检测（秒级）"),
) -> None:
    """植入错误样本的召回评估（需要答案清单）。"""
    out.mkdir(parents=True, exist_ok=True)
    reports: list[EvalReport] = []

    if samples_dir:
        flat = _flat_sample_pairs(samples_dir)
        if flat:
            for company, a, h, ans in flat:
                r = _eval_one(company, a, h, ans, out, overlay_only=overlay_only)
                if r:
                    reports.append(r)
        else:
            for name, a, h, ans in _subdir_pairs(samples_dir):
                r = _eval_one(name, a, h, ans, out, overlay_only=overlay_only)
                if r:
                    reports.append(r)
    elif pair:
        a_path, h_path = pair.split(",")
        r = _eval_one("single", Path(a_path.strip()), Path(h_path.strip()), answers, out,
                      overlay_only=overlay_only)
        if r:
            reports.append(r)
    else:
        print("请提供 --samples-dir 或 --pair")
        raise typer.Exit(1)

    if not reports:
        print("\n没有任何带答案的样本对完成评估。")
        raise typer.Exit(1)

    context = _run_context("overlay-only" if overlay_only else "full-pipeline")
    print("\n=== 汇总 ===")
    total_exp = sum(r.expected_count for r in reports)
    total_hit = sum(r.hit_count for r in reports)
    total_hard = sum(r.hard_fp_count for r in reports)
    recall = total_hit / total_exp if total_exp else 0.0
    precision = total_hit / (total_hit + total_hard) if (total_hit + total_hard) else 0.0
    print(f"  样本组数 {len(reports)}，加权召回率 {recall * 100:.1f}%，"
          f"加权精确率 {precision * 100:.1f}%，加权漏检率 {(1 - recall) * 100:.1f}%")
    _write_baseline(reports, out, context)


# ============================================================
# 模式 2：真实样本的误报上界
# ============================================================

@app.command("fp")
def cmd_fp(
    samples_dir: Path = typer.Option(..., help="真实 A/H 样本根目录（每个子目录一对）"),
    out: Path = typer.Option(Path("storage/eval"), help="输出目录"),
    sample_per_bucket: int = typer.Option(20, help="每个分桶的抽检条数"),
) -> None:
    """真实 A/H 样本的误报上界 + 人工抽检工作簿（不需要标准答案）。"""
    out.mkdir(parents=True, exist_ok=True)
    context = _run_context("fp-upper-bound")
    rows: list[tuple[str, dict, float]] = []

    for name, a, h, _ in _subdir_pairs(samples_dir):
        print(f"\n=== {name} ===")
        start = time.time()
        try:
            job = _run_pair(a, h)
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] {exc}")
            continue
        elapsed = time.time() - start
        bounds = export_fp_workbook(
            name, job.diffs, out / f"{name}_fp.xlsx", sample_per_bucket=sample_per_bucket
        )
        print(f"  {elapsed:.1f} 秒，差异 {len(job.diffs)} 条 → {bounds}")
        rows.append((name, bounds, elapsed))

    if not rows:
        print("没有可评估的样本对。")
        raise typer.Exit(1)

    md = out / "fp_upper_bound.md"
    checks = "、".join(k for k, v in context["enabled_checks"].items() if v) or "（无）"
    lines = [
        "# 真实 A/H 样本误报上界",
        "",
        f"- git commit：`{context['git_commit']}`　生成时间：{context['generated_at']}",
        f"- 启用的检查项：{checks}",
        "",
        "> 同一家公司的 A 股与 H 股年报，同一指标本应一致。因此每一条「跨报告 · real」",
        "> 差异都是待人工确认的条目，其**全量条数即 FP 上界**。抽检确认后用 Wilson 区间",
        "> 给出 FP 率的区间估计（见各样本的 `_fp.xlsx` 「人工抽检」表）。",
        "",
        "| 样本对 | 耗时(s) | 跨报告·real | 跨报告·unresolved | 跨报告·expected | 单侧内部 |",
        "|---|---|---|---|---|---|",
    ]
    for name, b, elapsed in rows:
        lines.append(
            f"| {name} | {elapsed:.0f} | **{b.get('cross_report_real', 0)}** | "
            f"{b.get('cross_report_unresolved', 0)} | {b.get('cross_report_expected', 0)} | "
            f"{b.get('internal', 0)} |"
        )
    total_real = sum(b.get("cross_report_real", 0) for _, b, _ in rows)
    lines.append(f"| **合计** | - | **{total_real}** | - | - | - |")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nFP 上界已写入：{md}")


# ============================================================
# 模式 3：自一致性探针
# ============================================================

@app.command("self-check")
def cmd_self_check(
    pdf: list[Path] = typer.Option(..., help="待自检的 PDF（可重复传多个）"),
    out: Path = typer.Option(Path("storage/eval"), help="输出目录"),
    overlay_only: bool = typer.Option(False, "--overlay-only", help="只跑文本层叠加检测"),
) -> None:
    """A=H 自一致性探针：同一份 PDF 自配对，跨报告差异必须为 0。"""
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    reports = []
    for path in pdf:
        try:
            job = _run_pair(path, path, overlay_only=overlay_only)
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] {path.name}: {exc}")
            failed += 1
            continue
        report = self_consistency_report(job.diffs, pair_id=path.stem[:40])
        print(report.summary_line())
        reports.append(report)
        if not report.passed:
            failed += 1
            export_fp_workbook(
                f"selfcheck_{path.stem[:30]}", report.cross_report_diffs,
                out / f"selfcheck_{path.stem[:30]}.xlsx",
            )

    md = out / "self_consistency.md"
    lines = [
        "# A=H 自一致性探针",
        "",
        "> 把同一份 PDF 同时当 A 和 H 传入。两侧完全相同，因此**任何跨报告差异都是纯误报**。",
        "",
        "| PDF | 跨报告差异 | 总差异 | 结果 | 主要 rule_id |",
        "|---|---|---|---|---|",
    ]
    for r in reports:
        top = sorted(r.by_rule_id.items(), key=lambda kv: -kv[1])[:3]
        lines.append(
            f"| {r.pair_id} | **{r.cross_report_count}** | {r.total_diffs} | "
            f"{'PASS' if r.passed else 'FAIL'} | {' '.join(f'`{k}`×{v}' for k, v in top) or '-'} |"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n结果已写入：{md}")
    if failed:
        raise typer.Exit(1)


# ============================================================
# 模式 4：注入式召回
# ============================================================

@app.command("inject")
def cmd_inject(
    pdf: Path = typer.Option(..., help="干净的源 PDF"),
    count: int = typer.Option(90, help="注入错误数（会在三种方式间均分）"),
    seed: int = typer.Option(7, help="随机种子，保证可复现"),
    out: Path = typer.Option(Path("storage/eval"), help="输出目录"),
    overlay_only: bool = typer.Option(
        False, "--overlay-only", help="只跑文本层叠加检测（用于证明其泛化边界）"
    ),
    counterpart: Optional[Path] = typer.Option(
        None, help="另一侧 PDF；不传则用原始未注入的 PDF 作为对照侧"
    ),
) -> None:
    """注入式召回：三种造错方式分别统计召回率。

    只有 `overlay` 能被 text_overlay_tamper 检出；`edit` 与 `swap` 必须靠
    跨报告比对 / 勾稽校验 / 内部一致性才能发现 —— 这才是真实召回。
    """
    out.mkdir(parents=True, exist_ok=True)
    tampered = out / f"injected_{pdf.stem[:30]}.pdf"
    records = inject_errors(pdf, tampered, count=count, seed=seed)
    export_injection_manifest(records, out / f"injected_{pdf.stem[:30]}_manifest.xlsx")
    if not records:
        print("未能注入任何错误（源 PDF 可能没有可用的财务数字目标）。")
        raise typer.Exit(1)

    other = counterpart or pdf  # 对照侧用未注入的原文件
    start = time.time()
    job = _run_pair(tampered, other, overlay_only=overlay_only)
    elapsed = time.time() - start
    print(f"\n跑完 {elapsed:.1f} 秒，检出 {len(job.diffs)} 条差异")

    context = _run_context("inject-overlay-only" if overlay_only else "inject-full-pipeline")
    per_method: list[tuple[str, EvalReport]] = []
    for method in ("overlay", "edit", "swap"):
        subset = [r for r in records if r.method == method]
        if not subset:
            continue
        rep = evaluate(job.diffs, records_to_expected(subset), pair_id=method)
        per_method.append((method, rep))
        print(
            f"  {method:8s} 注入 {rep.expected_count:3d}  命中 {rep.hit_count:3d}  "
            f"被压制 {rep.detected_but_suppressed_count:3d}  召回 {rep.recall * 100:5.1f}%"
        )
    overall = evaluate(job.diffs, records_to_expected(records), pair_id="all")
    print(
        f"  {'合计':8s} 注入 {overall.expected_count:3d}  命中 {overall.hit_count:3d}  "
        f"召回 {overall.recall * 100:5.1f}%  hard误报 {overall.hard_fp_count}"
    )
    export_eval_excel(overall, out / f"inject_{pdf.stem[:30]}_eval.xlsx")

    md = out / "inject_recall.md"
    checks = "、".join(k for k, v in context["enabled_checks"].items() if v) or "（无）"
    lines = [
        "# 注入式召回评估",
        "",
        f"- 源 PDF：`{pdf.name}`　种子：{seed}　注入 {len(records)} 处",
        f"- 运行模式：**{context['mode']}**　git commit：`{context['git_commit']}`",
        f"- 生成时间：{context['generated_at']}　耗时 {elapsed:.1f}s",
        f"- 启用的检查项：{checks}",
        "",
        "> 三种造错方式的关键区别在于**原值是否还留在文本层**。只有 `overlay` 留了，",
        "> 因此只有它能被 `text_overlay_tamper` 检出。`edit` / `swap` 的召回率才是",
        "> 这套系统面对未知造错方式时的真实能力。",
        "",
        "| 注入方式 | 原值是否残留 | 注入数 | 命中 | 检出但被压制 | 召回率 |",
        "|---|---|---|---|---|---|",
    ]
    residual = {"overlay": "是", "edit": "否", "swap": "否"}
    for method, rep in per_method:
        lines.append(
            f"| `{method}` | {residual.get(method, '-')} | {rep.expected_count} | {rep.hit_count} | "
            f"{rep.detected_but_suppressed_count} | **{rep.recall * 100:.1f}%** |"
        )
    lines.append(
        f"| **合计** | - | {overall.expected_count} | {overall.hit_count} | "
        f"{overall.detected_but_suppressed_count} | **{overall.recall * 100:.1f}%** |"
    )
    lines += ["", f"hard 误报 {overall.hard_fp_count} / soft 误报 {overall.soft_fp_count} / "
                  f"自称可解释 {overall.suppressed_count}"]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n结果已写入：{md}")

    (out / "inject_recall.json").write_text(
        json.dumps(
            {
                "context": context,
                "source_pdf": str(pdf),
                "injected": len(records),
                "by_method": {m: {"expected": r.expected_count, "hit": r.hit_count,
                                  "recall": r.recall} for m, r in per_method},
                "overall": {"expected": overall.expected_count, "hit": overall.hit_count,
                            "recall": overall.recall, "hard_fp": overall.hard_fp_count},
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    app()
