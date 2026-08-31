"""图表数据的文本层抽取 — 替代 VLM 视觉路线（deepseek-v4-pro 不支持图像输入，实测 HTTP 400）。

原理
----
年报中的图表（饼图/柱图/折线图）在这类 PDF 里是**矢量图形**：数据标签、坐标轴数值、
图例都是真实文本对象，可以用 PyMuPDF 在图表 bbox 内直接读出，比 VLM 更确定、可审计：

    page.get_text("words", clip=chart.bbox)

抽取策略
--------
1. bbox 内所有词按视觉行聚类（y 重叠）；
2. 数值词（含 %、千分位）与标签词（中文/英文）分开；
3. 每个数值找它的标签：优先**同一视觉行左侧**（饼图/条形图标签-数值并排），
   其次**正下方标签带**（柱状图类别轴在柱底），再其次正上方；
4. 最上方的纯标签行作为图表标题候选；≥30% 数值带 % 则单位记为 "%"。

局限（如实上报，不静默）：纯位图扫描件的图表没有文本层，本模块返回 {}，
由调用方记入 module_warnings —— 「检测到图表但无文本层数据」与「核对无异常」
是两种不同的结果，必须让用户能区分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz  # PyMuPDF
from loguru import logger

from ahcc.profile.extract_metrics import _parse_number
from ahcc.schemas import ChartRegion

_MAX_DATA_POINTS = 50
_MIN_DATA_POINTS = 2
_LABEL_BAND_TOLERANCE = 6.0   # 同一视觉行的 y 容差（pt）
_AXIS_LABEL_BAND_BELOW = 250.0  # 柱顶数值到柱底类别轴的允许垂直距离（pt）
_AXIS_LABEL_BAND_ABOVE = 40.0   # 数值正上方图例标签的允许距离（pt）
_AXIS_LABEL_MAX_DX = 60.0       # 数值与类别标签的允许水平中心距（pt）

_NUM_TOKEN_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$")
_LABEL_TOKEN_RE = re.compile(r"[一-鿿]|[A-Za-z]{2,}")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


def _is_number_word(text: str) -> bool:
    return bool(_NUM_TOKEN_RE.match(text.strip()))


def _is_label_word(text: str) -> bool:
    return bool(_LABEL_TOKEN_RE.search(text)) and not _is_number_word(text)


def _parse_chart_number(text: str) -> float | None:
    """图表数值解析：允许 % 后缀；剔除纯年份（坐标轴刻度）。"""
    cleaned = text.strip().rstrip("%")
    if _YEAR_RE.match(cleaned.replace(",", "")):
        return None
    return _parse_number(cleaned)


def _cluster_lines(words: list[_Word]) -> list[list[_Word]]:
    """按 y 中心聚类成视觉行（同 block/line 优先，y 重叠兜底）。"""
    lines: list[list[_Word]] = []
    for w in sorted(words, key=lambda w: (w.y0, w.x0)):
        placed = False
        for line in lines:
            if abs(line[0].yc - w.yc) <= _LABEL_BAND_TOLERANCE:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w.x0)
    return lines


_CJK_RE = re.compile(r"[一-鿿]")


def _line_text(line: list[_Word]) -> str:
    """拼行文本：CJK 之间不加空格；拉丁词之间按水平间隙补空格。"""
    parts: list[str] = []
    prev: _Word | None = None
    for w in line:
        if prev is not None:
            gap = w.x0 - prev.x1
            if gap > 2.0 and not _CJK_RE.search(prev.text[-1:] + w.text[:1]):
                parts.append(" ")
        parts.append(w.text)
        prev = w
    return "".join(parts)


def _line_is_label(line: list[_Word]) -> bool:
    text = _line_text(line)
    return bool(_LABEL_TOKEN_RE.search(text)) and not any(_is_number_word(w.text) for w in line)


def _nearest_label(number: _Word, label_lines: list[list[_Word]], same_line: list[_Word]) -> str | None:
    """给数值词找标签：同一行左侧 > 正下方标签带 > 正上方标签带。"""
    # 1) 同一视觉行左侧的标签词
    left_labels = [w for w in same_line if w.x1 <= number.x0 + 1 and _is_label_word(w.text)]
    if left_labels:
        return "".join(w.text for w in left_labels).strip(":：%") or None

    # 2) 正下方类别轴（柱状图标签在柱底，距离可达柱高）
    # 3) 正上方图例：距离收紧
    # 注意按「词组」而非「整行」匹配：类别轴上多个标签是同一视觉行里的独立词，
    # 行中心距会让两侧数值全部失配；先定位最近的词，再向左右吸纳紧邻词（≤12pt 间隙）
    # 拼出多词标签（如 "Total Revenue"）。
    best: tuple[float, str] | None = None
    for line in label_lines:
        ly = sum(w.yc for w in line) / len(line)
        dy_below = ly - number.yc
        below_ok = 0 < dy_below <= _AXIS_LABEL_BAND_BELOW
        above_ok = -_AXIS_LABEL_BAND_ABOVE <= dy_below < 0
        if not (below_ok or above_ok):
            continue
        # 行内找与数值水平中心最近的标签词
        nearest = min(line, key=lambda w: abs(w.xc - number.xc))
        dx = abs(nearest.xc - number.xc)
        if dx > _AXIS_LABEL_MAX_DX:
            continue
        # 向左右扩展紧邻词，拼出完整多词标签
        group = [nearest]
        for w in sorted(line, key=lambda w: w.x0):
            if w is nearest:
                continue
            left_edge = min(g.x0 for g in group)
            right_edge = max(g.x1 for g in group)
            if w.x1 <= left_edge and 0 <= left_edge - w.x1 <= 12.0:
                group.append(w)
            elif w.x0 >= right_edge and 0 <= w.x0 - right_edge <= 12.0:
                group.append(w)
        text = "".join(w.text for w in sorted(group, key=lambda w: w.x0))
        score = (dy_below + 0.5 * dx) if below_ok else (abs(dy_below) * 2.0 + dx)
        if best is None or score < best[0]:
            best = (score, text)
    if best:
        return best[1].strip(":：%") or None
    return None


def extract_chart_textlayer_data(page: "fitz.Page", chart: ChartRegion) -> dict:
    """从图表 bbox 的文本层抽取结构化数据。

    Args:
        page: 已打开的 fitz 页面对象（调用方负责缓存 fitz.Document）
        chart: 图表区域（bbox 为 PDF 用户坐标）

    Returns:
        {"title": str, "unit": str, "chart_type": "unknown", "data_points": [{"label","value"}]}
        数据点不足（纯位图/无标签）时返回 {}。
    """
    clip = fitz.Rect(*chart.bbox) if chart.bbox else None
    if clip is None or clip.is_empty:
        return {}
    try:
        raw_words = page.get_text("words", clip=clip)
    except Exception as exc:
        logger.warning(f"图表文本层读取失败 {chart.chart_id}: {exc}")
        return {}
    if not raw_words:
        return {}

    words = [_Word(x0=float(w[0]), y0=float(w[1]), x1=float(w[2]), y1=float(w[3]), text=str(w[4])) for w in raw_words]
    lines = _cluster_lines(words)
    label_lines = [ln for ln in lines if _line_is_label(ln)]

    # 标题候选：bbox 内最上方的纯标签行
    title = ""
    if label_lines:
        top = min(label_lines, key=lambda ln: min(w.y0 for w in ln))
        title = _line_text(top)[:80]

    data_points: list[dict] = []
    seen: set[tuple[str, float]] = set()
    percent_marks = 0
    number_count = 0

    for line in lines:
        for w in line:
            if not _is_number_word(w.text):
                continue
            value = _parse_chart_number(w.text)
            if value is None:
                continue
            number_count += 1
            if w.text.strip().endswith("%"):
                percent_marks += 1
            label = _nearest_label(w, label_lines, line)
            if not label:
                continue
            label = label[:40]
            key = (label, value)
            if key in seen:
                continue
            seen.add(key)
            data_points.append({"label": label, "value": value})
            if len(data_points) >= _MAX_DATA_POINTS:
                break

    if len(data_points) < _MIN_DATA_POINTS:
        return {}

    unit = "%" if number_count and percent_marks / number_count >= 0.3 else ""
    return {
        "title": title,
        "unit": unit,
        "chart_type": "unknown",
        "data_points": data_points,
    }
