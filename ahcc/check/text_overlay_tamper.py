"""文本层叠加篡改检测 — 检出"错误值以新文本对象覆盖在原值正上方"的植入式篡改。

背景：主办方样本（含错误测试版 PDF）的全部植入错误均为此形态——篡改值叠加在
原值矩形上（偏移 3~17pt），原值文本对象仍留在文本层。视觉上读者只看到篡改值，
但 `page.get_text()` 会同时返回两个值。

检测方法（纯 PyMuPDF，无 OCR，350 页约 6~8 秒）：
1. 逐页取 words，筛出数字词（规范化后 ≥3 位，支持千分位逗号/括号负数/百分号/小数）；
2. 两两配对：矩形相交且 y/x 方向重叠率过阈值、规范化后长度相同且逐位替换数 ≤2；
3. 用内容流顺序（get_text("dict", sort=False)）判定哪个 span 后写入 = 叠加层 = 可见值。

护栏取值依据（对 3 组样本 45 处错误 + 6 份干净 PDF 实测）：
- 真阳性 y 重叠率 0.91~1.00，误报（图表标签堆叠）最大 0.32 → MIN_Y_OVERLAP=0.60；
- 45 处篡改全部是保长度的逐位数字替换（"百位 3→4"式）→ 同长度 + 替换数 ≤2，
  不可放宽为通用编辑距离（变长会引入大量正常排版误报）；
- 不加"同字号"条件：真阳性两值字号本就不同，加了必漏检。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

from ahcc.schemas import (
    Diff,
    DiffScope,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportSide,
)

_NUM_RE = re.compile(r"^-?\(?[\d,]+(?:\.\d+)?\)?%?$")
MIN_NORMALIZED_DIGITS = 3   # 答案最短值 "7.00" → 规范化 "7.00"（3 位数字）
MAX_SUBSTITUTIONS = 2       # 同长度逐位替换数上限（实测植入错误均为 1，留 1 余量）
MIN_Y_OVERLAP = 0.60
MIN_X_OVERLAP = 0.50
_MAX_HITS_PER_PAGE = 50     # 单页命中软上限，防病态 PDF 刷屏


@dataclass
class OverlayHit:
    page: int                                   # 1-based
    visible_value: str                          # 叠加层（读者可见）原始文本
    hidden_value: str                           # 底层被覆盖的原始文本
    visible_rect: tuple[float, float, float, float]
    hidden_rect: tuple[float, float, float, float]
    order_confident: bool                       # 内容流顺序是否足以判定层次
    row_label: str = ""                         # 该行左侧最近的文字标签（如"营业收入"）
    line_text: str = ""                         # 上下文行文本（截断）


def _normalize_number(text: str) -> str:
    """去掉千分位逗号/括号/百分号/负号，保留数字与小数点，用于同长度逐位比较。"""
    return text.replace(",", "").replace("(", "").replace(")", "").replace("%", "").lstrip("-")


def _digit_count(normalized: str) -> int:
    return sum(1 for ch in normalized if ch.isdigit())


def _substitution_distance(a: str, b: str) -> int | None:
    """同长度逐位替换数；长度不同返回 None（不视为叠加篡改）。"""
    if len(a) != len(b):
        return None
    return sum(1 for x, y in zip(a, b) if x != y)


def _rect_overlap_ratios(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float]:
    """返回 (x 方向重叠率, y 方向重叠率)，均以较小边为基准。"""
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0, 0.0
    min_w = max(min(a[2] - a[0], b[2] - b[0]), 1e-6)
    min_h = max(min(a[3] - a[1], b[3] - b[1]), 1e-6)
    return ix / min_w, iy / min_h


def _parse_value(text: str) -> float | None:
    normalized = text.replace(",", "").replace("%", "").strip()
    negative = normalized.startswith("(") and normalized.endswith(")")
    normalized = normalized.strip("()")
    try:
        value = float(normalized)
    except ValueError:
        return None
    return -value if negative or text.lstrip().startswith("-") else value


def _span_stream_order(page, rect: tuple[float, float, float, float], text: str) -> int | None:
    """在内容流顺序的 span 序列里定位该词，返回顺序号；找不到返回 None。

    叠加篡改由编辑工具追加写入，内容流中必然晚于原值 span。
    """
    spans = getattr(page, "_ahcc_span_cache", None)
    if spans is None:
        spans = []
        try:
            raw = page.get_text("dict", sort=False)
        except Exception:  # pragma: no cover - fitz 解析异常
            raw = {"blocks": []}
        order = 0
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    spans.append((order, span.get("bbox"), span.get("text") or ""))
                    order += 1
        page._ahcc_span_cache = spans
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    best: int | None = None
    for order, bbox, span_text in spans:
        if not bbox or text not in span_text:
            continue
        if bbox[0] - 1 <= cx <= bbox[2] + 1 and bbox[1] - 1 <= cy <= bbox[3] + 1:
            best = order
    return best


def _line_context(
    words: list, pair_rects: tuple[tuple, tuple], numeric_rects: set[tuple]
) -> tuple[str, str]:
    """取与命中矩形同行的词：左侧最近的非数字词串作 row_label，整行拼接作 line_text。"""
    y0 = min(pair_rects[0][1], pair_rects[1][1])
    y1 = max(pair_rects[0][3], pair_rects[1][3])
    x_left = min(pair_rects[0][0], pair_rects[1][0])
    line_words = []
    for w in words:
        wy0, wy1 = w[1], w[3]
        overlap = min(y1, wy1) - max(y0, wy0)
        if overlap <= 0:
            continue
        if overlap / max(min(y1 - y0, wy1 - wy0), 1e-6) >= 0.5:
            line_words.append(w)
    line_words.sort(key=lambda w: w[0])
    label_parts: list[str] = []
    for w in line_words:
        if w[0] >= x_left:
            break
        if _NUM_RE.match(w[4]):
            label_parts = []  # 数字之后重新累积，取紧邻左侧的文字串
            continue
        label_parts.append(w[4])
    row_label = "".join(label_parts)[-30:]
    line_text = " ".join(w[4] for w in line_words)[:120]
    return row_label, line_text


def scan_pdf_overlays(file_path: str, *, max_pages: int | None = None) -> list[OverlayHit]:
    """扫描单份 PDF 的全部页面，返回叠加篡改命中列表。"""
    import fitz

    hits: list[OverlayHit] = []
    with fitz.open(file_path) as doc:
        total = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        for page_idx in range(total):
            page = doc[page_idx]
            try:
                words = page.get_text("words")
            except Exception:  # pragma: no cover - fitz 解析异常
                continue
            numeric_words = []
            for w in words:
                text = w[4]
                if not _NUM_RE.match(text):
                    continue
                normalized = _normalize_number(text)
                if _digit_count(normalized) < MIN_NORMALIZED_DIGITS:
                    continue
                numeric_words.append((tuple(w[:4]), text, normalized))

            page_hits: list[OverlayHit] = []
            seen_pairs: set[frozenset[str]] = set()
            for i in range(len(numeric_words)):
                for j in range(i + 1, len(numeric_words)):
                    rect_a, text_a, norm_a = numeric_words[i]
                    rect_b, text_b, norm_b = numeric_words[j]
                    if norm_a == norm_b:
                        continue
                    subs = _substitution_distance(norm_a, norm_b)
                    if subs is None or subs > MAX_SUBSTITUTIONS:
                        continue
                    x_ov, y_ov = _rect_overlap_ratios(rect_a, rect_b)
                    if y_ov < MIN_Y_OVERLAP or x_ov < MIN_X_OVERLAP:
                        continue
                    pair_key = frozenset((norm_a, norm_b))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    order_a = _span_stream_order(page, rect_a, text_a)
                    order_b = _span_stream_order(page, rect_b, text_b)
                    if order_a is not None and order_b is not None and order_a != order_b:
                        order_confident = True
                        if order_a > order_b:
                            visible, hidden = (rect_a, text_a), (rect_b, text_b)
                        else:
                            visible, hidden = (rect_b, text_b), (rect_a, text_a)
                    else:
                        order_confident = False
                        visible, hidden = (rect_a, text_a), (rect_b, text_b)

                    hit = OverlayHit(
                        page=page_idx + 1,
                        visible_value=visible[1],
                        hidden_value=hidden[1],
                        visible_rect=visible[0],
                        hidden_rect=hidden[0],
                        order_confident=order_confident,
                    )
                    hit.row_label, hit.line_text = _line_context(
                        words, (visible[0], hidden[0]), set()
                    )
                    page_hits.append(hit)
            if len(page_hits) > _MAX_HITS_PER_PAGE:
                logger.warning(
                    f"text overlay scan: page {page_idx + 1} yielded {len(page_hits)} hits "
                    f"(> {_MAX_HITS_PER_PAGE}), possible pathological layout"
                )
            hits.extend(page_hits)
    return hits


def _hit_to_diff(hit: OverlayHit, side: ReportSide, seq: int) -> Diff:
    side_label = "A" if side == ReportSide.A_SHARE else "H"
    label_part = f"「{hit.row_label}」" if hit.row_label else ""
    if hit.order_confident:
        summary_zh = (
            f"{side_label}股第{hit.page}页{label_part}文本层叠加异常："
            f"可见值 {hit.visible_value} 覆盖原值 {hit.hidden_value}，疑似篡改"
        )
        summary_en = (
            f"{side_label}-share page {hit.page} {hit.row_label}: overlaid text value "
            f"{hit.visible_value} covers original {hit.hidden_value}; suspected tampering"
        )
    else:
        summary_zh = (
            f"{side_label}股第{hit.page}页{label_part}同一位置重叠出现两个不同数值 "
            f"{hit.visible_value} 与 {hit.hidden_value}，疑似文本层篡改"
        )
        summary_en = (
            f"{side_label}-share page {hit.page} {hit.row_label}: two different values "
            f"{hit.visible_value} / {hit.hidden_value} overlap at the same position"
        )
    snippet_core = f"{hit.row_label} 可见:{hit.visible_value} 底层:{hit.hidden_value}".strip()
    context = f" | {hit.line_text}" if hit.line_text else ""
    return Diff(
        diff_id=f"OVERLAY_{side_label}_{hit.page}_{seq}",
        diff_type=DiffType.INTERNAL,
        diff_scope=DiffScope.A_INTERNAL if side == ReportSide.A_SHARE else DiffScope.H_INTERNAL,
        severity=DiffSeverity.HIGH,
        triage="real",
        topic=LocalizedString(
            zh=f"{side_label}股文本层叠加篡改", en=f"{side_label}-share text-layer overlay"
        ),
        summary=LocalizedString(zh=summary_zh, en=summary_en),
        a_value=_parse_value(hit.visible_value),
        h_value=_parse_value(hit.hidden_value),
        tolerance=0.0,
        evidence=[
            Evidence(
                side=side,
                page=hit.page,
                bbox=hit.visible_rect,
                snippet=(snippet_core + context)[:200],
                section="文本层叠加检测",
            ),
            Evidence(
                side=side,
                page=hit.page,
                bbox=hit.hidden_rect,
                snippet=f"底层原值 {hit.hidden_value}（被 {hit.visible_value} 覆盖）"[:200],
                section="文本层叠加检测",
            ),
        ],
        rule_id="text_overlay_tamper",
    )


def scan_side(file_path: str, side: ReportSide) -> list[Diff]:
    """扫描单侧 PDF 并转换为 Diff 列表。非 PDF 文件（如 H 股 HTML）直接跳过。"""
    if not str(file_path).lower().endswith(".pdf"):
        return []
    try:
        hits = scan_pdf_overlays(file_path)
    except Exception as exc:  # noqa: BLE001 - 单侧失败不拖垮另一侧
        logger.warning(f"text overlay scan failed for {file_path}: {exc}")
        return []
    return [_hit_to_diff(hit, side, seq + 1) for seq, hit in enumerate(hits)]


def run_text_overlay_checks(a_file: str, h_file: str) -> list[Diff]:
    """A/H 两侧各扫一遍。同步函数，编排层用 asyncio.to_thread 调用。"""
    diffs = [
        *scan_side(a_file, ReportSide.A_SHARE),
        *scan_side(h_file, ReportSide.H_SHARE),
    ]
    if diffs:
        logger.info(f"text overlay tamper check: {len(diffs)} hit(s)")
    return diffs
