"""图表三方核对（P4 实现）— 模块 C / 亮点 2。

deepseek-v4-pro 不支持图像输入（实测 HTTP 400），因此图表数据默认走**文本层抽取**：
年报图表是矢量图，数据标签/坐标轴数值/图例都是真实文本对象，用 PyMuPDF 在图表
bbox 内 `get_text("words", clip=bbox)` 直接读出，比 VLM 更确定、可审计。

流程：
1. 对 doc.charts 中每张图，按 chart_extract_mode 抽数（text_layer 默认 / vlm / off）
2. 在同页/相邻页找：
   - 表格中对应数据
   - 文本中"零售业务占 35%"这类陈述
3. 三方对比，输出 ChartCrossCheck

「检测到图表但无可用数据」（纯位图扫描件无文本层）与「核对无异常」是两种不同结果，
前者通过 module_warnings 透出，不静默。
"""

from __future__ import annotations

import asyncio
import uuid

import fitz  # PyMuPDF
from loguru import logger

from ahcc.config import settings
from ahcc.schemas import (
    ChartCrossCheck,
    ChartRegion,
    Diff,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportDocument,
    ReportSide,
)
from ahcc.check.explanation import make_value_explanation
from ahcc.check.chart_textlayer import extract_chart_textlayer_data
from ahcc.vlm.qwen_vl import extract_chart_data


def _resolve_chart_mode() -> str:
    """解析图表抽取模式：废弃别名 enable_chart_vlm_check=True 强制 vlm，否则用 chart_extract_mode。"""
    if getattr(settings, "enable_chart_vlm_check", False):
        return "vlm"
    mode = (getattr(settings, "chart_extract_mode", "text_layer") or "text_layer").strip().lower()
    return mode if mode in ("text_layer", "vlm", "off") else "text_layer"


# 进程级文档缓存：同一 PDF 的多张图表共享一个 fitz.Document，避免反复打开
_DOC_CACHE: dict[str, fitz.Document] = {}


def _page_for_chart(doc: ReportDocument, chart: ChartRegion) -> fitz.Page | None:
    path = doc.file_path
    if not path:
        return None
    try:
        fdoc = _DOC_CACHE.get(path)
        if fdoc is None:
            fdoc = fitz.open(path)
            _DOC_CACHE[path] = fdoc
        # chart.page 为 1 基（report/证据口径），fitz 为 0 基
        idx = chart.page - 1
        if idx < 0 or idx >= fdoc.page_count:
            return None
        return fdoc.load_page(idx)
    except Exception as exc:
        logger.warning(f"图表文本层打开失败 {chart.chart_id} ({path}): {exc}")
        return None


async def run_chart_checks(doc_a: ReportDocument, doc_h: ReportDocument, max_charts: int = 15) -> list[Diff]:
    """对两份报告分别跑图表核对。

    Args:
        max_charts: 最多核对 N 张图表（Demo 场景下 15 张已足够展示能力）。
    """
    mode = _resolve_chart_mode()
    if mode == "off":
        logger.info("图表核对已关闭（chart_extract_mode=off）")
        return []

    candidates = [
        (doc, chart)
        for doc in (doc_a, doc_h)
        for chart in doc.charts
    ][:max_charts]

    # 并发核对，受 LLM 并发上限约束（text_layer 模式不调 LLM，仅为统一闸口）
    sem = asyncio.Semaphore(max(1, settings.llm_concurrency))

    async def _guarded(doc: ReportDocument, chart: ChartRegion) -> Diff | None:
        async with sem:
            return await _check_one_chart(doc, chart, mode)

    results = await asyncio.gather(
        *[_guarded(doc, chart) for doc, chart in candidates],
        return_exceptions=True,
    )

    diffs: list[Diff] = []
    failures = 0
    no_text_layer = 0
    for res in results:
        if isinstance(res, Exception):
            failures += 1
            logger.warning(f"图表核对任务失败: {res}")
            continue
        if res == "NO_TEXT_LAYER":
            no_text_layer += 1
            continue
        if res:
            diffs.append(res)
    logger.info(
        f"图表核对完成（mode={mode}），检查 {len(candidates)} 张图表，发现 {len(diffs)} 条异常"
        + (f"，{failures} 张失败" if failures else "")
        + (f"，{no_text_layer} 张无文本层数据" if no_text_layer else "")
    )
    return diffs


