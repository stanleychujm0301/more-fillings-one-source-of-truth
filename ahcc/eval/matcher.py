"""答案对比核心逻辑：系统检出 Diff 与预期答案 ExpectedDiff 的匹配与指标计算。

匹配策略（按优先级）：
1. exact  — expected_rule_id 与 diff.rule_id 完全相等，且证据页码接近
2. prefix — rule_id 互为前缀，且页码接近
3. fuzzy  — 预期主题与差异文本的 glossary canonical_key 存在交集，且页码接近

指标：
- 召回率 = 命中 ExpectedDiff 数 / ExpectedDiff 总数
- 精确率 = 命中数 / (命中数 + 误报数)，误报 = 未命中任何预期且非 expected triage 的 diff
- 漏检率 = 1 - 召回率
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from ahcc.align.glossary import glossary, to_simplified
from ahcc.schemas import Diff, ReportSide

_PAGE_TOLERANCE = 3


@dataclass
class ExpectedDiff:
    pair_id: str = ""
    company: str = ""
    expected_rule_id: str = ""
    topic: str = ""
    expected_severity: str = ""
    a_page: int | None = None
    h_page: int | None = None
    note: str = ""
    # 官方错误清单格式专属字段（序号/PDF页码/描述/原始数字/错误数字）
    page: int | None = None
    original_value: str = ""
    tampered_value: str = ""
    description: str = ""


@dataclass
class MatchResult:
    expected: ExpectedDiff
    matched_diff: Diff | None = None
    match_level: str = "missed"  # exact / prefix / fuzzy / missed
    reason: str = ""


@dataclass
class EvalReport:
    pair_id: str
    expected_count: int
    detected_count: int
    hit_count: int
    false_positive_count: int
    recall: float
    precision: float
    matches: list[MatchResult] = field(default_factory=list)
    unmatched_diffs: list[Diff] = field(default_factory=list)


def load_answer_key(path: Path) -> list[ExpectedDiff]:
    """读取预期答案 Excel。自动嗅探表头格式：

    - 官方错误清单格式（序号/PDF页码/描述/原始数字/错误数字/…）→ load_official_answer_key
    - 内部格式（pair_id/expected_rule_id/topic/…）→ 原逻辑
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(c or "").strip().lower() for c in rows[0]]
    if _is_official_answer_header(header):
        return _parse_official_answer_rows(rows)

    def idx(name: str) -> int | None:
        return header.index(name) if name in header else None

    def cell(row: tuple, name: str) -> Any:
        i = idx(name)
        if i is None or i >= len(row):
            return None
        return row[i]

    def to_int(v: Any) -> int | None:
        try:
            return int(float(v)) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    expected: list[ExpectedDiff] = []
    for row in rows[1:]:
        if not any(cell(row, n) for n in ("pair_id", "expected_rule_id", "topic")):
            continue
        expected.append(
            ExpectedDiff(
                pair_id=str(cell(row, "pair_id") or "").strip(),
                company=str(cell(row, "company") or "").strip(),
                expected_rule_id=str(cell(row, "expected_rule_id") or "").strip(),
                topic=str(cell(row, "topic") or "").strip(),
                expected_severity=str(cell(row, "expected_severity") or "").strip().lower(),
                a_page=to_int(cell(row, "a_page")),
                h_page=to_int(cell(row, "h_page")),
                note=str(cell(row, "note") or "").strip(),
            )
        )
    return expected


_OFFICIAL_HEADER_KEYS = ("pdf页码", "原始数字", "错误数字")


def _is_official_answer_header(header: list[str]) -> bool:
    joined = "".join(header)
    return all(key in joined for key in _OFFICIAL_HEADER_KEYS)


