"""主办方样本评估脚本 — 量化召回率/精确率/漏检率（对应题目漏检率 ≤5% 要求）。

单对模式：
    python scripts/eval_samples.py --pair samples/A.pdf,samples/H.pdf \
        --answers kb/samples_answer_key.xlsx

批量模式（评估 3 组样本并汇总基线，写入 storage/eval/eval_baseline.md）：
    python scripts/eval_samples.py --samples-dir samples/ --out storage/eval

samples-dir 支持两种布局：
1. 子目录式：每个子目录为一个样本对（A 股 PDF + H 股 PDF + answer.xlsx），子目录名即 pair_id；
2. 平铺式（主办方 sample/ 目录原样）：目录下直接放
   {公司}A股年报_含错误_测试版.pdf + {公司}A股年报_错误清单_15处.xlsx + {公司}_20XX年H股年报.pdf，
   按公司名前缀自动配对。

--overlay-only 快速模式：跳过全 pipeline，仅跑文本层叠加篡改检测（秒级），用于日常回归。
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Optional

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahcc.eval import EvalReport, evaluate, export_eval_excel, load_answer_key, print_report
from ahcc.orchestrator import Orchestrator

app = typer.Typer(add_completion=False)


def _run_pair(a_path: Path, h_path: Path, *, overlay_only: bool = False):
    if overlay_only:
        from types import SimpleNamespace

        from ahcc.check.text_overlay_tamper import run_text_overlay_checks

        diffs = run_text_overlay_checks(str(a_path), str(h_path))
        return SimpleNamespace(diffs=diffs)
    return asyncio.run(Orchestrator().run(str(a_path), str(h_path)))


def _find_pdf(directory: Path, keyword: str) -> Optional[Path]:
    """在目录中找文件名含 keyword 的 PDF（不区分大小写）。"""
    candidates = sorted(directory.glob("*.pdf"))
    for p in candidates:
        if keyword in p.name.lower():
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
    """平铺目录配对：按 `{公司}A股年报_错误清单_*.xlsx` 锚定公司名，找同名含错误 A 股 PDF 与 H 股 PDF。

    返回 [(company, a_pdf, h_pdf, answers_xlsx), ...]
    """
    pairs: list[tuple[str, Path, Path, Path]] = []
    pdfs = sorted(directory.glob("*.pdf"))
    for xlsx in sorted(directory.glob("*错误清单*.xlsx")):
        company = re.sub(r"(20\d{2}\s*年?)?A股年报.*$", "", xlsx.stem).strip("_ ")
        if not company:
            continue
        a_pdf = next(
            (p for p in pdfs if p.name.startswith(company) and "含错误" in p.name),
            None,
        )
        h_pdf = next(
            (p for p in pdfs if p.name.startswith(company) and "H股" in p.name.upper()),
            None,
        )
        if not a_pdf or not h_pdf:
            print(f"[跳过] {company}：A={'有' if a_pdf else '无'} H={'有' if h_pdf else '无'}")
            continue
        pairs.append((company, a_pdf, h_pdf, xlsx))
    return pairs


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
    by_sev: dict[str, int] = {}
    for d in job.diffs:
        by_sev[d.severity.value] = by_sev.get(d.severity.value, 0) + 1
    if by_sev:
        print("  严重度分布：" + "  ".join(f"{k}={v}" for k, v in sorted(by_sev.items())))

    if not answers_path or not answers_path.exists():
        print(f"  [跳过对比] 未提供答案文件：{answers_path}")
        return None
    expected = load_answer_key(answers_path)
    # 答案 Excel 含多组时按 pair_id 过滤；条目无 pair_id 的视为通用，保留
    if pair_id and any(e.pair_id for e in expected):
        expected = [e for e in expected if e.pair_id == pair_id or not e.pair_id]
    report = evaluate(job.diffs, expected, pair_id=pair_id)
    print_report(report)
    out_path = out_dir / f"{pair_id}_eval.xlsx"
    export_eval_excel(report, out_path)
    print(f"  评估明细已导出：{out_path}")
    return report


def _write_baseline(reports: list[EvalReport], out_dir: Path) -> None:
    md = out_dir / "eval_baseline.md"
    lines = [
        "# 主办方样本评估基线",
        "",
        "> 自动生成于评估脚本运行时；召回率对应题目“漏检率 ≤5%”要求（漏检率 = 1 - 召回率）。",
        "",
        "| 样本对 | 预期 | 检出 | 命中 | 误报 | 召回率 | 精确率 | 漏检率 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        miss = round(1 - r.recall, 4)
        lines.append(
            f"| {r.pair_id} | {r.expected_count} | {r.detected_count} | {r.hit_count} | "
            f"{r.false_positive_count} | {r.recall * 100:.1f}% | {r.precision * 100:.1f}% | {miss * 100:.1f}% |"
        )
    total_exp = sum(r.expected_count for r in reports)
    total_hit = sum(r.hit_count for r in reports)
    total_fp = sum(r.false_positive_count for r in reports)
    recall = total_hit / total_exp if total_exp else 0.0
    precision = total_hit / (total_hit + total_fp) if (total_hit + total_fp) else 0.0
    lines.append(
        f"| **加权合计** | {total_exp} | - | {total_hit} | {total_fp} | "
        f"**{recall * 100:.1f}%** | **{precision * 100:.1f}%** | **{(1 - recall) * 100:.1f}%** |"
    )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n基线指标已写入：{md}")


@app.command()
def main(
    pair: str = typer.Option(None, help="单对模式：A,H 文件路径，逗号分隔"),
    answers: Path = typer.Option(None, help="单对模式：预期答案 Excel"),
    samples_dir: Path = typer.Option(None, help="批量模式：样本根目录（子目录式或平铺式）"),
    out: Path = typer.Option(Path("storage/eval"), help="评估明细输出目录"),
    overlay_only: bool = typer.Option(
        False, "--overlay-only", help="快速模式：仅跑文本层叠加篡改检测（秒级，日常回归用）"
    ),
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    reports: list[EvalReport] = []

    if samples_dir:
        subdirs = sorted(p for p in samples_dir.iterdir() if p.is_dir())
        flat_pairs = _flat_sample_pairs(samples_dir)
        if flat_pairs:
            # 平铺式（主办方 sample/ 目录原样）
            for company, a, h, ans in flat_pairs:
                r = _eval_one(company, a, h, ans, out, overlay_only=overlay_only)
                if r:
                    reports.append(r)
        else:
            for sub in subdirs:
                a = _find_pdf(sub, "a")
                h = _find_pdf(sub, "h")
                # 避免 H 文件被 a 关键词误匹配（如 'share'），二次确认 H 文件名含 h 但排除已选 A
                if a and h and a.resolve() == h.resolve():
                    h = None
                ans = _find_answer(sub)
                if not a or not h:
                    print(f"[跳过] {sub.name} 缺少 A/H PDF（A={'有' if a else '无'} H={'有' if h else '无'}）")
                    continue
                r = _eval_one(sub.name, a, h, ans, out, overlay_only=overlay_only)
                if r:
                    reports.append(r)
    elif pair:
        a_path, h_path = pair.split(",")
        r = _eval_one(
            "single", Path(a_path.strip()), Path(h_path.strip()), answers, out, overlay_only=overlay_only
        )
        if r:
            reports.append(r)
    else:
        print("请提供 --pair 或 --samples-dir")
        raise typer.Exit(1)

    if reports:
        print("\n=== 汇总 ===")
        total_exp = sum(r.expected_count for r in reports)
        total_hit = sum(r.hit_count for r in reports)
        total_fp = sum(r.false_positive_count for r in reports)
        recall = total_hit / total_exp if total_exp else 0.0
        precision = total_hit / (total_hit + total_fp) if (total_hit + total_fp) else 0.0
        print(
            f"  样本组数 {len(reports)}，加权召回率 {recall * 100:.1f}%，"
            f"加权精确率 {precision * 100:.1f}%，加权漏检率 {(1 - recall) * 100:.1f}%"
        )
        _write_baseline(reports, out)


if __name__ == "__main__":
    app()