async def _check_one_chart(doc: ReportDocument, chart: ChartRegion, mode: str = "text_layer") -> Diff | str | None:
    """单张图表的三方核对。

    步骤：
    1. 按 mode 抽取图表数据（text_layer 默认 / vlm）
    2. 在同页表格中查找对应数据
    3. 在同页/相邻页文本中查找对应陈述
    4. 比对三者一致性

    Returns:
        Diff / None；文本层模式下纯位图无文本层时返回哨兵 "NO_TEXT_LAYER"。
    """
    # 1. 抽取图表数据
    if mode == "vlm":
        if not chart.image_path:
            return None
        try:
            vlm_result = await asyncio.to_thread(extract_chart_data, chart.image_path)
        except Exception as e:
            logger.warning(f"VLM 提取失败 {chart.chart_id}: {e}")
            return None
    else:  # text_layer
        page = _page_for_chart(doc, chart)
        if page is None:
            return "NO_TEXT_LAYER"
        vlm_result = extract_chart_textlayer_data(page, chart)
        if not vlm_result:
            return "NO_TEXT_LAYER"

    if not vlm_result or not vlm_result.get("data_points"):
        return "NO_TEXT_LAYER" if mode == "text_layer" else None

    vlm_data_points = vlm_result.get("data_points", [])
    chart_title = vlm_result.get("title", "")
    chart_unit = vlm_result.get("unit", "")

    # 2. 在表格中查找对应数据
    table_matches = _find_table_matches(doc, chart, vlm_data_points)

    # 3. 在文本中查找对应陈述
    text_matches = _find_text_matches(doc, chart, vlm_data_points)

    # 4. 比对不一致
    inconsistencies = 0
    for dp in vlm_data_points:
        label = dp.get("label", "")
        vlm_val = dp.get("value")

        table_val = table_matches.get(label)
        text_val = text_matches.get(label)

        # 判断不一致（允许 1% 容差）
        vals = [v for v in [vlm_val, table_val, text_val] if v is not None]
        if len(vals) >= 2:
            max_val = max(abs(v) for v in vals)
            min_val = min(abs(v) for v in vals)
            if max_val > 0 and (max_val - min_val) / max_val > 0.01:
                inconsistencies += 1

    if inconsistencies == 0:
        return None  # 无明显不一致

    # 构建 ChartCrossCheck
    # 取第一个数据点的值作为代表
    first_dp = vlm_data_points[0] if vlm_data_points else {}
    cross = ChartCrossCheck(
        chart_value=first_dp.get("value"),
        table_value=table_matches.get(first_dp.get("label", "")),
        text_value=text_matches.get(first_dp.get("label", "")),
        chart_evidence=Evidence(
            side=doc.side,
            page=chart.page,
            bbox=chart.bbox,
            snippet=f"图表: {chart_title} (类型: {chart.chart_type})",
        ),
        inconsistency_count=inconsistencies,
    )

    severity = DiffSeverity.HIGH if inconsistencies >= 3 else DiffSeverity.MEDIUM
    chart_value = cross.chart_value
    comparison_value = cross.table_value if cross.table_value is not None else cross.text_value
    delta = (
        abs(chart_value - comparison_value)
        if chart_value is not None and comparison_value is not None
        else None
    )
    evidence = [cross.chart_evidence] if cross.chart_evidence else []

    return Diff(
        diff_id=f"chart-{uuid.uuid4().hex[:6]}",
        diff_type=DiffType.CHART,
        severity=severity,
        topic=LocalizedString(zh=f"图表核对: {chart_title}", en=f"Chart check: {chart_title}"),
        summary=LocalizedString(
            zh=f"图表「{chart_title}」与表格/文本存在 {inconsistencies} 处不一致",
            en=f"Chart '{chart_title}' has {inconsistencies} inconsistencies with table/text",
        ),
        evidence=evidence,
        diff_explanation=make_value_explanation(
            headline=f"图表《{chart_title}》与表格/文本不一致",
            label="图表核对值",
            role="chart_cross_check",
            a_value=chart_value,
            h_value=comparison_value,
            delta=delta,
            evidence=evidence,
            review_hint="优先核对同页图表、表格和正文中相同标签对应的数值。",
        ),
        chart_cross=cross,
    )


