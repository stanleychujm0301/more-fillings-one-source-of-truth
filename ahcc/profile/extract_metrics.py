"""全量数值提取 — 从 ReportDocument 中提取所有带标签的数字。

提取策略：
1. 表格扫描：遍历所有表格的所有行，第一列作为标签，后续列提取数字
2. 文本扫描：遍历所有文本段落，不限页码、不限章节，提取"标签+数字"模式
3. glossary映射：优先用 glossary.lookup 映射 canonical_key，未匹配则用 snake_case(label)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from loguru import logger

from ahcc.align.glossary import glossary, to_simplified
from ahcc.parser.audit import add_audit_warning
from ahcc.profile.models import MetricItem
from ahcc.schemas import Currency, Evidence, FinancialTable, LocalizedString, ReportDocument, ReportSide, TextSegment


# ============================================================
# 通用数值解析（复用 matcher.py 逻辑）
# ============================================================

_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|"  # 千分位格式
    r"-?\d{1,15}(?:\.\d+)?"                   # 纯数字格式
)


def _parse_number(text: str) -> Optional[float]:
    """从文本中解析数值。"""
    if not text:
        return None
    text = text.strip()
    if text in ("—", "-", "–", "", "N/A", "n/a", "不适用"):
        return None

    # 括号负数
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]
    elif text.startswith("（") and text.endswith("）"):
        is_negative = True
        text = text[1:-1]

    cleaned = text.replace(",", "").replace(" ", "").replace("'", "")
    match = _NUMBER_RE.match(cleaned)
    if not match:
        return None
    try:
        val = float(match.group())
        return -val if is_negative else val
    except ValueError:
        return None


def _find_first_number(text: str, min_abs: float = 1000.0) -> tuple[Optional[float], Optional[str]]:
    """在文本中找到第一个有效的财务金额。

    跳过：
    - 年份 (1990-2035)
    - 小整数 (1-50，可能是附注编号)
    - 百分比（数字后面紧跟 %）
    """
    for match in _NUMBER_RE.finditer(text):
        raw = match.group()
        val = _parse_number(raw)
        if val is None:
            continue
        # 跳过年份
        if 1990 <= val <= 2035 and "." not in raw:
            continue
        # 跳过小整数（附注编号）
        if 1 <= val <= 50 and "," not in raw and "." not in raw:
            continue
        # 跳过百分比
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos] == "%":
            continue
        # 优先选含逗号或大数值
        if "," in raw or abs(val) >= min_abs:
            return val, raw
    # 如果都太小，返回第一个找到的数字
    for match in _NUMBER_RE.finditer(text):
        raw = match.group()
        val = _parse_number(raw)
        if val is not None:
            return val, raw
    return None, None


def _to_snake_case(text: str) -> str:
    """将中文/英文标签转为 snake_case key。"""
    # 去除标点、空格，转为小写
    cleaned = re.sub(r"[^\w一-鿿]", "_", text.strip().lower())
    # 连续下划线合并
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown_metric"


# ============================================================
# 表格数值提取
# ============================================================

def _looks_like_label(text: str) -> bool:
    """判断文本是否像是一个财务标签（而非数字或空文本）。"""
    text = text.strip()
    if not text or len(text) < 2:
        return False
    # 如果整段都是数字/逗号/括号，不是标签
    if re.match(r"^[\d,\(\)\(\)\s\-%]+$", text):
        return False
    # 如果包含至少一个中文字符或3个以上英文字母，视为标签
    has_chinese = bool(re.search(r"[一-龥]", text))
    has_english_word = len(re.findall(r"[a-zA-Z]{3,}", text)) > 0
    return has_chinese or has_english_word


def _find_label_column(cells: list) -> tuple[int, str] | None:
    """在一行的cells中找到标签列（第一列看起来像标签的列）。

    对于H股英文表格，标签通常在col=0，但如果col=0是行号/空白，
    则向后搜索第一个看起来像标签的列。
    """
    for cell in sorted(cells, key=lambda c: c.col):
        if _looks_like_label(cell.text):
            return cell.col, cell.text.strip()
    return None


def _extract_from_table(table: FinancialTable, side: ReportSide) -> list[MetricItem]:
    """从单个表格中提取所有带标签的数值。"""
    items: list[MetricItem] = []
    if not table.cells:
        return items

    # 按行分组
    rows: dict[int, list] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    prev_key: Optional[str] = None
    prev_label: str = ""

    for row_idx in sorted(rows.keys()):
        cells = sorted(rows[row_idx], key=lambda c: c.col)
        if not cells:
            continue

        # 找到标签列（不一定是第一列）
        label_info = _find_label_column(cells)
        if label_info is None:
            # 没有标签，但可能有数字：如果前一行有有效key，尝试继承
            if prev_key and len(cells) > 0:
                for cell in cells:
                    val, val_text = _find_first_number(cell.text)
                    if val is not None:
                        items.append(
                            MetricItem(
                                canonical_key=prev_key,
                                name=LocalizedString(zh=prev_label, en=prev_label),
                                value=val,
                                value_text=val_text,
                                unit=table.unit,
                                currency=table.currency,
                                period=table.period,
                                page=table.page,
                                evidence=Evidence(
                                    side=side,
                                    page=table.page,
                                    bbox=table.bbox,
                                    snippet=f"[{prev_label}] {val_text}",
                                    section=table.title.zh or table.title.en,
                                ),
                                confidence=0.9,
                                source="table",
                            )
                        )
                        break
            continue

        label_col, label_text = label_info

        # 用 glossary 映射 canonical_key（支持中英文、繁体）
        label_simplified = to_simplified(label_text)
        canonical_key = glossary.lookup(label_text) or glossary.lookup(label_simplified)

        # 如果直接lookup失败，尝试去除常见前缀/后缀再lookup
        if not canonical_key:
            cleaned_label = re.sub(r"^[\s\d\-–—\.\*]+", "", label_text).strip()
            canonical_key = glossary.lookup(cleaned_label) or glossary.lookup(to_simplified(cleaned_label))

        if not canonical_key:
            # 未匹配到glossary，用通用模式
            canonical_key = _to_snake_case(label_simplified)
            if not canonical_key or canonical_key == "unknown_metric":
                prev_key = None
                prev_label = ""
                continue

        # 从该行的标签列之后的所有列提取数值
        val, val_text = None, None
        for cell in cells:
            if cell.col <= label_col:
                continue
            val, val_text = _find_first_number(cell.text)
            if val is not None:
                break

        if val is None:
            prev_key = canonical_key
            prev_label = label_text
            continue

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label_text,
            en=entry.en if entry else "",
        )

        # 判断confidence：glossary匹配=0.9，通用模式=0.5
        conf = 0.9 if (glossary.lookup(label_text) or glossary.lookup(label_simplified)) else 0.5

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=val_text,
                unit=table.unit,
                currency=table.currency,
                period=table.period,
                page=table.page,
                evidence=Evidence(
                    side=side,
                    page=table.page,
                    bbox=table.bbox,
                    snippet=f"[{label_text}] {val_text}",
                    section=table.title.zh or table.title.en,
                ),
                confidence=conf,
                source="table" if conf >= 0.9 else "generic_pattern",
            )
        )
        prev_key = canonical_key
        prev_label = label_text

    return items


# ============================================================
# 文本数值提取
# ============================================================

# 通用"标签: 数字"模式 — 支持中英文
_TEXT_METRIC_RE = re.compile(
    r"([一-龥_a-zA-Z][一-龥_a-zA-Z\s\(\)（）,]*[一-龥_a-zA-Z])"
    r"[\s:：]"
    r"(-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|-?\d{1,15}(?:\.\d+)?)"
    r"([一-龥_a-zA-Z%\s]*)"
)

# 英文财务标签+数字模式（H股年报常见："Revenue   1,234,567" 带多个空格）
_ENGLISH_METRIC_RE = re.compile(
    r"\b([A-Z][a-zA-Z\s\(\)]{2,50}[a-zA-Z\)])"  # 英文标签（首字母大写）
    r"\s{2,}"  # 至少2个空格（表格列间距）
    r"(-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|-?\d{1,15}(?:\.\d+)?)"  # 数字
    r"(?:\s*([a-zA-Z%\s]+))?"  # 可选单位后缀
)


def _extract_from_text(seg: TextSegment, side: ReportSide, doc_unit: Optional[str], doc_currency: Optional[Currency]) -> list[MetricItem]:
    """从单个文本段中提取"标签+数字"模式。"""
    items: list[MetricItem] = []
    text = seg.text.strip()
    if len(text) < 5:
        return items

    # 策略1: 通用正则匹配 "标签: 数字" 或 "标签 数字"（中英文）
    for match in _TEXT_METRIC_RE.finditer(text):
        label = match.group(1).strip()
        num_text = match.group(2)
        unit_suffix = match.group(3).strip() if match.group(3) else ""

        val = _parse_number(num_text)
        if val is None:
            continue
        # 跳过小数字（可能是编号）
        if abs(val) < 100 and "," not in num_text:
            continue

        label_simplified = to_simplified(label)
        canonical_key = glossary.lookup(label) or glossary.lookup(label_simplified)
        conf = 0.75
        if not canonical_key:
            canonical_key = _to_snake_case(label_simplified)
            conf = 0.5

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label,
            en=entry.en if entry else "",
        )

        # 合并单位
        unit = doc_unit or ""
        if unit_suffix and any(u in unit_suffix for u in ("元", "万元", "百万元", "千元", "million", "thousand", "亿")):
            unit = unit_suffix

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=num_text,
                unit=unit or None,
                currency=doc_currency,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[{label}] {num_text}",
                    section=seg.section,
                ),
                confidence=conf,
                source="text" if conf >= 0.75 else "generic_pattern",
            )
        )

    # 策略1b: 英文表格内联文本模式（H股PDF转文本后常见）
    for match in _ENGLISH_METRIC_RE.finditer(text):
        label = match.group(1).strip()
        num_text = match.group(2)
        unit_suffix = (match.group(3) or "").strip()

        val = _parse_number(num_text)
        if val is None:
            continue
        # 跳过小数字
        if abs(val) < 100 and "," not in num_text:
            continue

        canonical_key = glossary.lookup(label) or glossary.lookup(label.lower())
        conf = 0.75
        if not canonical_key:
            canonical_key = _to_snake_case(label)
            conf = 0.5

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else "",
            en=entry.en if entry else label,
        )

        unit = doc_unit or ""
        if unit_suffix and any(u in unit_suffix.lower() for u in ("million", "thousand", "元")):
            unit = unit_suffix

        # 避免重复提取
        already = any(it.canonical_key == canonical_key and abs(it.value - val) < 1 for it in items)
        if already:
            continue

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=num_text,
                unit=unit or None,
                currency=doc_currency,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[{label}] {num_text}",
                    section=seg.section,
                ),
                confidence=conf,
                source="text" if conf >= 0.75 else "generic_pattern",
            )
        )

    # 策略2: 对 glossary 中的每个术语，在该文本段中搜索匹配
    # 这是为了捕获那些不符合 "标签: 数字" 格式的数据（如表格内联文本）
    # 英文文本用原始text，中文用simplified
    text_for_matching = text if side == ReportSide.H_SHARE else to_simplified(text)
    for key in glossary.all_canonical_keys():
        entry = glossary.get_entry(key)
        if not entry:
            continue
        matched = False
        matched_label = ""
        for form in [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]:
            if not form:
                continue
            # 英文文本优先用原始大小写匹配，中文用简化字匹配
            if side == ReportSide.H_SHARE:
                if form in text_for_matching or form.lower() in text_for_matching.lower():
                    matched = True
                    matched_label = form
                    break
            else:
                if form in text_for_matching or form.lower() in text_for_matching.lower():
                    matched = True
                    matched_label = form
                    break
        if not matched:
            continue

        # 已经通过策略1提取过的跳过
        already_extracted = any(it.canonical_key == key for it in items)
        if already_extracted:
            continue

        # 在标签附近找数字
        idx = text_for_matching.find(matched_label)
        if idx < 0:
            idx = text_for_matching.lower().find(matched_label.lower())
        if idx < 0:
            continue
        window = text_for_matching[max(0, idx - 20):idx + len(matched_label) + 60]
        val, val_text = _find_first_number(window, min_abs=0)  # 不限制最小值
        if val is None:
            continue

        name = LocalizedString(zh=entry.zh_cn, en=entry.en)
        items.append(
            MetricItem(
                canonical_key=key,
                name=name,
                value=val,
                value_text=val_text,
                unit=doc_unit,
                currency=doc_currency,
                page=seg.page,
                evidence=Evidence(
                    side=side,
                    page=seg.page,
                    bbox=seg.bbox,
                    snippet=f"[{matched_label}] {val_text}",
                    section=seg.section,
                ),
                confidence=0.75,
                source="text",
            )
        )

    return items


# ============================================================
# 主入口
# ============================================================

def extract_metrics(doc: ReportDocument) -> list[MetricItem]:
    """从 ReportDocument 中提取全量数值指标。

    策略：
    1. 遍历所有表格（不限页码）提取行标签+数值
    2. 遍历所有文本段落（不限页码、不限章节）提取"标签+数值"
    3. 同一 canonical_key 同一页去重，保留 confidence 最高的
    """
    all_items: list[MetricItem] = []
    doc_unit = doc.metadata.get("unit")
    doc_currency = doc.metadata.get("currency")
    if isinstance(doc_currency, str):
        try:
            doc_currency = Currency(doc_currency)
        except ValueError:
            doc_currency = None

    side_label = "A股" if doc.side == ReportSide.A_SHARE else "H股"
    logger.info(f"[{side_label}] 开始提取指标: {len(doc.tables)} 表, {len(doc.texts)} 文本段, 单位={doc_unit}")

    # 1. 表格提取
    table_items_count = 0
    for table in doc.tables:
        items = _extract_from_table(table, doc.side)
        all_items.extend(items)
        table_items_count += len(items)

    # 2. 文本提取
    text_items_count = 0
    for seg in doc.texts:
        items = _extract_from_text(seg, doc.side, doc_unit, doc_currency)
        all_items.extend(items)
        text_items_count += len(items)

    logger.info(f"[{side_label}] 原始提取: 表格={table_items_count}, 文本={text_items_count}, 总计={len(all_items)}")

    # 3. 同页去重：同一 canonical_key + 同一 page，保留 confidence 最高的
    seen: dict[tuple[str, int], MetricItem] = {}
    for item in all_items:
        key = (item.canonical_key, item.page)
        if key not in seen or item.confidence > seen[key].confidence:
            seen[key] = item

    result = list(seen.values())
    logger.info(f"[{side_label}] 同页去重后: {len(result)} 个指标")

    # 4. 分组收集：同一 canonical_key 保留所有出现（不再跨页合并为单一值）
    from ahcc.profile.models import InternalInconsistency, MetricOccurrences

    grouped: dict[str, list[MetricItem]] = {}
    for item in result:
        grouped.setdefault(item.canonical_key, []).append(item)

    # 5. 构建 MetricOccurrences（选 primary + 内部一致性检查）
    final: list[MetricOccurrences] = []
    for key, items in grouped.items():
        # primary = 绝对值最大 + confidence 最高的
        primary = max(items, key=lambda m: (abs(m.value or 0), m.confidence))
        occ = MetricOccurrences(
            canonical_key=key,
            name=primary.name,
            primary=primary,
            all_occurrences=items,
        )
        # 内部一致性检查：
        #   - 只比较表格提取（conf>=0.9）、值>=1000（排除页码/百分比/碎片）
        #   - 只在同一页内比较（不同页可能是合并 vs 母公司、本期 vs 上期）
        from collections import defaultdict

        reliable = [m for m in items if m.value is not None and m.confidence >= 0.9 and abs(m.value) >= 1000]
        by_page: dict[int, list[MetricItem]] = defaultdict(list)
        for m in reliable:
            by_page[m.page].append(m)

        count = 0
        for page, page_items in by_page.items():
            page_values = set(round(m.value, 2) for m in page_items)
            if len(page_values) <= 1:
                continue
            occ.is_internally_consistent = False
            for i, a in enumerate(page_items):
                for b in page_items[i + 1:]:
                    if round(a.value, 2) != round(b.value, 2):
                        delta = abs(a.value - b.value)
                        base = max(abs(a.value), abs(b.value), 1e-9)
                        occ.internal_inconsistencies.append(
                            InternalInconsistency(item_a=a, item_b=b, delta=delta, delta_pct=delta / base * 100)
                        )
                        count += 1
                        if count >= 3:
                            break
                if count >= 3:
                    break
            if count >= 3:
                break
        final.append(occ)

    # 诊断
    core_keys = {"total_assets", "total_liabilities", "equity", "revenue",
                 "net_profit", "total_profit", "operating_profit",
                 "cash_equivalents", "operating_cash_flow"}
    found_core = [occ.canonical_key for occ in final if occ.canonical_key in core_keys]
    missing_core = core_keys - set(found_core)
    inconsistent_count = sum(1 for occ in final if not occ.is_internally_consistent)
    total_occurrences = sum(len(occ.all_occurrences) for occ in final)
    logger.info(
        f"[{side_label}] 最终指标: {len(final)} 个key, {total_occurrences} 次出现, "
        f"核心命中={len(found_core)}/{len(core_keys)}, 缺失={missing_core}, "
        f"内部不一致={inconsistent_count}"
    )

    # OCR 兜底：核心指标缺失过多或总量过少时触发
    if (len(found_core) < 5 or len(final) < 15) and Path(doc.file_path).exists():
        logger.warning(f"[{side_label}] 标准提取不足，触发 OCR 兜底")
        try:
            from ahcc.parser.ocr_fallback import extract_metrics_via_ocr
            ocr_items = extract_metrics_via_ocr(
                doc.file_path,
                side=doc.side,
                max_pages=None,
                unit=doc_unit,
                currency=doc_currency,
            )
            ocr_pages = sorted({item.page for item in ocr_items}) or list(range(1, max(doc.total_pages, 0) + 1))
            if ocr_items:
                add_audit_warning(
                    doc,
                    "ocr_used",
                    "OCR fallback was used because standard metric extraction was insufficient.",
                    ocr_pages=ocr_pages,
                )
            else:
                add_audit_warning(doc, "ocr_no_metrics", "OCR fallback ran but produced no metrics.")
            # 合并 OCR 结果到分组中
            existing_keys = {occ.canonical_key for occ in final}
            for ocr_item in ocr_items:
                if ocr_item.canonical_key not in existing_keys:
                    new_occ = MetricOccurrences(
                        canonical_key=ocr_item.canonical_key,
                        name=ocr_item.name,
                        primary=ocr_item,
                        all_occurrences=[ocr_item],
                    )
                    final.append(new_occ)
                    existing_keys.add(ocr_item.canonical_key)
                else:
                    # 补充到已有的 occurrences 中
                    occ = next(o for o in final if o.canonical_key == ocr_item.canonical_key)
                    occ.all_occurrences.append(ocr_item)
                    if ocr_item.value and occ.primary.value and abs(ocr_item.value) > abs(occ.primary.value):
                        occ.primary = ocr_item
            logger.info(f"[{side_label}] OCR 兜底后: {len(final)} 个指标")
        except Exception as e:
            add_audit_warning(doc, "ocr_failed", f"OCR fallback failed: {e}")
            logger.warning(f"[{side_label}] OCR 兜底失败: {e}")

    return final
