"""注入式召回评估：在干净 PDF 上程序化植入已知错误，用注入清单当答案测召回。

为什么需要这个
----------------
主办方样本的 45 处植入错误全部是**同一种**制作方式：把错误值以新文本对象叠加在
原值正上方，原值文本对象仍留在文本层。`ahcc/check/text_overlay_tamper.py` 正是
针对这个制作痕迹写的，因此拿到 45/45 —— 但这个分数**不代表任何泛化能力**：
换一种造错方式（直接改文本层、表格换位），该检查器完全失效。

本模块提供三种互不相同的注入方式，其中只有 `overlay` 能被 text_overlay_tamper
检出，另外两种必须靠跨报告比对 / 勾稽校验 / 内部一致性才能发现：

| 方式      | 原值是否留在文本层 | text_overlay_tamper 能否检出 |
|-----------|--------------------|------------------------------|
| `overlay` | 是（被覆盖）        | 能 —— 复刻主办方做法          |
| `edit`    | 否（被物理删除）    | **不能**                      |
| `swap`    | 否（两值互换位置）  | **不能**                      |

用法::

    from ahcc.eval.inject import inject_errors
    records = inject_errors("clean.pdf", "tampered.pdf", count=100, seed=7)
    # records 即答案清单，可直接转成 ExpectedDiff 送进 evaluate()
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Literal

from loguru import logger

InjectionMethod = Literal["overlay", "edit", "swap", "period_swap"]

# 只在这些页上注入：含财务关键词的页，避免把封面/目录/纯叙述页当成目标。
_FINANCIAL_PAGE_MARKERS = (
    "营业收入", "资产总计", "负债合计", "净利润", "利润总额", "现金流量",
    "资产负债表", "利润表", "所有者权益", "股东权益", "合计", "附注",
    "Total assets", "Total liabilities", "Revenue", "Net profit", "Cash flow",
)

_NUM_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")

# 注入目标的最小有效位数。太短的数字（附注号、年份、页码）不适合当目标。
_MIN_DIGITS = 4


@dataclass
class InjectedError:
    """一处注入的已知错误 —— 即评估用的标准答案。"""

    seq: int
    page: int                 # 1-based
    method: InjectionMethod
    original_value: str       # 注入前的原始文本
    tampered_value: str       # 注入后读者看到的值
    row_label: str            # 该行左侧最近的文字标签，便于人读
    bbox: tuple[float, float, float, float]
    note: str = ""

    def to_row(self) -> dict:
        data = asdict(self)
        data["bbox"] = ",".join(f"{v:.1f}" for v in self.bbox)
        return data


def _digit_count(text: str) -> int:
    return sum(1 for ch in text if ch.isdigit())


def _is_year_like(text: str) -> bool:
    """年份不是合适的注入目标：改年份是"日期错误"而非"金额错误"，
    且换位到年份上会产生语义荒谬、任何检查器都不该报的噪声。"""
    stripped = text.replace(",", "").strip("()")
    if not stripped.isdigit() or len(stripped) != 4:
        return False
    return 1990 <= int(stripped) <= 2035


def _perturb_one_digit(text: str, rng: random.Random) -> str | None:
    """保长度的单数字替换 —— 与主办方植入错误同形态。

    刻意不改第一位数字：改首位会造成量级跳变，任何粗糙的检查都能发现，
    不能真实反映检测能力。
    """
    positions = [i for i, ch in enumerate(text) if ch.isdigit()]
    if len(positions) < 2:
        return None
    # 跳过第一个数字位
    candidates = positions[1:]
    idx = rng.choice(candidates)
    original_digit = text[idx]
    replacement = rng.choice([d for d in "0123456789" if d != original_digit])
    return text[:idx] + replacement + text[idx + 1:]


def _row_label(words: list, target_rect: tuple[float, float, float, float]) -> str:
    """取与目标同行、位于其左侧的最近一段文字标签。"""
    y0, y1 = target_rect[1], target_rect[3]
    line = []
    for w in words:
        overlap = min(y1, w[3]) - max(y0, w[1])
        if overlap <= 0:
            continue
        if overlap / max(min(y1 - y0, w[3] - w[1]), 1e-6) < 0.5:
            continue
        line.append(w)
    line.sort(key=lambda w: w[0])
    parts: list[str] = []
    for w in line:
        if w[0] >= target_rect[0]:
            break
        if _NUM_RE.match(w[4]):
            parts = []
            continue
        parts.append(w[4])
    return "".join(parts)[-40:]


def _page_is_financial(page_text: str) -> bool:
    """H 股年报正文常为繁体，必须先转简体再匹配关键词，否则整份报告一个目标都找不到。"""
    from ahcc.align.glossary import to_simplified

    normalized = to_simplified(page_text or "")
    return any(marker in normalized for marker in _FINANCIAL_PAGE_MARKERS)


def _span_font(page, rect) -> tuple[str, float]:
    """取目标位置的字号；字体统一用内置 helv（只需渲染数字）。"""
    try:
        raw = page.get_text("dict")
    except Exception:  # pragma: no cover
        return "helv", 8.0
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span.get("bbox")
                if not bbox:
                    continue
                if bbox[0] - 1 <= cx <= bbox[2] + 1 and bbox[1] - 1 <= cy <= bbox[3] + 1:
                    return "helv", float(span.get("size") or 8.0)
    # 兜底：用矩形高度估字号
    return "helv", max(float(rect[3] - rect[1]) * 0.85, 5.0)


def _collect_targets(doc, rng: random.Random, max_per_page: int = 3) -> list[tuple[int, tuple, str, list]]:
    """扫描全文，收集可注入的数字目标。返回 [(page_idx, rect, text, page_words), ...]"""
    targets: list[tuple[int, tuple, str, list]] = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        try:
            page_text = page.get_text("text") or ""
            words = page.get_text("words")
        except Exception:  # pragma: no cover
            continue
        if not _page_is_financial(page_text):
            continue
        numeric = [
            (tuple(w[:4]), w[4])
            for w in words
            if _NUM_RE.match(w[4]) and _digit_count(w[4]) >= _MIN_DIGITS and not _is_year_like(w[4])
        ]
        if not numeric:
            continue
        rng.shuffle(numeric)
        for rect, text in numeric[:max_per_page]:
            targets.append((page_idx, rect, text, words))
    return targets


def _apply_overlay(page, rect, original: str, tampered: str, fontsize: float) -> None:
    """复刻主办方做法：白底 + 新文本叠加在原值上，**原值文本对象保留在文本层**。"""
    import fitz

    r = fitz.Rect(*rect)
    page.draw_rect(r, color=None, fill=(1, 1, 1), overlay=True)
    # 基线略高于矩形底边，与原值视觉对齐
    page.insert_text(
        fitz.Point(r.x0, r.y1 - fontsize * 0.18),
        tampered,
        fontname="helv",
        fontsize=fontsize,
        color=(0, 0, 0),
        overlay=True,
    )


def _queue_redaction(page, rect, tampered: str, fontsize: float) -> None:
    """把原值**物理删除**并写入新值 —— 文本层不再残留原值。"""
    import fitz

    page.add_redact_annot(
        fitz.Rect(*rect),
        text=tampered,
        fontname="helv",
        fontsize=fontsize,
        align=fitz.TEXT_ALIGN_LEFT,
        fill=(1, 1, 1),
        text_color=(0, 0, 0),
    )


def inject_errors(
    src_pdf: str | Path,
    out_pdf: str | Path,
    *,
    count: int = 100,
    methods: Iterable[InjectionMethod] = ("overlay", "edit", "swap"),
    seed: int = 0,
    max_per_page: int = 3,
) -> list[InjectedError]:
    """在 `src_pdf` 上注入 `count` 处已知错误，写出 `out_pdf`，返回答案清单。

    注入方式在目标上均匀轮转，保证每种方式都有足够样本量分别统计召回率。
    period_swap 与 swap 的区别：伙伴必须在不同列（x 中心距离 ≥30pt，即不同
    期间列，表头不动）—— 检出依赖列键期间硬门槛与 twin 语义 role，而不是
    位置巧合。
    """
    import fitz

    method_list = [m for m in methods]
    if not method_list:
        raise ValueError("methods 不能为空")

    rng = random.Random(seed)
    doc = fitz.open(str(src_pdf))
    try:
        targets = _collect_targets(doc, rng, max_per_page=max_per_page)
        rng.shuffle(targets)

        records: list[InjectedError] = []
        # swap 需要同行另一个数字，按页分组处理；overlay/edit 逐点处理。
        pending_redactions: dict[int, bool] = {}

        for page_idx, rect, text, words in targets:
            if len(records) >= count:
                break
            method = method_list[len(records) % len(method_list)]
            page = doc[page_idx]
            _, fontsize = _span_font(page, rect)
            label = _row_label(words, rect)

            if method in ("swap", "period_swap"):
                partner = _find_row_partner(
                    words, rect, text, min_column_distance=30.0 if method == "period_swap" else 0.0
                )
                if partner is None:
                    continue
                p_rect, p_text = partner
                _queue_redaction(page, rect, p_text, fontsize)
                _queue_redaction(page, p_rect, text, fontsize)
                pending_redactions[page_idx] = True
                records.append(
                    InjectedError(
                        seq=len(records) + 1,
                        page=page_idx + 1,
                        method=method,
                        original_value=text,
                        tampered_value=p_text,
                        row_label=label,
                        bbox=tuple(float(v) for v in rect),
                        note=(
                            f"与同行不同期间列的 {p_text} 互换（表头不动）"
                            if method == "period_swap"
                            else f"与同行 {p_text} 互换位置"
                        ),
                    )
                )
                continue

            tampered = _perturb_one_digit(text, rng)
            if tampered is None or tampered == text:
                continue

            if method == "overlay":
                _apply_overlay(page, rect, text, tampered, fontsize)
            else:  # edit
                _queue_redaction(page, rect, tampered, fontsize)
                pending_redactions[page_idx] = True

            records.append(
                InjectedError(
                    seq=len(records) + 1,
                    page=page_idx + 1,
                    method=method,
                    original_value=text,
                    tampered_value=tampered,
                    row_label=label,
                    bbox=tuple(float(v) for v in rect),
                    note="原值保留在文本层" if method == "overlay" else "原值已从文本层删除",
                )
            )

        for page_idx in pending_redactions:
            doc[page_idx].apply_redactions()

        Path(out_pdf).parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out_pdf), garbage=0, deflate=True)
    finally:
        doc.close()

    by_method: dict[str, int] = {}
    for r in records:
        by_method[r.method] = by_method.get(r.method, 0) + 1
    logger.info(f"注入完成：{len(records)} 处 → {out_pdf}  分布={by_method}")
    return records


def _find_row_partner(
    words: list,
    rect,
    text: str,
    *,
    min_column_distance: float = 0.0,
) -> tuple[tuple, str] | None:
    """在同一行里找另一个数字词作为换位伙伴（值必须不同，否则换了等于没换）。

    min_column_distance > 0 时要求伙伴与目标分属不同列（x 中心距离门槛）——
    period_swap 用它保证互换发生在两个期间列之间（表头不动）。
    """
    y0, y1 = rect[1], rect[3]
    target_xc = (rect[0] + rect[2]) / 2
    for w in words:
        w_rect = tuple(w[:4])
        if w_rect == tuple(rect):
            continue
        overlap = min(y1, w[3]) - max(y0, w[1])
        if overlap <= 0:
            continue
        if overlap / max(min(y1 - y0, w[3] - w[1]), 1e-6) < 0.6:
            continue
        if not _NUM_RE.match(w[4]) or _digit_count(w[4]) < _MIN_DIGITS:
            continue
        if _is_year_like(w[4]):
            continue
        if w[4] == text:
            continue
        if min_column_distance > 0:
            partner_xc = (w[0] + w[2]) / 2
            if abs(partner_xc - target_xc) < min_column_distance:
                continue
        return w_rect, w[4]
    return None


def records_to_expected(records: list[InjectedError]):
    """把注入清单转成 evaluate() 需要的 ExpectedDiff 列表。"""
    from ahcc.eval.matcher import ExpectedDiff

    return [
        ExpectedDiff(
            topic=f"P{r.page} {r.row_label}".strip(),
            page=r.page,
            a_page=r.page,
            original_value=r.original_value,
            tampered_value=r.tampered_value,
            description=f"[{r.method}] {r.row_label}",
            note=r.note,
        )
        for r in records
    ]


def export_injection_manifest(records: list[InjectedError], out_path: str | Path) -> None:
    """把注入清单导出为 Excel，格式与主办方错误清单一致，可直接当答案文件用。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "注入清单"
    ws.append(["序号", "PDF页码", "描述", "原始数字", "错误数字", "注入方式", "变动说明"])
    for r in records:
        ws.append([r.seq, r.page, f"[{r.method}] {r.row_label}", r.original_value,
                   r.tampered_value, r.method, r.note])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))
