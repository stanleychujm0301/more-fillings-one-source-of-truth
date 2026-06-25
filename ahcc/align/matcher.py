"""跨语言数据点抽取与对齐（P4 实现）。

流程：
1. 对 A/H 两份 ReportDocument，分章节并行调用 LLM 抽取 30 个关键数据点
2. 用 glossary.csv 做规则匹配（高置信度直接匹配）
3. 剩余项再用 LLM align_zh_en 做语义匹配
4. 输出 List[AlignedPair]
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from loguru import logger

from ahcc.align.glossary import glossary, to_simplified
from ahcc.schemas import (
    AlignedPair,
    Currency,
    DataPoint,
    Evidence,
    FinancialTable,
    Language,
    LocalizedString,
    ReportDocument,
    ReportSide,
    TextSegment,
)


# ============================================================
# 1. 关键数据点抽取
# ============================================================

async def _extract_keypoints(doc: ReportDocument) -> list[DataPoint]:
    """从 ReportDocument 中抽取关键财务数据点。

    策略：
    1. 规则匹配（高置信度）：基于 glossary 在表格和文本中搜索匹配项
    2. LLM 兜底（中置信度）：对规则未覆盖的部分，调用 LLM 补充抽取
    """
    points: list[DataPoint] = []
    seen_keys: set[str] = set()

    # ---- 阶段 1: 表格规则匹配 ----
    table_points = _extract_from_tables(doc)
    for p in table_points:
        if p.canonical_key not in seen_keys:
            points.append(p)
            seen_keys.add(p.canonical_key)

    # ---- 阶段 2: 文本规则匹配 ----
    text_points = _extract_from_texts(doc)
    for p in text_points:
        if p.canonical_key not in seen_keys:
            points.append(p)
            seen_keys.add(p.canonical_key)

    # ---- 阶段 2.5: 聚合指标修正（解决子项误匹配 + 文本污染）----
    # 策略：优先信任表格提取（confidence=0.9），其次文本（confidence=0.75）
    # 同一 key 多值时：优先最高 confidence → 然后取最频繁出现的值（mode）→
    # 最后取最早出现的页码（合并报表通常在前，母公司在后）
    _AGGREGATE_KEYS = {"total_assets", "total_liabilities", "equity", "revenue", "net_profit", "operating_profit"}
    for key in _AGGREGATE_KEYS:
        all_candidates = [p for p in table_points + text_points if p.canonical_key == key]
        if len(all_candidates) > 1:
            best = _pick_best_aggregate(all_candidates)
            for i, p in enumerate(points):
                if p.canonical_key == key:
                    points[i] = best
                    break

    # ---- 阶段 2.6: BS 页面锚定修正（区分合并报表 vs 母公司报表）----
    # 以 total_assets 最大值所在页为锚点，优先取同页或相邻页的 BS 项目
    _BS_KEYS = {
        "cash_equivalents", "receivables", "fixed_assets", "intangible_assets",
        "goodwill", "long_term_investments", "investment_property",
        "construction_in_progress", "current_assets", "non_current_assets",
        "short_term_borrowings", "long_term_borrowings",
        "current_liabilities", "non_current_liabilities",
        "share_capital", "capital_reserve", "retained_earnings",
    }
    bs_anchor_pages: set[int] = set()
    # 只用表格提取的 total_assets 来锚定（文本提取可能污染）
    ta_candidates = [p for p in table_points if p.canonical_key == "total_assets"]
    if not ta_candidates:
        ta_candidates = [p for p in text_points if p.canonical_key == "total_assets"]
    if ta_candidates:
        max_ta = max(p.value or 0 for p in ta_candidates)
        max_pages = {p.evidence.page for p in ta_candidates if p.value == max_ta and p.evidence.page}
        bs_anchor_pages.update(max_pages)
        # 相邻页 only if 它们没有自己的 total_assets（避免母公司报表页被纳入）
        ta_by_page = {}
        for p in ta_candidates:
            if p.evidence.page:
                ta_by_page[p.evidence.page] = p.value or 0
        for pg in list(max_pages):
            for adj in (pg - 1, pg + 1):
                if adj not in ta_by_page:
                    bs_anchor_pages.add(adj)
    if bs_anchor_pages:
        for key in _BS_KEYS:
            # 只用表格提取做锚定修正（避免文本污染）
            all_candidates = [p for p in table_points if p.canonical_key == key]
            if not all_candidates:
                continue
            if len(all_candidates) > 1:
                anchored = [p for p in all_candidates if p.evidence.page in bs_anchor_pages]
                if anchored:
                    best = max(anchored, key=lambda p: p.value or 0)
                    for i, p in enumerate(points):
                        if p.canonical_key == key:
                            points[i] = best
                            break

    # ---- 阶段 2.7: PL 页面锚定修正（区分合并利润表 vs 母公司利润表）----
    # 以 revenue / net_profit 最早出现且频次最高的页为锚点，
    # 优先取同页或相邻页的 PL 项目（排除现金流量表页）
    _PL_KEYS = {
        "revenue", "operating_profit", "total_profit", "net_profit",
        "income_tax", "eps_basic", "eps_diluted",
    }
    pl_anchor_pages: set[int] = set()
    # 用 net_profit 或 revenue 的最早高频页做锚点
    pl_seed_candidates = []
    for seed_key in ("net_profit", "revenue"):
        pl_seed_candidates += [p for p in table_points if p.canonical_key == seed_key]
    if pl_seed_candidates:
        # 取最高 confidence，然后取最早页码
        max_conf = max(p.confidence or 0 for p in pl_seed_candidates)
        best_seeds = [p for p in pl_seed_candidates if (p.confidence or 0) == max_conf]
        # 按 (值频次降序, 页码升序) 排序
        from collections import Counter
        val_counts = Counter(p.value for p in best_seeds if p.value is not None)
        if val_counts:
            most_common_val, _ = val_counts.most_common(1)[0]
            best_seeds = [p for p in best_seeds if p.value == most_common_val]
        best_seed = min(best_seeds, key=lambda p: p.evidence.page or 9999)
        seed_page = best_seed.evidence.page
        if seed_page:
            pl_anchor_pages.add(seed_page)
            # 相邻页（只要该页没有同 key 的冲突值就纳入）
            for adj in (seed_page - 1, seed_page + 1):
                if adj > 0:
                    pl_anchor_pages.add(adj)
    if pl_anchor_pages:
        for key in _PL_KEYS:
            all_candidates = [p for p in table_points if p.canonical_key == key]
            if not all_candidates:
                continue
            if len(all_candidates) > 1:
                anchored = [p for p in all_candidates if p.evidence.page in pl_anchor_pages]
                if anchored:
                    best = _pick_best_aggregate(anchored)
                    for i, p in enumerate(points):
                        if p.canonical_key == key:
                            points[i] = best
                            break

    # ---- 阶段 3: OCR 兜底抽取（针对 PDF 字体编码损坏） ----
    # 触发条件：
    # 1. 规则抽取数量 < 10（H 股常因编码问题导致 glossary 匹配失败）
    # 2. 核心财务指标缺失 >= 2 个
    _CORE_KEYS = {
        "total_assets", "total_liabilities", "equity",
        "revenue", "total_profit", "net_profit", "operating_profit", "income_tax",
        "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
        "share_capital", "eps_basic",
    }
    missing_core = [k for k in _CORE_KEYS if k not in seen_keys]
    should_fallback = len(points) < 10 or len(missing_core) >= 2

    if should_fallback and doc.side == ReportSide.H_SHARE:
        try:
            from ahcc.parser.ocr_fallback import extract_keypoints_from_page_images

            # Use full-report table pages; fall back to all pages when no table pages exist.
            table_pages = sorted({t.page for t in doc.tables})
            ocr_pages = table_pages or list(range(1, max(doc.total_pages, 0) + 1))

            ocr_points = extract_keypoints_from_page_images(
                doc.file_path, ocr_pages, side=doc.side
            )
            for p in ocr_points:
                if p.canonical_key not in seen_keys:
                    points.append(p)
                    seen_keys.add(p.canonical_key)
        except Exception as e:
            logger.warning(f"OCR 兜底抽取失败: {e}")

    # ---- 阶段 4: LLM 兜底抽取 ----
    missing_core = [k for k in _CORE_KEYS if k not in seen_keys]
    should_llm = len(points) < 8 or len(missing_core) >= 3
    if should_llm:
        try:
            llm_points = await _extract_with_llm(doc, seen_keys)
            for p in llm_points:
                if p.canonical_key not in seen_keys:
                    points.append(p)
                    seen_keys.add(p.canonical_key)
        except Exception as e:
            logger.warning(f"LLM 兜底抽取失败: {e}")

    logger.info(f"从 {doc.side.value} 报告抽取 {len(points)} 个数据点")
    return points


def _pick_best_aggregate(candidates: list[DataPoint]) -> DataPoint:
    """从多个同名候选数据点中选出最可信的一个。

    策略（按优先级）：
    1. 最高 confidence（表格 0.9 > 文本 0.75 > LLM 0.6）
    2. 同 confidence 时，取出现最频繁的值（mode）——合并报表数据
       通常在摘要、主要会计数据、正表多处重复出现
    3. 同频时，取最早页码（合并报表一般在母公司报表之前）
    """
    from collections import Counter

    # 1. 按 confidence 分组，取最高 confidence 组
    max_conf = max(p.confidence or 0 for p in candidates)
    best_group = [p for p in candidates if (p.confidence or 0) == max_conf]
    if len(best_group) == 1:
        return best_group[0]

    # 2. 同 confidence 时，按 value 计频次，取最频繁的 value
    value_counts = Counter(p.value for p in best_group if p.value is not None)
    if value_counts:
        most_common_val, _ = value_counts.most_common(1)[0]
        mode_group = [p for p in best_group if p.value == most_common_val]
    else:
        mode_group = best_group
    if len(mode_group) == 1:
        return mode_group[0]

    # 3. 同 value 时，取最早页码
    return min(mode_group, key=lambda p: p.evidence.page or 9999)


# ============================================================
# 1.1 表格规则匹配
# ============================================================

def _extract_from_tables(doc: ReportDocument) -> list[DataPoint]:
    """从表格中基于 glossary 规则匹配提取数据点。

    策略：对 glossary 中每个术语，在表格的 cells 中搜索匹配的行，
    然后从该行的数值列提取金额。
    """
    points: list[DataPoint] = []
    all_keys = glossary.all_canonical_keys()

    for table in doc.tables:
        # 将 cells 按行分组
        rows: dict[int, list] = {}
        for cell in table.cells:
            rows.setdefault(cell.row, []).append(cell)

        prev_key: str | None = None
        prev_label: str = ""
        prev_entry = None

        for row_idx, cells in sorted(rows.items()):
            # 按列排序
            cells_sorted = sorted(cells, key=lambda c: c.col)
            if not cells_sorted:
                continue

            # 第一列通常是科目名
            label_cell = cells_sorted[0]
            raw_label = label_cell.text.strip()
            label_text = to_simplified(raw_label)

            # 尝试匹配 glossary（优先原始文本，再试简体转换）
            canonical_key = None
            if len(label_text) >= 2:
                canonical_key = glossary.lookup(raw_label) or glossary.lookup(label_text)

            # 处理 rowspan：当前行标签为空，但包含数值，且上一行有匹配项
            if not canonical_key and prev_key and len(label_text) < 2:
                value, value_text = _find_first_number_in_row(cells_sorted[1:])
                if value is not None:
                    points.append(
                        DataPoint(
                            name=LocalizedString(
                                zh=prev_entry.zh_cn if prev_entry else prev_label,
                                en=prev_entry.en if prev_entry else "",
                            ),
                            canonical_key=prev_key,
                            value=value,
                            value_text=value_text,
                            unit=table.unit or doc.metadata.get("unit"),
                            currency=table.currency or doc.metadata.get("currency"),
                            period=table.period,
                            evidence=Evidence(
                                side=doc.side,
                                page=table.page,
                                bbox=table.bbox,
                                snippet=f"[{prev_label}] {value_text}",
                                section=table.title.zh or table.title.en,
                            ),
                            confidence=0.9,
                        )
                    )
                continue

            if not canonical_key:
                prev_key = None
                prev_label = ""
                prev_entry = None
                continue

            # 从该行的其他列提取数值（优先取第一个非空数值）
            value, value_text = _find_first_number_in_row(cells_sorted[1:])
            if value is None:
                prev_key = canonical_key
                prev_label = label_text
                prev_entry = glossary.get_entry(canonical_key)
                continue

            entry = glossary.get_entry(canonical_key)
            name = LocalizedString(
                zh=entry.zh_cn if entry else label_text,
                en=entry.en if entry else "",
            )

            points.append(
                DataPoint(
                    name=name,
                    canonical_key=canonical_key,
                    value=value,
                    value_text=value_text,
                    unit=table.unit or doc.metadata.get("unit"),
                    currency=table.currency or doc.metadata.get("currency"),
                    period=table.period,
                    evidence=Evidence(
                        side=doc.side,
                        page=table.page,
                        bbox=table.bbox,
                        snippet=f"[{label_text}] {value_text}",
                        section=table.title.zh or table.title.en,
                    ),
                    confidence=0.9,
                )
            )

            prev_key = canonical_key
            prev_label = label_text
            prev_entry = entry

    return points


def _find_first_number_in_row(cells: list) -> tuple[Optional[float], Optional[str]]:
    """在一行的单元格列表中找到财务金额（跳过附注编号）。

    策略：
    1. 优先返回含逗号或大数值（>1000）的金额
    2. 仅当没有大额数值时才返回小整数（可能是附注编号 1, 2, 3...）
    """
    candidates: list[tuple[float, str]] = []
    for cell in cells:
        text = cell.text.strip()
        val = _parse_number(text)
        if val is not None:
            candidates.append((val, text))

    if not candidates:
        return None, None

    # 优先选含逗号或绝对值 >= 1000 的金额（排除附注编号）
    for val, text in candidates:
        if "," in text or abs(val) >= 1000:
            return val, text

    # 无有效金额（全是附注编号），跳过该行
    return None, None


# ============================================================
# 1.2 文本规则匹配
# ============================================================

def _extract_from_texts(doc: ReportDocument) -> list[DataPoint]:
    """从文本段落中基于 glossary 规则匹配提取数据点。

    策略：对 glossary 中每个术语，在文本中搜索匹配的行，
    然后用正则提取附近的数值。

    严格过滤：
    1. 只对主表章节（bs/pl/cf/equity）的前 60 页文本提取
    2. 跳过明显是附注、讨论分析、封面的文本段
    3. 跳过包含大量明细数据的文本（如"其中："、"附注"、"明细"）
    """
    points: list[DataPoint] = []

    # 主表页面范围限制：前 60 页通常包含所有主表
    # H 股年报可能有 300+ 页，后面全是附注
    MAX_STATEMENT_PAGE = 60

    for seg in doc.texts:
        if len(seg.text) < 5:
            continue

        # 页码过滤：只在前 MAX_STATEMENT_PAGE 页内搜索
        if seg.page and seg.page > MAX_STATEMENT_PAGE:
            continue

        # 章节过滤：仅对主表章节
        if seg.section not in ("bs", "pl", "cf", "equity"):
            continue

        # 跳过明显是附注/明细的文本段
        text_lower = seg.text.lower()
        skip_markers = ["附注", "其中：", "明细", "详细", "披露", "注释", "note ", "notes ", "details", "breakdown"]
        if any(m in text_lower for m in skip_markers):
            continue

        # 跳过过长的文本段（可能是长篇讨论而非表格数据）
        if len(seg.text) > 800:
            continue

        text_simplified = to_simplified(seg.text)
        text_lower = text_simplified.lower()

        # 尝试匹配每个 glossary 术语
        for canonical_key in glossary.all_canonical_keys():
            entry = glossary.get_entry(canonical_key)
            if not entry:
                continue

            # 检查是否包含该术语的中文或英文形式（大小写不敏感）
            matched = False
            matched_label = ""
            for form in [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]:
                if not form:
                    continue
                # 中文直接匹配；英文转小写匹配
                if form in text_simplified or form.lower() in text_lower:
                    matched = True
                    matched_label = form
                    break
                # H 股文本已转简体，但 glossary 形式可能是繁体 — 也检查简体版本
                form_s = to_simplified(form)
                if form_s != form and (form_s in text_simplified or form_s.lower() in text_lower):
                    matched = True
                    matched_label = form_s
                    break

            if not matched:
                continue

            # 提取数值（在术语附近搜索，大小写不敏感）
            val, val_text = _extract_number_near_label(text_simplified, matched_label)
            if val is None:
                continue

            points.append(
                DataPoint(
                    name=LocalizedString(zh=entry.zh_cn, en=entry.en),
                    canonical_key=canonical_key,
                    value=val,
                    value_text=val_text,
                    unit=doc.metadata.get("unit"),
                    currency=doc.metadata.get("currency"),
                    period=None,
                    evidence=Evidence(
                        side=doc.side,
                        page=seg.page,
                        bbox=seg.bbox,
                        snippet=seg.text[:200],
                        section=seg.section,
                    ),
                    confidence=0.75,
                )
            )

    return points


def _is_likely_percentage(text: str, val_text: str, offset: int) -> bool:
    """判断该数值附近是否有 % 符号，或是否为百分比格式（如 3.47, 6.19）。"""
    # 搜索数值前后 5 个字符是否有 %
    start = max(0, offset - 5)
    end = min(len(text), offset + len(val_text) + 5)
    context = text[start:end]
    if "%" in context:
        return True
    # 纯小数且绝对值 < 100 且不带逗号，大概率是百分比或比率
    if "," not in val_text and abs(float(val_text.replace(",", ""))) < 100:
        # 如果文本中有"%"、"percent"、"比率"、"占比"等词
        if any(k in text[:200] for k in ["%", "percent", "比率", "占比", "增长", "下降", "变动"]):
            return True
    return False


def _extract_number_near_label(text: str, label: str, window: int = 40) -> tuple[Optional[float], Optional[str]]:
    """在术语附近提取数值，优先取术语之后的数字（财务文本中金额通常在标签后）。"""
    import re

    # 大小写不敏感查找
    text_lower = text.lower()
    label_lower = label.lower()
    idx = text_lower.find(label_lower)
    if idx < 0:
        return None, None

    def _filter_candidates(candidates: list[tuple[float, str]], search_text: str) -> tuple[Optional[float], Optional[str]]:
        for val, val_text in candidates:
            # 跳过年份
            if 1990 <= val <= 2035 and "." not in val_text:
                continue
            # 跳过小整数（附注编号 1-50）
            if 1 <= val <= 50 and "," not in val_text and "." not in val_text:
                continue
            # 跳过百分比（增长率、比率等）
            # 查找 val_text 在 search_text 中的位置
            m = re.search(re.escape(val_text), search_text)
            if m and _is_likely_percentage(search_text, val_text, m.start()):
                continue
            return val, val_text
        return None, None

    # 1. 优先搜索术语之后的文本（金额通常紧跟标签）
    after_start = idx + len(label)
    after_end = min(len(text), after_start + window)
    after_text = text[after_start:after_end]
    result = _filter_candidates(_extract_all_numbers(after_text), after_text)
    if result[0] is not None:
        return result

    # 2. 术语后无可信数值，再搜索术语之前的文本
    before_start = max(0, idx - window)
    before_text = text[before_start:idx]
    result = _filter_candidates(_extract_all_numbers(before_text), before_text)
    if result[0] is not None:
        return result

    return None, None


def _extract_all_numbers(text: str) -> list[tuple[float, str]]:
    """从文本中提取所有可解析的数值及其原始文本。"""
    results: list[tuple[float, str]] = []
    # 基于 _parse_number 的正则策略，逐个匹配
    cleaned = text.replace(",", "").replace(" ", "").replace("'", "")

    # 检测括号负数（简化处理：替换后搜索）
    import re

    # 先找带逗号的标准格式
    for match in re.finditer(r"-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?", text):
        val = _parse_number(match.group())
        if val is not None:
            results.append((val, match.group()))

    # 再找无逗号的数字（避免重复匹配已找到的）
    found_spans = {m.span() for m in re.finditer(r"-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?", text)}
    for match in re.finditer(r"-?\d{1,15}(?:\.\d+)?", text):
        if any(match.start() >= s[0] and match.end() <= s[1] for s in found_spans):
            continue
        val = _parse_number(match.group())
        if val is not None:
            results.append((val, match.group()))

    # 按在文本中出现的位置排序
    return results


# ============================================================
# 1.3 LLM 兜底抽取
# ============================================================

async def _extract_with_llm(doc: ReportDocument, seen_keys: set[str]) -> list[DataPoint]:
    """对规则未覆盖的数据点，调用 LLM 补充抽取。"""
    from ahcc.llm.client import cached_call, load_prompt

    # 构建上下文：优先取表格标题和数值，文本取 section 标记的段落
    context = _build_extraction_context(doc)

    prompt_template = load_prompt("extract_keypoints.txt")
    # 用 replace 逐个替换，避免模板中的 JSON {} 被 format() 误解析
    prompt = (
        prompt_template
        .replace("{side}", doc.side.value)
        .replace("{language}", doc.primary_language.value)
        .replace("{currency}", doc.metadata.get("currency", "CNY"))
        .replace("{period}", doc.metadata.get("period", ""))
        .replace("{section}", "、".join(set(t.section for t in doc.texts if t.section)))
        .replace("{content}", context)
    )
    messages = [
        {"role": "system", "content": "你是一个专业的财务数据提取助手。请从以下年报内容中提取关键财务数据点，输出 JSON 格式。"},
        {"role": "user", "content": prompt},
    ]

    result = await asyncio.to_thread(
        cached_call,
        "extract",
        messages,
        json_mode=True,
        temperature=0.1,
    )

    points: list[DataPoint] = []
    # 兼容两种键名: data_points (代码用) / datapoints (prompt 示例用)
    raw_items = result.get("data_points") or result.get("datapoints", [])
    for item in raw_items:
        ck = item.get("canonical_key", "")
        if ck in seen_keys:
            continue
        if not ck:
            continue

        entry = glossary.get_entry(ck)
        name = LocalizedString(
            zh=item.get("name", entry.zh_cn if entry else ""),
            en=entry.en if entry else item.get("name_en", ""),
        )

        val = _parse_number(str(item.get("value", "")))
        points.append(
            DataPoint(
                name=name,
                canonical_key=ck,
                value=val,
                value_text=str(item.get("value", "")),
                unit=item.get("unit"),
                currency=Currency(item.get("currency")) if item.get("currency") else None,
                period=item.get("period"),
                evidence=Evidence(
                    side=doc.side,
                    page=item.get("page", 1),
                    bbox=None,
                    snippet=item.get("snippet", "")[:200],
                    section=item.get("section"),
                ),
                confidence=0.6,
            )
        )

    return points


def _build_extraction_context(doc: ReportDocument, max_chars: int = 6000) -> str:
    """构建 LLM 抽取用的上下文（截断到 max_chars）。"""
    chunks: list[str] = []
    char_count = 0

    # 优先取表格
    for table in doc.tables:
        title = table.title.zh or table.title.en or ""
        table_text = f"\n[Table: {title} (Page {table.page})]\n"
        # 将 cells 按行重组为文本
        rows: dict[int, list] = {}
        for cell in table.cells:
            rows.setdefault(cell.row, []).append(cell)
        for r in sorted(rows.keys()):
            row_text = " | ".join(c.text for c in sorted(rows[r], key=lambda x: x.col))
            table_text += row_text + "\n"

        if char_count + len(table_text) > max_chars:
            break
        chunks.append(table_text)
        char_count += len(table_text)

    # 补充文本段（带 section 标记的优先）
    for seg in doc.texts:
        if seg.section in ("bs", "pl", "cf", "notes"):
            seg_text = f"\n[{seg.section} p{seg.page}] {seg.text[:300]}\n"
            if char_count + len(seg_text) > max_chars:
                break
            chunks.append(seg_text)
            char_count += len(seg_text)

    return "".join(chunks)


# ============================================================
# 2. 数值解析工具
# ============================================================

def _parse_number(text: str) -> Optional[float]:
    """从文本中解析数值。

    支持的格式：
    - 1,234,567.89
    - 1 234 567.89
    - 123.45
    - （123.45）→ -123.45（括号表示负数）
    - (123.45) → -123.45
    - — / - / 空白 → None
    """
    if not text:
        return None

    text = text.strip()

    # 空白或横线表示无数据
    if text in ("—", "-", "–", "—", "", "N/A", "n/a", "不适用"):
        return None

    # 检测括号负数
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]
    elif text.startswith("（") and text.endswith("）"):
        is_negative = True
        text = text[1:-1]

    # 移除千分位符号和多余空格
    cleaned = text.replace(",", "").replace(" ", "").replace("'", "")

    # 尝试匹配数值（支持小数，限制长度防合并单元格污染）
    # 分支1: 标准千分位格式（最多5组，如 99,999,999,999,999 ≈ 99万亿）
    # 分支2: 无逗号纯数字（最多15位整数，覆盖万亿级报表）
    match = re.search(
        r"-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|"  # 千分位，最多5个逗号
        r"-?\d{1,15}(?:\.\d+)?",                 # 纯数字，最多15位整数
        cleaned,
    )
    if not match:
        return None

    num_str = match.group()
    # 去掉逗号后总长度检查
    digits_only = num_str.replace(",", "").replace(".", "").replace("-", "")
    if len(digits_only) > 15:
        return None

    try:
        val = float(num_str.replace(",", ""))
        # 合理性检查：>1e15 视为异常
        if abs(val) > 1e15:
            return None
        if is_negative:
            val = -abs(val)
        return val
    except ValueError:
        return None


# ============================================================
# 3. 对齐配对
# ============================================================

async def align_documents(doc_a: ReportDocument, doc_h: ReportDocument) -> list[AlignedPair]:
    """两份报告 → 对齐后的数据点对列表。"""
    logger.info(f"对齐 {doc_a.doc_id} ↔ {doc_h.doc_id}")

    # 并行抽取两侧关键数据点
    a_points, h_points = await asyncio.gather(
        _extract_keypoints(doc_a),
        _extract_keypoints(doc_h),
    )
    logger.info(f"A 抽取 {len(a_points)} 项 / H 抽取 {len(h_points)} 项")

    # 对齐
    pairs = _align_by_canonical_key(a_points, h_points)
    logger.info(f"对齐 {len(pairs)} 对")
    return pairs


def _align_by_canonical_key(
    a_points: list[DataPoint], h_points: list[DataPoint]
) -> list[AlignedPair]:
    """按 canonical_key 直接配对。"""
    h_by_key = {p.canonical_key: p for p in h_points}
    pairs: list[AlignedPair] = []
    for a in a_points:
        h = h_by_key.get(a.canonical_key)
        pairs.append(
            AlignedPair(
                canonical_key=a.canonical_key,
                topic_zh=a.name.zh or "",
                topic_en=a.name.en or "",
                a_point=a,
                h_point=h,
                alignment_confidence=1.0 if h else 0.0,
            )
        )
    for h in h_points:
        if not any(p.canonical_key == h.canonical_key for p in pairs):
            pairs.append(
                AlignedPair(
                    canonical_key=h.canonical_key,
                    topic_zh=h.name.zh or "",
                    topic_en=h.name.en or "",
                    a_point=None,
                    h_point=h,
                    alignment_confidence=0.0,
                )
            )
    return pairs
