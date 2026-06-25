"""OCR 兜底提取 — 针对 PDF 字体编码损坏导致文本/表格抽取失败的情况。

支持双后端：
- PaddleOCR（优先，精度高，需安装 paddleocr）
- EasyOCR（备选，已安装即可用，支持繁体中文）

使用 PyMuPDF 渲染页面为图片 + OCR 识别文字，
替代损坏的 PDF 内嵌文本提取。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from ahcc.schemas import Currency, DataPoint, Evidence, ReportSide, LocalizedString

# 可选依赖检测
try:
    import easyocr
    _EASYOCR_AVAILABLE = True
except ImportError:
    _EASYOCR_AVAILABLE = False

try:
    from paddleocr import PaddleOCR
    _PADDLEOCR_AVAILABLE = True
except ImportError:
    _PADDLEOCR_AVAILABLE = False


# 核心财务指标 → 多语言关键词（含 OCR 常见错字变体）
_OCR_KEYWORDS: dict[str, list[str]] = {
    "revenue": ["營業收入", "营业收入", "惩訾收入", "營業收益", "营业收益"],
    "net_profit": ["淨利潤", "净利润", "浮利涠", "歸屬於", "归属于"],
    "net_profit_attributable": ["歸屬於母公司股東的淨利潤", "归属于母公司股东的净利润", "屬於本行股東的淨利潤", "本行股東的淨利潤"],
    "total_assets": ["資產總額", "资产总额", "瓷t额", "資產總計", "资产总计"],
    "total_liabilities": ["負債總額", "负债总额", "负债雒额", "負債總計", "负债合计"],
    "equity": ["股東權益總額", "股东权益总额", "股東權益合計", "股东权益合计", "股束榷益缌额", "股束權益缌额"],
    "share_capital": ["股本", "股數", "股数"],
    "operating_profit": ["營業利潤", "营业利润", "税前利潤", "税前利润", "稅前利潤"],
    "total_profit": ["利潤總額", "利润总额", "稅前利潤", "税前利润"],
    "eps_basic": ["基本每股收益", "基本每股盈利", "每股計息", "每股盈利"],
    "eps_diluted": ["攤薄每股收益", "摊薄每股收益", "稀釋每股收益", "稀释每股收益", "稀辉每股收益"],
    "operating_cash_flow": ["經營活動所得現金流量淨額", "经营活动现金流量净额", "經營活動產生的現金流量淨額"],
    "investing_cash_flow": ["投資活動所得現金流量淨額", "投资活动现金流量净额", "投資活動產生的現金流量淨額"],
    "financing_cash_flow": ["籌資活動所得現金流量淨額", "筹资活动现金流量净额", "融資活動產生的現金流量淨額"],
    "fixed_assets": ["固定資產", "固定资产", "物業及設備", "物业及设备"],
    "intangible_assets": ["無形資產", "无形资产"],
    "goodwill": ["商譽", "商誉"],
    "long_term_investments": ["長期股權投資", "长期股权投资"],
    "current_assets": ["流動資產合計", "流动资产合计", "流動資產總額"],
    "non_current_assets": ["非流動資產合計", "非流动资产合计"],
    "current_liabilities": ["流動負債合計", "流动负债合计"],
    "cash_equivalents": ["現金及現金等價物", "现金及现金等价物", "貨幣資金", "货币资金"],
    "income_tax": ["所得稅費用", "所得税费用", "所得稅", "所得税"],
    "cost_of_sales": ["營業支出", "营业支出", "營業成本", "营业成本"],
    "retained_earnings": ["留存收益", "未分配利潤", "未分配利润", "保留盈利"],
    "capital_reserve": ["資本公積", "资本公积"],
    "provisions": ["預計負債", "预计负债"],
    "lease_liabilities": ["租賃負債", "租赁负债"],
    "construction_in_progress": ["在建工程", "在建工程"],
    "receivables": ["應收賬款", "应收账款", "應收款項", "应收款项"],
    "inventory": ["存貨", "存货"],
    "short_term_borrowings": ["短期借款", "短期借款", "短期借貸"],
    "long_term_borrowings": ["長期借款", "长期借款", "長期借貸"],
    "payables": ["應付賬款", "应付账款", "應付款項", "应付款项"],
}


def is_scanned_pdf(pdf_path: str) -> bool:
    """启发式检测：抽取前 3 页文本，若可识别文字字符数 < 阈值则视为扫描件。"""
    try:
        import pdfplumber
    except ImportError:
        return False

    total_chars = 0
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages[:3], start=1):
                text = page.extract_text() or ""
                total_chars += len(text.strip())
    except Exception:
        return False

    return total_chars < 200


def _parse_number_ocr(text: str) -> Optional[float]:
    """从 OCR 文本中解析数值，支持千分位逗号和括号表示负数。"""
    if not text:
        return None
    text = text.strip().replace(" ", "").replace(",", "")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    try:
        val = float(text)
        return -val if negative else val
    except ValueError:
        return None


def _find_line_value(ocr_lines: list[tuple[str, float]], keyword: str, max_lines: int = 3) -> Optional[tuple[float, str]]:
    """在 OCR 结果中查找关键词所在行，并返回同行的数值。"""
    for i, (text, conf) in enumerate(ocr_lines):
        if keyword in text:
            nums_in_line = re.findall(r"[\(\)]?\d{1,3}(?:,\d{3})+(?:\.\d+)?[\(\)]?", text)
            if nums_in_line:
                val = _parse_number_ocr(nums_in_line[0])
                if val is not None:
                    return val, nums_in_line[0]
            for j in range(i + 1, min(i + 1 + max_lines, len(ocr_lines))):
                next_text = ocr_lines[j][0]
                nums = re.findall(r"[\(\)]?\d{1,3}(?:,\d{3})+(?:\.\d+)?[\(\)]?", next_text)
                if nums:
                    val = _parse_number_ocr(nums[0])
                    if val is not None:
                        return val, nums[0]
    return None


def _run_ocr_easyocr(img_path: str) -> list[tuple[str, float]]:
    """使用 EasyOCR 识别图片中的文字。"""
    reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
    result = reader.readtext(img_path)
    outputs: list[tuple[str, float]] = []
    for r in result:
        bbox, text, conf = r
        outputs.append((text, conf))
    return outputs


def _run_ocr_paddleocr(img_path: str) -> list[tuple[str, float]]:
    """使用 PaddleOCR 识别图片中的文字。"""
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    result = ocr.ocr(img_path, cls=True)
    outputs: list[tuple[str, float]] = []
    if result and result[0]:
        for line in result[0]:
            if line:
                text = line[1][0]
                conf = line[1][1]
                outputs.append((text, conf))
    return outputs


def ocr_page(pdf_path: str, page_num: int, dpi: int = 200) -> list[dict]:
    """对指定页跑 OCR，返回 [{text, bbox, confidence}] 列表。"""
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF 未安装，OCR 不可用")
        return []

    doc = fitz.open(pdf_path)
    if page_num > len(doc):
        doc.close()
        return []

    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=dpi)

    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = Path(tmpdir) / f"page_{page_num}.png"
        pix.save(str(img_path))

        if _PADDLEOCR_AVAILABLE:
            lines = _run_ocr_paddleocr(str(img_path))
        elif _EASYOCR_AVAILABLE:
            lines = _run_ocr_easyocr(str(img_path))
        else:
            logger.warning("没有可用的 OCR 引擎")
            doc.close()
            return []

    doc.close()

    outputs: list[dict] = []
    for text, conf in lines:
        outputs.append({"text": text, "confidence": conf})
    return outputs


def extract_keypoints_from_page_images(
    file_path: str,
    pages: list[int],
    side: ReportSide = ReportSide.H_SHARE,
    dpi: int = 200,
) -> list[DataPoint]:
    """对指定 PDF 页面进行 OCR，提取关键财务数据点。

    Args:
        file_path: PDF 文件路径
        pages: 要 OCR 的页码列表（1-based）
        side: 报告侧（A_SHARE / H_SHARE）
        dpi: 渲染分辨率

    Returns:
        提取的 DataPoint 列表
    """
    if not _EASYOCR_AVAILABLE and not _PADDLEOCR_AVAILABLE:
        logger.warning("没有可用的 OCR 引擎，跳过 OCR 兜底")
        return []

    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF 未安装，跳过 OCR 兜底")
        return []

    points: list[DataPoint] = []
    seen_keys: set[str] = set()
    doc = fitz.open(file_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        for page_num in pages:
            if page_num > len(doc):
                continue
            page = doc[page_num - 1]
            pix = page.get_pixmap(dpi=dpi)
            img_path = Path(tmpdir) / f"page_{page_num}.png"
            pix.save(str(img_path))

            if _PADDLEOCR_AVAILABLE:
                ocr_lines = _run_ocr_paddleocr(str(img_path))
            else:
                ocr_lines = _run_ocr_easyocr(str(img_path))

            for canonical_key, keywords in _OCR_KEYWORDS.items():
                if canonical_key in seen_keys:
                    continue
                for kw in keywords:
                    found = _find_line_value(ocr_lines, kw)
                    if found:
                        val, val_text = found
                        if abs(val) < 10 or (abs(val) < 100 and "." not in val_text):
                            continue
                        if abs(val) < 1 and "." in val_text:
                            continue

                        points.append(
                            DataPoint(
                                name=LocalizedString(zh=kw, en=canonical_key),
                                canonical_key=canonical_key,
                                value=val,
                                value_text=val_text,
                                unit=None,
                                currency=Currency.CNY,
                                period=None,
                                evidence=Evidence(
                                    side=side,
                                    page=page_num,
                                    bbox=None,
                                    snippet=f"{kw}: {val_text}",
                                    section=None,
                                ),
                                confidence=0.7,
                            )
                        )
                        seen_keys.add(canonical_key)
                        break

    doc.close()
    logger.info(f"OCR 兜底提取: {len(pages)} 页 → {len(points)} 个数据点")
    return points


# ============================================================
# Profile pipeline OCR 适配 — 返回 MetricItem
# ============================================================

_NUMBER_RE_OCR = re.compile(
    r"-?\d{1,3}(?:,\d{3}){1,5}(?:\.\d+)?|"  # 千分位格式
    r"-?\d{1,15}(?:\.\d+)?"                   # 纯数字格式
)


def _parse_number_from_ocr(text: str) -> Optional[float]:
    """从 OCR 文本中解析单个数值。"""
    if not text:
        return None
    text = text.strip()
    if text in ("—", "-", "–", "", "N/A", "n/a", "不适用"):
        return None
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]
    elif text.startswith("（") and text.endswith("）"):
        is_negative = True
        text = text[1:-1]
    cleaned = text.replace(",", "").replace(" ", "").replace("'", "")
    match = _NUMBER_RE_OCR.match(cleaned)
    if not match:
        return None
    try:
        val = float(match.group())
        return -val if is_negative else val
    except ValueError:
        return None


def _find_first_number_ocr(text: str, min_abs: float = 1000.0) -> tuple[Optional[float], Optional[str]]:
    """在 OCR 文本中找到第一个有效的财务金额。"""
    for match in _NUMBER_RE_OCR.finditer(text):
        raw = match.group()
        val = _parse_number_from_ocr(raw)
        if val is None:
            continue
        if 1990 <= val <= 2035 and "." not in raw:
            continue
        if 1 <= val <= 50 and "," not in raw and "." not in raw:
            continue
        end_pos = match.end()
        if end_pos < len(text) and text[end_pos] == "%":
            continue
        if "," in raw or abs(val) >= min_abs:
            return val, raw
    #  fallback: 返回第一个数字
    for match in _NUMBER_RE_OCR.finditer(text):
        raw = match.group()
        val = _parse_number_from_ocr(raw)
        if val is not None:
            return val, raw
    return None, None


def _looks_like_label_ocr(text: str, side: ReportSide) -> bool:
    """OCR 文本是否像财务标签。"""
    text = text.strip()
    if not text or len(text) < 2:
        return False
    # 排除纯数字行
    if re.match(r"^[\d,\(\)\(\)\s\-%]+$", text):
        return False
    # 中文字符或 3+ 英文字母
    has_chinese = bool(re.search(r"[一-龥]", text))
    has_english_word = len(re.findall(r"[a-zA-Z]{3,}", text)) > 0
    return has_chinese or has_english_word


def _extract_lines_from_ocr(ocr_lines: list[tuple[str, float]]) -> list[str]:
    """将 OCR 行输出合并为文本列表。"""
    return [text for text, conf in ocr_lines if text.strip()]


def _extract_metrics_from_ocr_lines(
    lines: list[str],
    page_num: int,
    side: ReportSide,
    file_path: str,
    unit: Optional[str] = None,
    currency: Optional[Currency] = None,
) -> list:
    """从 OCR 文本行中提取 MetricItem（适配 profile pipeline）。

    策略：
    1. 关键词匹配：遍历 _OCR_KEYWORDS + glossary，找到关键词后提取同行/下行数字
    2. 行模式匹配：尝试 "标签  数字" 表格行模式
    """
    from ahcc.align.glossary import glossary, to_simplified
    from ahcc.profile.models import MetricItem
    from ahcc.schemas import Evidence, LocalizedString

    items: list[MetricItem] = []
    seen_keys: set[str] = set()

    # 策略 1：关键词搜索
    all_keywords: dict[str, list[str]] = {}
    # 合并 _OCR_KEYWORDS
    for k, v in _OCR_KEYWORDS.items():
        all_keywords.setdefault(k, []).extend(v)
    # 合并 glossary
    for key in glossary.all_canonical_keys():
        entry = glossary.get_entry(key)
        if entry:
            forms = [f for f in [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases] if f]
            all_keywords.setdefault(key, []).extend(forms)

    for canonical_key, keywords in all_keywords.items():
        if canonical_key in seen_keys:
            continue
        for kw in keywords:
            if not kw:
                continue
            for i, line in enumerate(lines):
                # 中文用简化字匹配，英文用原始大小写+小写
                line_cmp = to_simplified(line) if side == ReportSide.A_SHARE else line
                kw_cmp = to_simplified(kw) if side == ReportSide.A_SHARE else kw
                if kw_cmp not in line_cmp and kw_cmp.lower() not in line_cmp.lower():
                    continue

                # 关键词命中，提取数字
                # 优先同行，其次下行
                val, val_text = None, None
                for j in range(i, min(i + 4, len(lines))):
                    val, val_text = _find_first_number_ocr(lines[j], min_abs=0)
                    if val is not None and abs(val) >= 10:
                        break

                if val is None or abs(val) < 10:
                    continue
                # 排除年份
                if 1990 <= val <= 2035 and "." not in (val_text or ""):
                    continue

                entry = glossary.get_entry(canonical_key)
                name = LocalizedString(
                    zh=entry.zh_cn if entry else kw,
                    en=entry.en if entry else canonical_key,
                )

                items.append(
                    MetricItem(
                        canonical_key=canonical_key,
                        name=name,
                        value=val,
                        value_text=val_text,
                        unit=unit,
                        currency=currency,
                        page=page_num,
                        evidence=Evidence(
                            side=side,
                            page=page_num,
                            bbox=None,
                            snippet=f"[OCR {kw}] {val_text}",
                            section=None,
                        ),
                        confidence=0.65,
                        source="generic_pattern",
                    )
                )
                seen_keys.add(canonical_key)
                break
            if canonical_key in seen_keys:
                break

    # 策略 2：表格行模式 "标签    数字"
    # 扫描每一行，若前半部分像标签、后半部分像数字，提取
    for i, line in enumerate(lines):
        # 至少两个空格分隔的列
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 2:
            continue
        label_part = parts[0].strip()
        num_part = parts[-1].strip()
        if not _looks_like_label_ocr(label_part, side):
            continue
        val, val_text = _find_first_number_ocr(num_part, min_abs=0)
        if val is None or abs(val) < 10:
            continue

        label_simplified = to_simplified(label_part) if side == ReportSide.A_SHARE else label_part
        canonical_key = glossary.lookup(label_part) or glossary.lookup(label_simplified)
        conf = 0.7
        if not canonical_key:
            canonical_key = re.sub(r"[^\w一-鿿]", "_", label_simplified.strip().lower())
            canonical_key = re.sub(r"_+", "_", canonical_key).strip("_")
            conf = 0.4
        if not canonical_key:
            continue
        if canonical_key in seen_keys:
            continue

        entry = glossary.get_entry(canonical_key)
        name = LocalizedString(
            zh=entry.zh_cn if entry else label_part,
            en=entry.en if entry else "",
        )

        items.append(
            MetricItem(
                canonical_key=canonical_key,
                name=name,
                value=val,
                value_text=val_text,
                unit=unit,
                currency=currency,
                page=page_num,
                evidence=Evidence(
                    side=side,
                    page=page_num,
                    bbox=None,
                    snippet=f"[OCR table] {label_part} = {val_text}",
                    section=None,
                ),
                confidence=conf,
                source="generic_pattern",
            )
        )
        seen_keys.add(canonical_key)

    return items


def extract_metrics_via_ocr(
    file_path: str,
    side: ReportSide,
    max_pages: int | None = None,
    dpi: int = 200,
    unit: Optional[str] = None,
    currency: Optional[Currency] = None,
) -> list:
    """对 PDF 进行 OCR，提取 MetricItem（供 profile pipeline 调用）。

    Args:
        file_path: PDF 路径
        side: A_SHARE / H_SHARE
        max_pages: Optional debug cap. None scans the full report.
        dpi: 渲染分辨率
        unit: 默认单位
        currency: 默认币种

    Returns:
        MetricItem 列表
    """
    if not _EASYOCR_AVAILABLE and not _PADDLEOCR_AVAILABLE:
        logger.warning("没有可用的 OCR 引擎，跳过 OCR 兜底")
        return []

    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF 未安装，跳过 OCR 兜底")
        return []

    doc = fitz.open(file_path)
    total_pages = len(doc)
    end_page = total_pages if max_pages is None else min(total_pages, max_pages)
    pages_to_ocr = list(range(1, end_page + 1))

    logger.info(f"[OCR] 开始兜底提取: {file_path}, 页数={total_pages}, OCR页={pages_to_ocr[-1] if pages_to_ocr else 0}")

    all_items: list = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for page_num in pages_to_ocr:
            page = doc[page_num - 1]
            pix = page.get_pixmap(dpi=dpi)
            img_path = Path(tmpdir) / f"page_{page_num}.png"
            pix.save(str(img_path))

            if _PADDLEOCR_AVAILABLE:
                ocr_lines = _run_ocr_paddleocr(str(img_path))
            else:
                ocr_lines = _run_ocr_easyocr(str(img_path))

            lines = _extract_lines_from_ocr(ocr_lines)
            items = _extract_metrics_from_ocr_lines(
                lines, page_num, side, file_path, unit=unit, currency=currency
            )
            all_items.extend(items)

    doc.close()
    logger.info(f"[OCR] 兜底提取完成: {len(all_items)} 个指标")
    return all_items