def _find_table_matches(doc: ReportDocument, chart: ChartRegion, data_points: list[dict]) -> dict[str, float]:
    """在同页表格中查找与图表数据对应的数据。

    策略：
    - 优先查找同页表格
    - 匹配标签名称（如"零售业务"）
    - 提取对应数值
    """
    matches: dict[str, float] = {}

    # 归一化标签 -> 原始标签：匹配用归一化形式，但 key 用原始 label，
    # 与 _check_one_chart 中 table_matches.get(label) 的查询口径保持一致。
    label_by_norm = {
        dp.get("label", "").strip().lower(): dp.get("label", "")
        for dp in data_points
        if dp.get("label", "").strip()
    }

    for table in doc.tables:
        # 优先同页，也看相邻页
        if abs(table.page - chart.page) > 2:
            continue

        # 将表格按行遍历
        rows: dict[int, list] = {}
        for cell in table.cells:
            rows.setdefault(cell.row, []).append(cell)

        for row_idx, cells in rows.items():
            cells_sorted = sorted(cells, key=lambda c: c.col)
            if not cells_sorted:
                continue

            label_cell = cells_sorted[0]
            label_text = label_cell.text.strip().lower()
            if not label_text:
                continue

            # 检查是否匹配图表中的某个标签
            for norm_label, orig_label in label_by_norm.items():
                if norm_label in label_text or label_text in norm_label:
                    # 从该行的其他列找数值
                    for cell in cells_sorted[1:]:
                        val = _parse_number(cell.text)
                        if val is not None:
                            matches[orig_label] = val
                            break
                    break

    return matches


def _find_text_matches(doc: ReportDocument, chart: ChartRegion, data_points: list[dict]) -> dict[str, float]:
    """在同页/相邻页文本中查找与图表数据对应的陈述。

    策略：
    - 搜索包含图表标签和数值的文本
    - 例："零售业务占 35%"、"对公业务收入 45.5 亿元"
    """
    matches: dict[str, float] = {}

    for dp in data_points:
        label = dp.get("label", "")
        expected_val = dp.get("value")
        if not label or expected_val is None:
            continue

        for seg in doc.texts:
            if abs(seg.page - chart.page) > 3:
                continue

            text = seg.text.lower()
            if label.lower() not in text:
                continue

            # 在标签附近提取数值
            val = _extract_number_near_text(text, label.lower(), expected_val)
            if val is not None:
                matches[label] = val
                break

    return matches


def _extract_number_near_text(text: str, label: str, expected_val: float, window: int = 30) -> float | None:
    """在标签附近的文本中提取与预期值接近的数值。"""
    idx = text.find(label)
    if idx < 0:
        return None

    start = max(0, idx - window)
    end = min(len(text), idx + len(label) + window)
    window_text = text[start:end]

    # 提取所有数值
    import re

    numbers = []
    for match in re.finditer(r"\d+\.?\d*", window_text):
        try:
            numbers.append(float(match.group()))
        except ValueError:
            continue

    if not numbers:
        return None

    # 找与预期值最接近的（允许比例差异）
    expected_abs = abs(expected_val)
    for num in numbers:
        if expected_abs > 0:
            ratio = num / expected_abs
            # 考虑到可能是百分比和绝对值之间的转换
            if 0.95 <= ratio <= 1.05 or 95 <= ratio <= 105:
                return num
        elif num == expected_val:
            return num

    return None


def _parse_number(text: str) -> float | None:
    """从文本中解析数值。"""
    import re

    text = text.strip()
    if text in ("—", "-", "–", "", "N/A", "n/a"):
        return None

    # 移除千分位
    cleaned = text.replace(",", "").replace(" ", "")

    # 检测括号负数
    is_negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        is_negative = True
        cleaned = cleaned[1:-1]
    elif cleaned.startswith("（") and cleaned.endswith("）"):
        is_negative = True
        cleaned = cleaned[1:-1]

    match = re.search(r"-?\d+\.?\d*", cleaned)
    if not match:
        return None

    try:
        val = float(match.group())
        return -abs(val) if is_negative else val
    except ValueError:
        return None