def load_official_answer_key(path: Path) -> list[ExpectedDiff]:
    """读取主办方官方错误清单（列：序号/PDF页码/描述/原始数字/错误数字/差异额/变动说明）。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = list(wb.active.iter_rows(values_only=True))
    return _parse_official_answer_rows(rows) if rows else []


def _parse_official_answer_rows(rows: list[tuple]) -> list[ExpectedDiff]:
    header = [str(c or "").strip().lower() for c in rows[0]]

    def idx_of(*names: str) -> int | None:
        for i, cell in enumerate(header):
            if any(name in cell for name in names):
                return i
        return None

    page_idx = idx_of("pdf页码", "页码")
    desc_idx = idx_of("描述")
    orig_idx = idx_of("原始数字", "原始")
    tamp_idx = idx_of("错误数字", "错误")
    note_idx = idx_of("变动说明", "说明")

    def cell(row: tuple, i: int | None) -> str:
        if i is None or i >= len(row) or row[i] is None:
            return ""
        return str(row[i]).strip()

    expected: list[ExpectedDiff] = []
    for row in rows[1:]:
        page_text = cell(row, page_idx)
        tampered = cell(row, tamp_idx)
        if not page_text or not tampered:
            continue
        try:
            page = int(float(page_text))
        except ValueError:
            continue
        expected.append(
            ExpectedDiff(
                topic=cell(row, desc_idx),
                page=page,
                a_page=page,  # 植入错误都在 A 股侧，页码同时供旧的页码接近判断使用
                original_value=cell(row, orig_idx),
                tampered_value=tampered,
                description=cell(row, desc_idx),
                note=cell(row, note_idx),
            )
        )
    return expected


def _normalize_value_text(text: str) -> str:
    """数值文本规范化：去千分位逗号/括号/百分号/空格，用于跨格式匹配。"""
    return (
        str(text)
        .replace(",", "")
        .replace("(", "")
        .replace(")", "")
        .replace("%", "")
        .replace(" ", "")
        .strip()
    )


def _diff_value_texts(diff: Diff) -> str:
    """把 diff 中所有可能包含数值的文本拼成一个规范化后的检索串。"""
    parts: list[str] = []
    for value in (diff.a_value, diff.h_value):
        if value is None:
            continue
        parts.append(f"{value:.4f}".rstrip("0").rstrip("."))
        parts.append(f"{value:,.2f}")
        if value == int(value):
            parts.append(f"{int(value):,}")
            parts.append(str(int(value)))
    parts.extend(filter(None, [diff.summary.zh, diff.summary.en]))
    parts.extend(ev.snippet or "" for ev in diff.evidence)
    return _normalize_value_text(" ".join(parts))


def _match_by_values(diff: Diff, exp: ExpectedDiff) -> tuple[str, str]:
    """官方错误清单的值匹配：页码接近 + 错误数字/原始数字出现在 diff 的值或文本里。"""
    if not exp.tampered_value and not exp.original_value:
        return "", ""
    a_page, h_page = _diff_pages(diff)
    diff_page = a_page if a_page is not None else h_page
    if exp.page is not None and diff_page is not None and abs(diff_page - exp.page) > 2:
        return "", ""
    haystack = _diff_value_texts(diff)
    tampered = _normalize_value_text(exp.tampered_value) if exp.tampered_value else ""
    original = _normalize_value_text(exp.original_value) if exp.original_value else ""
    tampered_hit = bool(tampered) and tampered in haystack
    original_hit = bool(original) and original in haystack
    if tampered_hit and original_hit:
        return "exact", f"错误值 {exp.tampered_value} 与原始值 {exp.original_value} 均命中"
    if tampered_hit:
        return "prefix", f"错误值 {exp.tampered_value} 命中"
    if original_hit:
        return "prefix", f"原始值 {exp.original_value} 命中"
    return "", ""


def _diff_pages(diff: Diff) -> tuple[int | None, int | None]:
    a_page = None
    h_page = None
    for ev in diff.evidence:
        if ev.side == ReportSide.A_SHARE and a_page is None:
            a_page = ev.page
        elif ev.side == ReportSide.H_SHARE and h_page is None:
            h_page = ev.page
    return a_page, h_page


def _topic_keys(text: str) -> set[str]:
    """提取文本中的 glossary canonical_key，用于跨语言主题模糊匹配。复用 glossary 术语表。"""
    if not text:
        return set()
    norm = to_simplified(text).lower()
    keys: set[str] = set()
    for form, canonical in glossary._to_canonical.items():
        if len(form) >= 3 and form in norm:
            keys.add(canonical)
    return keys


def _page_close(a: int | None, b: int | None, tol: int = _PAGE_TOLERANCE) -> bool:
    """一侧未指定页码时不作为否决条件（答案可能只标了一侧）。"""
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


def _match_diff_to_expected(diff: Diff, exp: ExpectedDiff) -> tuple[str, str]:
    """返回 (匹配级别, 原因)；不匹配返回 ("", "")。"""
    # 0. 官方错误清单：按 页码+数值 匹配（优先级最高，命中即返回）
    level, reason = _match_by_values(diff, exp)
    if level:
        return level, reason

    a_page, h_page = _diff_pages(diff)
    pages_ok = _page_close(a_page, exp.a_page) and _page_close(h_page, exp.h_page)

    # 1. rule_id 精确
    if exp.expected_rule_id and diff.rule_id:
        if exp.expected_rule_id == diff.rule_id:
            return ("exact", f"rule_id 精确匹配 {diff.rule_id}") if pages_ok else ("", "rule_id 匹配但页码不接近")
        # 2. rule_id 前缀
        if exp.expected_rule_id in diff.rule_id or diff.rule_id in exp.expected_rule_id:
            return ("prefix", f"rule_id 前缀匹配 {diff.rule_id}") if pages_ok else ("", "rule_id 前缀匹配但页码不接近")

    # 3. 模糊：主题 canonical_key 交集 + 页码
    exp_keys = _topic_keys(exp.topic)
    if exp_keys:
        diff_texts = " ".join(
            filter(None, [
                diff.topic.zh, diff.topic.en, diff.summary.zh, diff.summary.en,
                diff.diff_explanation.headline if diff.diff_explanation else "",
            ])
        )
        diff_keys = _topic_keys(diff_texts)
        overlap = exp_keys & diff_keys
        if overlap and pages_ok:
            return "fuzzy", f"主题关键词交集 {overlap}，页码接近"
    return "", ""


_LEVEL_RANK = {"exact": 3, "prefix": 2, "fuzzy": 1}


def evaluate(diffs: list[Diff], expected: list[ExpectedDiff], *, pair_id: str = "") -> EvalReport:
    """对每条 ExpectedDiff 在 diffs 中找最佳命中（高级别优先），计算指标。"""
    matches: list[MatchResult] = []
    used_diffs: set[int] = set()
    hit = 0
    for exp in expected:
        best: tuple[str, str, int] | None = None
        for i, diff in enumerate(diffs):
            if i in used_diffs:
                continue
            level, reason = _match_diff_to_expected(diff, exp)
            if level and (best is None or _LEVEL_RANK[level] > _LEVEL_RANK[best[0]]):
                best = (level, reason, i)
        if best is not None:
            used_diffs.add(best[2])
            matches.append(MatchResult(expected=exp, matched_diff=diffs[best[2]], match_level=best[0], reason=best[1]))
            hit += 1
        else:
            matches.append(MatchResult(expected=exp, matched_diff=None, match_level="missed", reason="未检出"))

    unmatched_diffs = [d for i, d in enumerate(diffs) if i not in used_diffs and d.triage != "expected"]
    fp = len(unmatched_diffs)
    recall = hit / len(expected) if expected else 0.0
    precision = hit / (hit + fp) if (hit + fp) else 0.0
    return EvalReport(
        pair_id=pair_id,
        expected_count=len(expected),
        detected_count=len(diffs),
        hit_count=hit,
        false_positive_count=fp,
        recall=round(recall, 4),
        precision=round(precision, 4),
        matches=matches,
        unmatched_diffs=unmatched_diffs,
    )


def export_eval_excel(report: EvalReport, out_path: Path) -> None:
    """导出评估明细 Excel：指标汇总 / 命中明细 / 漏检清单 / 误报清单。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws = wb.active
    ws.title = "指标汇总"
    ws.append(["指标", "数值"])
    miss_rate = round(1 - report.recall, 4) if report.expected_count else 0.0
    for k, v in [
        ("样本对", report.pair_id),
        ("预期差异数", report.expected_count),
        ("检出差异数", report.detected_count),
        ("命中数", report.hit_count),
        ("误报数", report.false_positive_count),
        ("召回率", report.recall),
        ("精确率", report.precision),
        ("漏检率", miss_rate),
    ]:
        ws.append([k, v])

    ws_hit = wb.create_sheet("命中明细")
    ws_hit.append(["预期规则", "预期主题", "匹配级别", "命中差异ID", "命中rule_id", "匹配原因"])
    for m in report.matches:
        if m.matched_diff is not None:
            ws_hit.append([
                m.expected.expected_rule_id, m.expected.topic, m.match_level,
                m.matched_diff.diff_id, m.matched_diff.rule_id, m.reason,
            ])

    ws_miss = wb.create_sheet("漏检清单")
    ws_miss.append(["预期规则", "预期主题", "预期严重度", "预期A页", "预期H页", "备注", "待补规则建议"])
    for m in report.matches:
        if m.match_level != "missed":
            continue
        if m.expected.expected_rule_id:
            suggest = f"规则 {m.expected.expected_rule_id} 未触发，请检查规则配置与对齐"
        else:
            suggest = f"建议新增规则覆盖：{m.expected.topic}"
        ws_miss.append([
            m.expected.expected_rule_id, m.expected.topic, m.expected.expected_severity,
            m.expected.a_page, m.expected.h_page, m.expected.note, suggest,
        ])

    ws_fp = wb.create_sheet("误报清单")
    ws_fp.append(["差异ID", "rule_id", "严重度", "主题", "差异说明", "定位"])
    for d in report.unmatched_diffs:
        a_page, h_page = _diff_pages(d)
        ws_fp.append([d.diff_id, d.rule_id, d.severity.value, d.topic.best(), d.summary.best(), f"A{a_page}/H{h_page}"])

    wb.save(out_path)


def print_report(report: EvalReport) -> None:
    miss_rate = round(1 - report.recall, 4) if report.expected_count else 0.0
    print(
        f"[{report.pair_id or '-'}] 预期 {report.expected_count} / 检出 {report.detected_count} / "
        f"命中 {report.hit_count} / 误报 {report.false_positive_count}"
    )
    print(f"  召回率 {report.recall * 100:.1f}%  精确率 {report.precision * 100:.1f}%  漏检率 {miss_rate * 100:.1f}%")
    missed = [m for m in report.matches if m.match_level == "missed"]
    if missed:
        print(f"  漏检 {len(missed)} 条：")
        for m in missed:
            print(f"    - [{m.expected.expected_rule_id or '?'}] {m.expected.topic}")
