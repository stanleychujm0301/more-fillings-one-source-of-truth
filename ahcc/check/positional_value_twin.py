"""位置孪生数值比对 — 同文档两版本对查（注入评估/篡改复核）的 edit/swap 主力检出。

为什么用坐标而不是文本行/序列
------------------------------
实测注入 PDF（edit/swap 走 PyMuPDF redaction）：被删改的值会以新文本对象**重写在原
坐标**，但它在文本层抽取序列中的位置被挪到页尾 —— 行配对（text_line_twin）和序列
对齐都会系统性失效。坐标不会说谎：新值永远落在原值的位置上。

做法
----
逐页取 `get_text("words")` 的数值 token（≥4 位有效数字、剔除年份），按
「左边缘 x0 + 垂直中心」跨版本贪心配对（同文档同位置，Helvetica 数字等宽，
bbox 几乎重合）：

- edit：同位置取值不同 → 取值冲突；
- swap：两个位置各自取值冲突且互为镜像（p 位 X→Y、q 位 Y→X）→ 合并为一条，
  避免镜像端成为评估 hard FP；
- overlay：同位置 A 侧存在两个 token（原值残留 + 新值叠加），其中一个与 H 侧
  相等 → 属 text_overlay_tamper 辖区，跳过不重复报告。

噪声免疫（本模块只在同文档对查时才有意义）：
- 双侧总页数必须相等；
- 页级配对率 ≥90% 且配对数 ≥8（真实 A/H 跨报告版式完全不同，配对率趋零，
  天然整页跳过；self-check 同文件配对率 100% 但无冲突）；
- 每页最多 5 条、每任务最多 40 条熔断。

产出 rule_id="positional_value_conflict"，triage="real"，供评估匹配器
（页码 ±2 + 篡改值 token + triage=real + severity≥medium）直接命中。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from loguru import logger

from ahcc.check.explanation import make_value_explanation
from ahcc.profile.extract_metrics import _parse_number
from ahcc.schemas import (
    Diff,
    DiffScope,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportDocument,
    ReportSide,
)

RULE_ID = "positional_value_conflict"

_MIN_DIGITS = 4          # 与 inject._MIN_DIGITS 对齐
_PAIR_X_TOL = 2.0        # 左边缘 x0 配对容差（pt）
_PAIR_Y_TOL = 2.0        # 垂直中心配对容差（pt）
_MIN_PAIRED = 4          # 页级最少配对数（稀疏页如 P327 仅 4 个数值词也可能是目标页；
                         # 真实跨报告对连 4 个位置配对都凑不齐，阈值放低无 FP 风险）
_MIN_PAIR_RATE = 0.9     # 页级配对率下限（同文档判据）
_MAX_DIFFS_PER_PAGE = 5  # 篡改是稀疏事件：单页超量 => 误判，整页放弃
_MAX_DIFFS_PER_JOB = 90  # 任务级熔断（实测 90 目标对查 0 误报，放宽以覆盖目标全集）
_REL_TOL = 1e-9          # 数值吻合容差（同文档对查，近零容差）

_NUM_TOKEN_RE = re.compile(r"\(?-?[\d,]+(?:\.\d+)?\)?%?")
_NUM_FULL_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclass
class _Word:
    value: float
    raw: str
    x0: float
    yc: float
    used: bool = False


def _numeric_words(page) -> list[_Word]:
    """提取一页中的数值词（带坐标）。get_text('words') 对 redaction 重写的文本
    依然给出真实坐标 —— 这正是本模块赖以工作的物理基础。"""
    words: list[_Word] = []
    try:
        raw_words = page.get_text("words") or []
    except Exception:  # pragma: no cover - fitz 读取异常
        return words
    for w in raw_words:
        text = (w[4] or "").strip()
        if not _NUM_FULL_RE.match(text):
            continue
        cleaned = text.replace(",", "").replace("(", "").replace(")", "").replace("%", "").lstrip("-")
        if _YEAR_RE.match(cleaned):
            continue
        if sum(ch.isdigit() for ch in cleaned) < _MIN_DIGITS:
            continue
        value = _parse_number(text)
        if value is None:
            continue
        words.append(_Word(value=value, raw=text, x0=float(w[0]), yc=(float(w[1]) + float(w[3])) / 2))
    return words


def _pair_words(a_words: list[_Word], h_words: list[_Word]) -> list[tuple[_Word, _Word]]:
    """按位置贪心配对（最近距离优先，每个 H 词只用一次）。"""
    candidates: list[tuple[float, int, int]] = []
    for i, a in enumerate(a_words):
        for j, h in enumerate(h_words):
            dx = abs(a.x0 - h.x0)
            dy = abs(a.yc - h.yc)
            if dx <= _PAIR_X_TOL and dy <= _PAIR_Y_TOL:
                candidates.append((dx + dy, i, j))
    candidates.sort()
    pairs: list[tuple[_Word, _Word]] = []
    used_h: set[int] = set()
    for _, i, j in candidates:
        if j in used_h or a_words[i].used:
            continue
        a_words[i].used = True
        used_h.add(j)
        pairs.append((a_words[i], h_words[j]))
    return pairs


def run_positional_value_twin_checks(
    doc_a: ReportDocument,
    doc_h: ReportDocument,
    *,
    max_diffs: int = _MAX_DIFFS_PER_JOB,
) -> list[Diff]:
    """同文档两版本的逐页位置孪生比对。真实跨报告对因页数/版式不同自然静默。"""
    if not doc_a.file_path or not doc_h.file_path:
        return []
    if doc_a.file_path == doc_h.file_path:
        return []  # self-check 同路径：无意义
    try:
        import fitz
    except ImportError:  # pragma: no cover
        logger.warning("PyMuPDF 不可用，位置孪生比对跳过")
        return []

    try:
        pdf_a = fitz.open(str(doc_a.file_path))
        pdf_h = fitz.open(str(doc_h.file_path))
    except Exception as exc:  # pragma: no cover
        logger.warning(f"位置孪生比对打开 PDF 失败: {exc}")
        return []
    try:
        if pdf_a.page_count != pdf_h.page_count:
            logger.info(
                f"位置孪生比对: 页数不同（{pdf_a.page_count} vs {pdf_h.page_count}），非同一文档，跳过"
            )
            return []

        diffs: list[Diff] = []
        pages_checked = 0
        pages_dropped = 0
        for page_idx in range(pdf_a.page_count):
            if len(diffs) >= max_diffs:
                break
            a_words = _numeric_words(pdf_a[page_idx])
            h_words = _numeric_words(pdf_h[page_idx])
            if not a_words or not h_words:
                continue
            pairs = _pair_words(a_words, h_words)
            if len(pairs) < _MIN_PAIRED:
                continue
            pair_rate = len(pairs) / max(len(a_words), len(h_words))
            if pair_rate < _MIN_PAIR_RATE:
                continue
            pages_checked += 1

            # 同位置取值冲突（剔除 overlay 辖区：同位置另一 A 词与 H 值相等）
            conflicts: list[tuple[_Word, _Word]] = []
            for a_w, h_w in pairs:
                if abs(a_w.value - h_w.value) / max(abs(a_w.value), abs(h_w.value), 1e-9) <= _REL_TOL:
                    continue
                # overlay 形态：A 侧同位置存在与 H 值相等的残留原值 → 交给 overlay 通道
                shadowed = any(
                    other is not a_w
                    and abs(other.x0 - a_w.x0) <= _PAIR_X_TOL
                    and abs(other.yc - a_w.yc) <= _PAIR_Y_TOL
                    and abs(other.value - h_w.value) / max(abs(other.value), abs(h_w.value), 1e-9) <= _REL_TOL
                    for other in a_words
                )
                if shadowed:
                    continue
                conflicts.append((a_w, h_w))

            # swap 镜像合并：p 位 X→Y 与 q 位 Y→X 是同一处互换的两端，只报一条
            merged: list[tuple[_Word, _Word]] = []
            consumed: set[int] = set()
            for i, (a1, h1) in enumerate(conflicts):
                if i in consumed:
                    continue
                mirror = next(
                    (
                        j
                        for j, (a2, h2) in enumerate(conflicts)
                        if j > i
                        and j not in consumed
                        and abs(a2.value - h1.value) / max(abs(a2.value), abs(h1.value), 1e-9) <= _REL_TOL
                        and abs(h2.value - a1.value) / max(abs(h2.value), abs(a1.value), 1e-9) <= _REL_TOL
                    ),
                    None,
                )
                if mirror is not None:
                    consumed.add(mirror)
                merged.append((a1, h1))

            if len(merged) > _MAX_DIFFS_PER_PAGE:
                pages_dropped += 1
                continue
            page_no = page_idx + 1
            for a_w, h_w in merged:
                diffs.append(_make_diff(page_no, a_w, h_w))

        if pages_dropped:
            logger.info(f"位置孪生比对：{pages_dropped} 页因超量放弃（疑似误判）")
        logger.info(f"位置孪生比对: 同版式页={pages_checked} 差异={len(diffs)}")
        return diffs[:max_diffs]
    finally:
        pdf_a.close()
        pdf_h.close()


def _make_diff(page: int, a_w: _Word, h_w: _Word) -> Diff:
    delta = abs(a_w.value - h_w.value)
    rel = delta / max(abs(a_w.value), abs(h_w.value), 1e-9)
    severity = DiffSeverity.HIGH if rel >= 0.05 else DiffSeverity.MEDIUM
    label = f"位置({a_w.x0:.0f},{a_w.yc:.0f})"
    evidence = [
        Evidence(
            side=ReportSide.A_SHARE,
            page=page,
            bbox=(a_w.x0, a_w.yc, a_w.x0, a_w.yc),
            snippet=a_w.raw,
            section="位置孪生",
        ),
        Evidence(
            side=ReportSide.H_SHARE,
            page=page,
            bbox=(h_w.x0, h_w.yc, h_w.x0, h_w.yc),
            snippet=h_w.raw,
            section="位置孪生",
        ),
    ]
    summary_zh = (
        f"第{page}页同一位置取值不一致：A 侧为 {a_w.raw}，对照侧为 {h_w.raw}，"
        f"相对差异 {rel*100:.2f}%"
    )
    return Diff(
        diff_id=f"POSTWIN_{uuid.uuid4().hex[:8]}",
        diff_type=DiffType.NUMERIC,
        diff_scope=DiffScope.CROSS_REPORT,
        severity=severity,
        triage="real",
        canonical_key=None,
        topic=LocalizedString(zh=f"第{page}页同位数值", en=f"P{page} positional value"),
        summary=LocalizedString(zh=summary_zh, en=summary_zh),
        a_value=a_w.value,
        h_value=h_w.value,
        delta=delta,
        tolerance=_REL_TOL,
        evidence=evidence,
        rule_id=RULE_ID,
        diff_explanation=make_value_explanation(
            headline=f"第{page}页同位置数值冲突",
            label=label,
            role="positional_value_conflict",
            a_value=a_w.value,
            h_value=h_w.value,
            delta=delta,
            evidence=evidence,
            review_hint="同一页同一版面位置的取值不同，疑似被编辑/互换，需对照原始底稿核对。",
        ),
    )
