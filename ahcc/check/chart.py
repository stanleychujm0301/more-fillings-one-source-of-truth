"""多模态图表三方核对（P4 实现）— 模块 C / 亮点 2。

流程：
1. 对 doc.charts 中每张图，调 VLM 抽数（饼图份额、柱图数值、折线趋势）
2. 在同页/相邻页找：
   - 表格中对应数据
   - 文本中"零售业务占 35%"这类陈述
3. 三方对比，输出 ChartCrossCheck
"""

from __future__ import annotations

import asyncio
import uuid

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
from ahcc.vlm.qwen_vl import extract_chart_data


async def run_chart_checks(doc_a: ReportDocument, doc_h: ReportDocument, max_charts: int = 15) -> list[Diff]:
    """对两份报告分别跑图表核对。

    Args:
        max_charts: 最多核对 N 张图表（Demo 场景下 15 张已足够展示能力）。
            预算只计入「有图像、会真正调用 VLM」的图表，避免被空图表耗尽。
    """
    # 仅保留有图像的图表（会真正触发 VLM），按预算跨两份报告截断
    candidates = [
        (doc, chart)
        for doc in (doc_a, doc_h)
        for chart in doc.charts
        if chart.image_path
    ][:max_charts]

    # 并发核对，受 LLM 并发上限约束，避免打爆 provider
    sem = asyncio.Semaphore(max(1, settings.llm_concurrency))

    async def _guarded(doc: ReportDocument, chart: ChartRegion) -> Diff | None:
        async with sem:
            return await _check_one_chart(doc, chart)

    results = await asyncio.gather(
        *[_guarded(doc, chart) for doc, chart in candidates],
        return_exceptions=True,
    )

    diffs: list[Diff] = []
    failures = 0
    for res in results:
        if isinstance(res, Exception):
            failures += 1
            logger.warning(f"图表核对任务失败: {res}")
            continue
        if res:
            diffs.append(res)
    logger.info(
        f"图表核对完成，检查 {len(candidates)} 张图表，发现 {len(diffs)} 条异常"
        + (f"，{failures} 张失败" if failures else "")
    )
    return diffs


async def _check_one_chart(doc: ReportDocument, chart: ChartRegion) -> Diff | None:
    """单张图表的三方核对。

    步骤：
    1. VLM 提取图表数据
    2. 在同页表格中查找对应数据
    3. 在同页/相邻页文本中查找对应陈述
    4. 比对三者一致性
    """
    # 1. VLM 提取
    if not chart.image_path:
        return None

    try:
        vlm_result = await asyncio.to_thread(extract_chart_data, chart.image_path)
    except Exception as e:
        logger.warning(f"VLM 提取失败 {chart.chart_id}: {e}")
        return None

    if not vlm_result or not vlm_result.get("data_points"):
        return None

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
