"""A 股 PDF 解析（P2 实现）— pdfplumber 主，camelot/PPStructure 处理复杂表格。

实现步骤：
1. pdfplumber 打开 PDF，逐页提取 text + bbox + table
2. 用 ahcc/parser/table_extract.py 抽取财务表格
3. 段落级文本切分，标记 section（如"合并资产负债表"、"管理层讨论与分析"）
4. 输出 ReportDocument
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from ahcc.config import settings
from ahcc.parser.audit import attach_audit, build_extraction_audit
from ahcc.parser.table_extract import (
    extract_tables_camelot,
    extract_tables_ppstructure,
    merge_tables,
)
from ahcc.schemas import (
    Currency,
    Evidence,
    FinancialTable,
    Language,
    LocalizedString,
    ReportDocument,
    ReportSide,
    TableCell,
    TextSegment,
)


# 章节检测关键词（A 股年报常见章节）
SECTION_KEYWORDS = {
    "合并资产负债表": "bs",
    "合并利润表": "pl",
    "合并现金流量表": "cf",
    "合并所有者权益变动表": "equity",
    "合并股东权益变动表": "equity",
    "管理层讨论与分析": "mda",
    "董事会报告": "directors_report",
    "重要事项": "significant_events",
    "关联交易": "related_party",
    "关联方": "related_party",
    "会计政策": "accounting_policy",
    "重要会计估计": "accounting_estimate",
    "附注": "notes",
    "财务报表": "financial_statements",
    "公司概况": "company_profile",
    "公司治理": "corporate_governance",
    "环境与社会责任": "esg",
    "股份变动": "share_changes",
    "优先股": "preference_shares",
    "债券": "bonds",
    "商誉": "goodwill",
    "研发支出": "rnd",
    "所得税": "income_tax",
    "每股收益": "eps",
    "分部报告": "segment_report",
    "永续债": "perpetual_bond",
    "政府补助": "government_grant",
    "租赁": "leases",
    "金融工具": "financial_instruments",
    "收入": "revenue",
    "存货": "inventories",
    "固定资产": "ppe",
    "在建工程": "construction_in_progress",
    "无形资产": "intangible_assets",
    "投资性房地产": "investment_property",
    "长期股权投资": "long_term_investment",
    "应付职工薪酬": "employee_benefits",
    "预计负债": "provisions",
    "资本公积": "capital_reserve",
    "盈余公积": "surplus_reserve",
    "未分配利润": "retained_earnings",
    "少数股东权益": "minority_interest",
    "营业收入": "revenue",
    "营业成本": "cogs",
    "销售费用": "selling_expenses",
    "管理费用": "admin_expenses",
    "研发费用": "rnd_expenses",
    "财务费用": "finance_expenses",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "net_profit_attributable",
    "基本每股收益": "basic_eps",
    "稀释每股收益": "diluted_eps",
    "经营活动现金流量": "cfo",
    "投资活动现金流量": "cfi",
    "筹资活动现金流量": "cff",
    "现金及现金等价物": "cash_equivalents",
    "货币资金": "cash",
    "应收账款": "receivables",
    "预付款项": "prepayments",
    "其他应收款": "other_receivables",
    "存货": "inventories",
    "流动资产合计": "current_assets",
    "非流动资产合计": "non_current_assets",
    "资产总计": "total_assets",
    "短期借款": "short_term_borrowings",
    "应付账款": "payables",
    "预收款项": "advance_receipts",
    "合同负债": "contract_liabilities",
    "应付职工薪酬": "employee_payables",
    "应交税费": "tax_payables",
    "其他应付款": "other_payables",
    "一年内到期的非流动负债": "current_portion_non_current",
    "流动负债合计": "current_liabilities",
    "非流动负债合计": "non_current_liabilities",
    "负债合计": "total_liabilities",
    "实收资本": "share_capital",
    "股本": "share_capital",
    "其他权益工具": "other_equity_instruments",
    "其他综合收益": "oci",
    "所有者权益合计": "total_equity",
    "负债和所有者权益总计": "total_liabilities_equity",
    # 证券行业特有术语
    "手续费及佣金收入": "fee_commission_income",
    "手续费及佣金支出": "fee_commission_expense",
    "利息收入": "interest_income",
    "利息支出": "interest_expense",
    "融出资金": "margin_loans",
    "买入返售金融资产": "reverse_repurchase",
    "卖出回购金融资产款": "repo_payable",
    "交易性金融资产": "trading_assets",
    "债权投资": "debt_investment",
    "其他债权投资": "other_debt_investment",
    "其他权益工具投资": "other_equity_investment",
    "金融负债": "financial_liabilities",
    "代理买卖证券款": "agency_trading_payable",
    "应付短期融资款": "short_term_financing",
    "应付债券": "bonds_payable",
    "衍生金融工具": "derivative_instruments",
    "公允价值变动": "fv_change",
    "投资收益": "investment_income",
    "资产减值损失": "impairment_loss",
    "信用减值损失": "credit_impairment",
    "其他资产": "other_assets",
    "其他负债": "other_liabilities",
    "代理承销证券款": "agency_underwriting",
    "证券承销业务": "underwriting",
    "资产管理业务": "asset_management",
    "融资融券业务": "margin_trading",
    "股票质押回购": "stock_pledge_repo",
    "期货经纪业务": "futures_brokerage",
}


# 财务关键词（用于页面预筛选）
_FINANCIAL_PAGE_KEYWORDS = [
    "资产负债表", "利润表", "现金流量表", "所有者权益变动表",
    "附注", "财务报表", "合并", "营业收入", "净利润", "总资产",
    "流动资产", "非流动资产", "负债", "股本", "资本公积",
    "手续费及佣金", "利息收入", "融出资金", "买入返售",
    "交易性金融资产", "代理买卖证券", "应付债券",
    # 银行监管指标页（资本充足率/流动性覆盖率等季报表也是数值核对对象）
    "资本充足率", "流动性覆盖率", "净稳定资金", "杠杆率", "监管指标",
]

_A_CORE_SECTIONS = ("bs", "pl", "cf", "equity")
_A_KEY_NOTE_SECTIONS = ("financial_instruments", "related_party", "segment_report", "income_tax", "eps")

_A_CORE_TITLE_PATTERNS = {
    "bs": (
        "资产负债表",
        "合并资产负债表",
        "财务状况表",
        "合并财务状况表",
    ),
    "pl": (
        "利润表",
        "合并利润表",
        "损益表",
        "合并损益表",
        "综合收益表",
        "合并综合收益表",
    ),
    "cf": (
        "现金流量表",
        "合并现金流量表",
        "现金流动表",
    ),
    "equity": (
        "所有者权益变动表",
        "合并所有者权益变动表",
        "股东权益变动表",
        "合并股东权益变动表",
        "权益变动表",
    ),
}

_A_CORE_SUPPORTING_PATTERNS = {
    "bs": (
        "流动资产", "非流动资产", "资产总计", "资产合计",
        "流动负债", "非流动负债", "负债合计",
        "所有者权益", "股东权益", "实收资本", "资本公积",
        "盈余公积", "未分配利润", "少数股东权益",
        "货币资金", "应收账款", "存货", "固定资产",
        "无形资产", "商誉", "长期股权投资",
        "短期借款", "长期借款", "应付债券",
    ),
    "pl": (
        "营业收入", "营业成本", "营业利润", "利润总额",
        "净利润", "归属于母公司股东的净利润",
        "基本每股收益", "稀释每股收益",
        "销售费用", "管理费用", "研发费用", "财务费用",
        "所得税费用", "其他综合收益", "综合收益总额",
        "手续费及佣金收入", "利息收入", "利息支出",
        "投资收益", "公允价值变动收益",
    ),
    "cf": (
        "经营活动产生的现金流量", "投资活动产生的现金流量", "筹资活动产生的现金流量",
        "经营活动现金流量", "投资活动现金流量", "筹资活动现金流量",
        "现金及现金等价物净增加额", "现金及现金等价物余额",
        "销售商品、提供劳务收到的现金",
        "购买商品、接受劳务支付的现金",
        "收回投资收到的现金", "取得借款收到的现金",
    ),
    "equity": (
        "实收资本", "资本公积", "盈余公积", "未分配利润",
        "所有者权益合计", "股东权益合计",
        "上年年末余额", "本年年初余额", "本年增减变动金额",
        "会计政策变更", "前期差错更正",
        "利润分配", "提取盈余公积", "对所有者的分配",
    ),
}


def _infer_a_core_sections_from_text(text: str) -> set[str]:
    """从文本推断 A 股核心报表章节（bs/pl/cf/equity）。"""
    if not text:
        return set()
    normalized = re.sub(r"\s+", " ", text.lower())
    inferred: set[str] = set()
    for section, patterns in _A_CORE_TITLE_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            inferred.add(section)
    for section, patterns in _A_CORE_SUPPORTING_PATTERNS.items():
        hits = sum(1 for pattern in patterns if pattern in normalized)
        if section == "cf" and hits >= 2:
            inferred.add(section)
        elif section in {"bs", "pl", "equity"} and hits >= 3:
            inferred.add(section)
    return inferred


_A_PDFPLUMBER_TEXT_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "edge_min_length": 2,
    "min_words_vertical": 1,
    "min_words_horizontal": 1,
    "text_tolerance": 5,
}


def _is_financial_page(page_text: str) -> bool:
    """判断页面是否可能包含财务表格（快速预筛选）。"""
    if not page_text:
        return False
    text = page_text[:500]  # 只看前500字，提高效率
    return any(kw in text for kw in _FINANCIAL_PAGE_KEYWORDS)


def _looks_like_a_table_page(page_text: str) -> bool:
    if not page_text:
        return False
    text = re.sub(r"\s+", " ", page_text)
    if len(text) < 80:
        return False
    digit_count = sum(ch.isdigit() for ch in text)
    digit_ratio = digit_count / max(len(text), 1)
    line_count = page_text.count("\n") + 1
    table_markers = ("项目", "单位", "2025年", "2024年", "资产", "负债", "现金流量", "利润")
    if any(marker in text for marker in table_markers) and digit_ratio >= 0.05:
        return True
    return digit_ratio >= 0.2 and line_count >= 10


def _is_useful_a_raw_table(raw_table: list[list[str | None]]) -> bool:
    if not raw_table:
        return False
    rows = len(raw_table)
    cols = max((len(row or []) for row in raw_table), default=0)
    if rows < 2 or cols < 2:
        return False
    flat = [str(cell or "").strip() for row in raw_table for cell in (row or [])]
    non_empty = [cell for cell in flat if cell]
    numeric_cells = [cell for cell in non_empty if re.search(r"\d", cell)]
    return len(non_empty) >= 4 and len(numeric_cells) >= 2


def _extract_pymupdf_page_texts(file_path: str) -> dict[int, str]:
    try:
        import fitz
    except ImportError:
        return {}

    result: dict[int, str] = {}
    doc = None
    try:
        doc = fitz.open(file_path)
        for idx, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            if text.strip():
                result[idx] = text
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"A share PyMuPDF text supplement skipped: {exc}")
        return {}
    finally:
        if doc is not None:
            doc.close()
    return result


def _compact_text_for_merge(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _merge_page_texts(pdfplumber_text: str, pymupdf_text: str) -> str:
    left = (pdfplumber_text or "").strip()
    right = (pymupdf_text or "").strip()
    if not right:
        return left
    if not left:
        return right

    left_compact = _compact_text_for_merge(left)
    right_compact = _compact_text_for_merge(right)
    if right in left or right_compact in left_compact:
        return left
    if left in right or left_compact in right_compact:
        return right
    return f"{left}\n\n{right}"


# 快速路径的期间词（数字+年/月/日/季度）：这类词能被 _parse_number 解析出数字
# （"2025年12月31日"→2025.0），但语义是列头不是数值 —— 必须先于数值判定排除。
_FASTPATH_PERIOD_WORD_RE = re.compile(r"\d")
_FASTPATH_PERIOD_MARK_RE = re.compile(r"年|月|日|季度|year|month|quarter|date", re.IGNORECASE)
_FASTPATH_CJK_RE = re.compile(r"[一-龥]")
_FASTPATH_BARE_NUMBER_RE = re.compile(r"\d{1,4}")
# 表头行最多保留的视觉行数（两级表头 + 跨行折行 + 单位行；光大 p17 实测 3 行）
_FASTPATH_MAX_HEADER_BANDS = 3


def _fastpath_word_classifier():
    """返回 (is_data_band, loose_numeric_words) 二件套。

    - 严格值词：可解析为数字、不含 CJK、且非期间词（"7,165,319"）。
    - 期间词：含数字且含年/月/日/季度（"2025年12月31日"），或裸数字紧邻
      年/月/日开头的右邻词（A 股 PDF 常见"2025 年"被 get_text 按空格拆开）。
    - 宽松数值词：_parse_number 可解析（含期间词与"921.01亿元"类单位内联值）。
    数据行 = 有严格值词，或（有宽松数值词且无期间词，即单位内联值行）。
    """
    from ahcc.profile.extract_metrics import _parse_number  # 延迟导入，避免循环依赖

    def word_text(w) -> str:
        return str(w[4]).strip()

    def _period_mask(xsorted: list) -> list[bool]:
        mask = [False] * len(xsorted)
        for i, w in enumerate(xsorted):
            text = word_text(w)
            if text and _FASTPATH_PERIOD_WORD_RE.search(text) and _FASTPATH_PERIOD_MARK_RE.search(text):
                mask[i] = True
            elif text and _FASTPATH_BARE_NUMBER_RE.fullmatch(text):
                # 日期碎片："2025" + 右邻"年比2024"/"年12月31日"（间距 ≤6pt）
                nxt = xsorted[i + 1] if i + 1 < len(xsorted) else None
                if nxt is not None:
                    nxt_text = word_text(nxt)
                    if nxt_text and nxt_text[0] in "年月日" and (nxt[0] - w[2]) <= 6:
                        mask[i] = True
        return mask

    def loose_numeric_words(band: list) -> list:
        return [w for w in band if _parse_number(word_text(w)) is not None]

    def is_data_band(band: list) -> bool:
        xsorted = sorted(band, key=lambda w: w[0])
        mask = _period_mask(xsorted)
        has_period = any(mask)
        for i, w in enumerate(xsorted):
            text = word_text(w)
            if (
                not mask[i]
                and _parse_number(text) is not None
                and not _FASTPATH_CJK_RE.search(text)
            ):
                return True  # 有严格值词 → 数据行
        loose = [w for w in xsorted if _parse_number(word_text(w)) is not None]
        if loose:
            # 单位内联值行（"营业收入 921.01亿元"）：有数值词但无期间词
            return not has_period
        return False

    return is_data_band, loose_numeric_words


def _band_y0(band: list) -> float:
    return min(w[1] for w in band)


# 组标签行样式：无数字且短的居中标签（"每股计（人民币元）"/"盈利能力指标（%）"）
_GROUP_LABEL_MAX_CHARS = 30
# 数据行内紧贴数值的单位后缀词（"-0.06 个百分点"），贴回前一个数值单元格
_UNIT_SUFFIX_TOKENS = {
    "个百分点", "個百分點", "百分点", "百分點", "%", "％",
    "万元", "萬元", "亿元", "億元", "百万元", "百萬元", "千元",
}


def _split_table_segments(row_bands: list[list], data_bands: list[list]) -> list[dict]:
    """把页面切成表格段：大间隙（>2.5×众数行距）或组标签行为界。

    多表页（光大 p20：资本充足率/杠杆率/LCR 三表）与多节页（p17：经营业绩/
    每股计/规模指标/盈利能力各节列几何不同）都必须切段，否则数值错误归列。
    返回 list[dict(prefix=非数据行, data=数据行)]：
    - prefix：上一段末行与本段首数据行之间的非数据行（组标签/注释/页眉）；
      与前段合并建表时转为表内行，独立建表时作表头候选；
    - 段内非组标签行（注释等）直接丢弃（与旧版只收数值行的行为一致）。
    """
    data_ids = {id(b) for b in data_bands}
    ordered = sorted(row_bands, key=_band_y0)

    # 众数行距（并列取最小，下限 10pt 防带内抖动）
    data_ordered = sorted(data_bands, key=_band_y0)
    gaps = [_band_y0(b) - _band_y0(a) for a, b in zip(data_ordered, data_ordered[1:])]
    counts: dict[int, int] = {}
    for g in gaps:
        if g > 0:
            counts[round(g)] = counts.get(round(g), 0) + 1
    if counts:
        max_count = max(counts.values())
        mode_gap = max(10.0, float(min(r for r, c in counts.items() if c == max_count)))
    else:
        mode_gap = 20.0
    threshold = 2.5 * mode_gap

    segments: list[dict] = []
    current_data: list[list] = []
    current_prefix: list[list] = []
    prev_data: list | None = None

    def _close() -> None:
        nonlocal current_data, current_prefix
        if current_data:
            segments.append({"prefix": current_prefix, "data": current_data})
        current_data = []
        current_prefix = []

    for band in ordered:
        if id(band) in data_ids:
            if prev_data is not None and _band_y0(band) - _band_y0(prev_data) > threshold:
                _close()
            current_data.append(band)
            prev_data = band
        else:
            text = "".join(str(w[4]).strip() for w in band)
            is_group_label = (
                bool(current_data)
                and not any(ch.isdigit() for ch in text)
                and 0 < len(text) <= _GROUP_LABEL_MAX_CHARS
            )
            if is_group_label:
                _close()
            if not current_data:
                current_prefix.append(band)

    _close()
    return segments


def _segment_col_centers(segment: dict, loose_numeric_words) -> list[float]:
    """段内数值词的 x 中心聚类（30pt 内同列，均值更新）。"""
    num_centers = sorted(
        (w[0] + w[2]) / 2 for band in segment["data"] for w in loose_numeric_words(band)
    )
    col_centers: list[float] = []
    for xc in num_centers:
        if col_centers and abs(xc - col_centers[-1]) <= 30:
            col_centers[-1] = (col_centers[-1] + xc) / 2
        else:
            col_centers.append(xc)
    return col_centers


def _geometry_matches(centers_a: list[float], centers_b: list[float], tol: float = 20.0) -> bool:
    """两段列几何是否一致（列数相同且逐列中心偏差 ≤tol）——一致则合并回一张表。"""
    if not centers_a or len(centers_a) != len(centers_b):
        return False
    return all(abs(a - b) <= tol for a, b in zip(centers_a, centers_b))


def _collect_header_bands(
    row_bands: list[list],
    region: list[list],
    col_centers: list[float],
    min_y0: float = -1.0,
) -> list[list]:
    """区域首个数据行之上的紧邻表头样行（≤3 行，自上而下返回）。

    min_y0：上一子表区域的末行 y0 —— 表头收集不得越过它（防把上一表的
    数据行/表头当成下一表的表头）。防混入闸门：与下行垂直距离 ≤1.8×行距
    （防页眉/段落）、总文本 ≤60 字、标签区右侧至少一个词对齐列中心。
    """
    if not region or not col_centers:
        return []

    first_data_y0 = _band_y0(region[0])
    bands_above = [
        b for b in row_bands if min_y0 < _band_y0(b) < first_data_y0 - 0.5
    ]
    if not bands_above:
        return []

    # 行距：区域内数据行间 y0 差的中位数（前 10 行），退化时用 20pt 兜底
    data_y0s = [_band_y0(b) for b in region[:10]]
    diffs = [b - a for a, b in zip(data_y0s, data_y0s[1:]) if b > a]
    pitch = sorted(diffs)[len(diffs) // 2] if diffs else 20.0

    min_gap = min(
        (col_centers[i + 1] - col_centers[i] for i in range(len(col_centers) - 1)),
        default=40.0,
    )
    align_tolerance = max(min_gap * 0.6, 12.0)
    label_boundary = col_centers[0] - min_gap * 0.5

    accepted: list[list] = []
    below_y0 = first_data_y0
    for band in reversed(bands_above):
        if len(accepted) >= _FASTPATH_MAX_HEADER_BANDS:
            break
        total_text = "".join(str(w[4]).strip() for w in band)
        if len(total_text) > 60:
            break
        if below_y0 - _band_y0(band) > 1.8 * pitch:
            break  # 与表体脱节（页眉/上一段落/小节标题），不再向上找
        # 对齐校验：标签列右侧至少一个词落在列中心附近（日期词/金额词都对齐列）；
        # 纯标签/单位行（全在标签区）跳过校验
        right_words = [w for w in band if (w[0] + w[2]) / 2 >= label_boundary]
        if right_words:
            aligned = any(
                abs((w[0] + w[2]) / 2 - cc) <= align_tolerance
                for w in right_words
                for cc in col_centers
            )
            if not aligned:
                break  # 段落文本按行宽排布、不对齐列 —— 不是表头
        accepted.append(band)
        below_y0 = _band_y0(band)

    accepted.reverse()  # 自上而下
    return accepted


def _reconstruct_textlayer_tables(
    file_path: str, page_texts: dict[int, str]
) -> tuple[list[FinancialTable], set[int]]:
    """用 fitz 词坐标在财务页上重建文本层表格（快速路径专用，不开 pdfplumber）。

    年报主表/摘要在 PDF 里是规则排布的文本网格：标签列在左、数值列在右。这里
    按 y 坐标把词聚成行、按 x 坐标把词聚成列（列中心聚类），拼出对齐的
    FinancialTable —— 供表格行孪生比对（edit/swap 检出）在 A 侧快速路径下也有
    `doc.tables` 可用。每页按垂直间隙切成子表区域（多表页各表列几何不同），
    区域内保留 ≤3 行表头（旧版整行丢弃，列键全空）。宁缺毋滥：只有财务关键词
    页才重建。
    """
    try:
        import fitz
    except ImportError:
        return [], set()

    tables: list[FinancialTable] = []
    table_pages: set[int] = set()
    try:
        doc = fitz.open(file_path)
    except Exception:
        return [], set()

    try:
        for page_num in sorted(page_texts):
            if not _is_financial_page(page_texts.get(page_num, "")):
                continue
            page_idx = page_num - 1
            if page_idx < 0 or page_idx >= doc.page_count:
                continue
            try:
                words = doc[page_idx].get_text("words")
            except Exception:
                continue
            if not words:
                continue

            # 1) 按 y 聚成视觉行（容差 4pt）
            row_bands: list[list] = []
            for w in sorted(words, key=lambda w: (w[1], w[0])):
                placed = False
                for band in row_bands:
                    if abs(band[0][1] - w[1]) <= 4:
                        band.append(w)
                        placed = True
                        break
                if not placed:
                    row_bands.append([w])
            row_bands = [sorted(b, key=lambda w: w[0]) for b in row_bands]

            # 2) 数据行 = 有严格值词（无 CJK 的数字）或单位内联值行；日期词行是表头
            is_data_band, loose_numeric_words = _fastpath_word_classifier()
            data_bands = [band for band in row_bands if is_data_band(band)]
            if not data_bands:
                continue

            # 3) 切段（大间隙/组标签），同几何段合并成组，逐组建表
            segments = _split_table_segments(row_bands, data_bands)
            groups: list[dict] = []
            for segment in segments:
                centers = _segment_col_centers(segment, loose_numeric_words)
                if not centers:
                    continue
                if groups and _geometry_matches(groups[-1]["centers"], centers):
                    groups[-1]["segments"].append(segment)
                else:
                    groups.append({"centers": centers, "segments": [segment]})

            prev_group_last_y0 = -1.0
            for group_idx, group in enumerate(groups, start=1):
                first_segment = group["segments"][0]
                last_segment = group["segments"][-1]
                group_last_y0 = _band_y0(last_segment["data"][-1])
                col_centers = group["centers"]
                ncols = len(col_centers)
                if ncols < 1:
                    prev_group_last_y0 = group_last_y0
                    continue

                # 表头行（从组首段数据行之上收集，不得越过上一组末行）
                header_bands: list[list] = []
                if getattr(settings, "fast_path_header_rows", True):
                    header_bands = _collect_header_bands(
                        row_bands, first_segment["data"], col_centers, min_y0=prev_group_last_y0
                    )

                # 组准入：≥2 数据行，或带表头的少行表（LCR 单行季报表）；
                # cells ≥8 门槛兜底防噪声
                total_data_bands = sum(len(s["data"]) for s in group["segments"])
                if total_data_bands < 2 and not header_bands:
                    prev_group_last_y0 = group_last_y0
                    continue

                # 4) 拼单元格：表头行在前（按列中心归位）；组内其余行按 y 序
                #    （合并进来的组标签行也作表内行）
                cells: list[TableCell] = []
                min_gap = min(
                    (col_centers[i + 1] - col_centers[i] for i in range(ncols - 1)),
                    default=40.0,
                )
                label_boundary = col_centers[0] - min_gap * 0.5

                def _col_for_xc(xc: float) -> int:
                    return 1 + min(range(ncols), key=lambda i: abs(xc - col_centers[i]))

                def _append_header_cells(band: list, row_idx: int) -> None:
                    buckets: dict[int, list[str]] = {}
                    for w in sorted(band, key=lambda w: w[0]):
                        text = str(w[4]).strip()
                        if not text:
                            continue
                        xc = (w[0] + w[2]) / 2
                        col = 0 if xc < label_boundary else _col_for_xc(xc)
                        buckets.setdefault(col, []).append(text)
                    for col in sorted(buckets):
                        parts = buckets[col]
                        joined = (
                            "".join(parts)
                            if all(re.search(r"[一-龥]", p) for p in parts)
                            else " ".join(parts)
                        )
                        cells.append(TableCell(row=row_idx, col=col, text=joined, is_header=True))

                for h_idx, band in enumerate(header_bands):
                    _append_header_cells(band, h_idx)

                # 组内内容行 = 首段数据行 + 后续段的（prefix 组标签行 + 数据行），按 y 序
                content_bands = list(first_segment["data"])
                for segment in group["segments"][1:]:
                    content_bands.extend(segment["prefix"])
                    content_bands.extend(segment["data"])
                content_bands.sort(key=_band_y0)

                for d_idx, band in enumerate(content_bands, start=len(header_bands)):
                    from ahcc.profile.extract_metrics import _parse_number  # noqa: F811 已在分类器延迟导入

                    label_parts: list[str] = []
                    last_numeric_cell: TableCell | None = None
                    last_numeric_x1 = -1.0
                    pending_cells: list[TableCell] = []
                    for w in sorted(band, key=lambda w: w[0]):
                        text = str(w[4]).strip()
                        if not text:
                            continue
                        xc = (w[0] + w[2]) / 2
                        if (
                            _parse_number(text) is not None
                            and not _FASTPATH_CJK_RE.search(text)
                        ):
                            cell = TableCell(row=d_idx, col=_col_for_xc(xc), text=text)
                            pending_cells.append(cell)
                            last_numeric_cell = cell
                            last_numeric_x1 = w[2]
                        elif xc < label_boundary:
                            label_parts.append(text)
                        elif (
                            text in _UNIT_SUFFIX_TOKENS
                            and last_numeric_cell is not None
                            and w[0] - last_numeric_x1 <= 40
                        ):
                            # 单位后缀（"-0.06 个百分点"）：贴回前一个数值单元格
                            last_numeric_cell.text += text
                        else:
                            # 段内独立文本词（"不适用"/"-"）：按列归位成文本单元格
                            pending_cells.append(
                                TableCell(row=d_idx, col=_col_for_xc(xc), text=text)
                            )
                    if label_parts:
                        cells.append(TableCell(row=d_idx, col=0, text="".join(label_parts)))
                    cells.extend(pending_cells)

                if len(cells) < 8:
                    prev_group_last_y0 = group_last_y0
                    continue

                tables.append(
                    FinancialTable(
                        table_id=f"A_p{page_num:03d}_textlayer_t{group_idx:02d}",
                        title=LocalizedString(
                            zh=f"第{page_num}页文本层表格", en=f"p{page_num} text-layer grid"
                        ),
                        page=page_num,
                        bbox=(0.0, 0.0, 1.0, 1.0),
                        cells=cells,
                        currency=Currency.CNY,
                    )
                )
                table_pages.add(page_num)
                prev_group_last_y0 = group_last_y0
    finally:
        doc.close()

    return tables, table_pages


def _build_fast_pymupdf_a_doc(file_path: str, page_texts: dict[int, str]) -> ReportDocument:
    texts: list[TextSegment] = []
    scanned_pages = set(page_texts)
    text_pages: set[int] = set()
    for page_num in sorted(page_texts):
        page_text = (page_texts.get(page_num) or "").strip()
        if not page_text:
            continue
        text_pages.add(page_num)
        texts.extend(_split_text(page_text, page_num))

    total_pages = max(page_texts, default=0)
    unit, currency = _detect_unit_currency(texts)

    # 轻量文本层表格重建：快速路径本不跑 pdfplumber，但「表格行孪生比对」（edit/swap
    # 注入的主力检出引擎）依赖 doc.tables。注入评估的对照是同一文档的干净版本，页级
    # 文本层网格足以让孪生引擎在篡改页命中 —— 这里用 fitz 词坐标做行列重建，不开
    # pdfplumber，保住快速路径的性能。
    tables, table_page_nums = _reconstruct_textlayer_tables(file_path, page_texts)

    engines = {
        "text": "pymupdf",
        "tables": ["pymupdf_textlayer"] if tables else [],
        "fast_text_only": True,
        "pymupdf_text_pages": sorted(text_pages),
        "pymupdf_text_page_count": len(text_pages),
        "table_count": len(tables),
        "table_pages": sorted(table_page_nums),
    }
    table_coverage = _build_a_table_coverage(texts, set(), tables)
    doc = ReportDocument(
        doc_id=Path(file_path).stem,
        side=ReportSide.A_SHARE,
        file_path=file_path,
        total_pages=total_pages,
        primary_language=Language.ZH,
        tables=tables,
        texts=texts,
        charts=[],
        metadata={
            "unit": unit,
            "currency": currency.value if currency else None,
            "extraction_engines": engines,
            "table_count": len(tables),
        },
    )
    audit = build_extraction_audit(
        total_pages=total_pages,
        scanned_pages=scanned_pages,
        text_pages=text_pages,
        table_pages=sorted(table_page_nums),
        text_segments=texts,
        warning_flags=[],
        warnings=[],
        engines=engines,
        table_coverage=table_coverage,
    )
    logger.info(f"A 股快速解析完成: {total_pages} 页, {len(texts)} 文本段, 跳过表格重型抽取")
    return attach_audit(doc, audit)


def parse_a_pdf(file_path: str) -> ReportDocument:
    """A 股 PDF 解析入口。

    性能策略：
    1. 所有页面提取文本（快）
    2. 只有含财务关键词的页面才提取表格（慢操作，大幅减少调用）
    3. 表格数<30时才启用 camelot 补充
    """
    logger.info(f"开始解析 A 股 PDF: {file_path}")

    pymupdf_page_texts = _extract_pymupdf_page_texts(file_path)
    if pymupdf_page_texts and not settings.enable_a_share_table_extraction:
        return _build_fast_pymupdf_a_doc(file_path, pymupdf_page_texts)

    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber 未安装，无法解析 PDF")
        return _empty_doc(file_path)

    tables: list[FinancialTable] = []
    texts: list[TextSegment] = []
    total_pages = 0
    scanned_pages: set[int] = set()
    text_pages: set[int] = set()
    table_page_nums: set[int] = set()
    financial_pages: set[int] = set()
    pdfplumber_text_pages: set[int] = set()
    audit_flags: list[str] = []
    audit_warnings: list[str] = []
    pymupdf_text_pages = set(pymupdf_page_texts)

    with pdfplumber.open(file_path) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            scanned_pages.add(i)
            try:
                page_text = page.extract_text() or ""
            except Exception as e:
                page_text = ""
                audit_flags.append("page_text_failed")
                audit_warnings.append(f"Text extraction failed on page {i}: {e}")
            page_text = _merge_page_texts(page_text, pymupdf_page_texts.get(i, ""))

            # 提取文本段（所有页面）
            if page_text:
                text_pages.add(i)
                segments = _split_text(page_text, i)
                texts.extend(segments)

            # 性能优化：只对可能含财务表格的页面提取表格
            if _is_financial_page(page_text):
                financial_pages.add(i)
                try:
                    page_tables = page.extract_tables()
                    page_tables = [t for t in page_tables if _is_useful_a_raw_table(t)]
                except Exception as e:
                    page_tables = []
                    audit_flags.append("table_page_failed")
                    audit_warnings.append(f"Table extraction failed on page {i}: {e}")
                if not page_tables and _looks_like_a_table_page(page_text):
                    try:
                        page_tables = page.extract_tables(table_settings=_A_PDFPLUMBER_TEXT_SETTINGS) or []
                        page_tables = [t for t in page_tables if _is_useful_a_raw_table(t)]
                        if page_tables:
                            pdfplumber_text_pages.add(i)
                    except Exception as e:
                        audit_flags.append("table_page_failed")
                        audit_warnings.append(f"Text-strategy table extraction failed on page {i}: {e}")
                if page_tables:
                    table_page_nums.add(i)
                    for j, t in enumerate(page_tables, start=1):
                        source = "text" if i in pdfplumber_text_pages else "pl"
                        ft = _convert_table(t, i, j, f"A_p{i:03d}_{source}_t{j:02d}")
                        tables.append(ft)

    # 用 camelot 补充（尤其跨页/复杂表格）
    # 性能优化：若 pdfplumber 已提取较多表格，跳过 camelot lattice（极慢且召回率低）
    if len(tables) < 30:
        try:
            camelot_tables = extract_tables_camelot(file_path, page_range=_page_range_string(financial_pages), use_lattice=False)
            tables = merge_tables(tables, camelot_tables)
            table_page_nums.update(t.page for t in tables)
        except Exception as e:
            audit_flags.append("camelot_failed")
            audit_warnings.append(f"camelot table extraction failed: {e}")
            logger.warning(f"camelot 补充提取失败: {e}")
    else:
        logger.info(f"pdfplumber 已提取 {len(tables)} 个表格，跳过 camelot 补充")

    coverage_after_camelot = _build_a_table_coverage(texts, table_page_nums, tables)
    pp_pages = sorted(_missing_a_core_pages(texts, coverage_after_camelot))[:24]
    ppstructure_added_tables = 0
    if pp_pages:
        try:
            before = len(tables)
            pp_tables = extract_tables_ppstructure(file_path, pages=pp_pages)
            tables = merge_tables(tables, pp_tables)
            ppstructure_added_tables = len(tables) - before
            table_page_nums.update(t.page for t in tables)
        except Exception as e:
            audit_flags.append("ppstructure_failed")
            audit_warnings.append(f"PPStructure table extraction failed: {e}")
            logger.warning(f"PPStructure 兜底提取失败: {e}")

    # 检测单位和币种（从文本中推断）
    unit, currency = _detect_unit_currency(texts)
    for t in tables:
        if not t.unit:
            t.unit = unit
        if not t.currency:
            t.currency = currency

    table_page_nums.update(t.page for t in tables)
    final_coverage = _build_a_table_coverage(texts, table_page_nums, tables)
    logger.info(f"A 股解析完成: {total_pages} 页, {len(tables)} 表({len(table_page_nums)} 个财务页), {len(texts)} 文本段")
    doc = ReportDocument(
        doc_id=Path(file_path).stem,
        side=ReportSide.A_SHARE,
        file_path=file_path,
        total_pages=total_pages,
        primary_language=Language.ZH,
        tables=tables,
        texts=texts,
        charts=[],
        metadata={
            "unit": unit,
            "currency": currency.value if currency else None,
            "extraction_engines": {
                "text": "pdfplumber",
                "tables": ["pdfplumber", "camelot", "ppstructure"],
                "financial_page_count": len(financial_pages),
                "pdfplumber_text_pages": sorted(pdfplumber_text_pages),
                "pymupdf_text_pages": sorted(pymupdf_text_pages),
                "pymupdf_text_page_count": len(pymupdf_text_pages),
                "ppstructure": {"attempted": bool(pp_pages), "pages": pp_pages, "added_tables": ppstructure_added_tables},
            },
            "table_count": len(tables),
        },
    )
    audit = build_extraction_audit(
        total_pages=total_pages,
        scanned_pages=scanned_pages,
        text_pages=text_pages,
        table_pages=table_page_nums,
        text_segments=texts,
        warning_flags=audit_flags,
        warnings=audit_warnings,
        engines={
            "text": "pdfplumber",
            "tables": ["pdfplumber", "camelot", "ppstructure"],
            "financial_page_count": len(financial_pages),
            "pdfplumber_text_pages": sorted(pdfplumber_text_pages),
            "pymupdf_text_pages": sorted(pymupdf_text_pages),
            "pymupdf_text_page_count": len(pymupdf_text_pages),
            "table_count": len(tables),
            "table_pages": sorted(table_page_nums),
            "ppstructure": {"attempted": bool(pp_pages), "pages": pp_pages, "added_tables": ppstructure_added_tables},
        },
        table_coverage=final_coverage,
    )
    return attach_audit(doc, audit)


def _empty_doc(file_path: str) -> ReportDocument:
    """返回空的 ReportDocument（降级）。"""
    doc = ReportDocument(
        doc_id=Path(file_path).stem,
        side=ReportSide.A_SHARE,
        file_path=file_path,
        total_pages=0,
        primary_language=Language.ZH,
        tables=[],
        texts=[],
        charts=[],
    )
    audit = build_extraction_audit(
        total_pages=0,
        scanned_pages=[],
        text_pages=[],
        table_pages=[],
        warning_flags=["parser_unavailable"],
        warnings=["A-share parser returned an empty document."],
        engines={},
    )
    return attach_audit(doc, audit)


def _split_text(page_text: str, page_num: int) -> list[TextSegment]:
    """将页面文本按空行切分，并标记 section。"""
    # 按多个换行符分段
    raw_segments = re.split(r"\n{2,}", page_text.strip())
    segments: list[TextSegment] = []
    current_section: str | None = None

    for idx, seg in enumerate(raw_segments):
        seg = seg.strip()
        if not seg:
            continue

        # 检测 section 变化
        detected = _detect_section(seg)
        if detected:
            current_section = detected

        # 清理多余空白（保留兼容的 clean_text）
        clean_text = re.sub(r"\s+", " ", seg.replace("\n", " "))
        if len(clean_text) < 5:
            continue

        # 保留排版结构的 raw_text：仅压缩连续空行，保留段落内换行和空格
        raw_text = re.sub(r"\n{3,}", "\n\n", seg.strip())

        segments.append(
            TextSegment(
                segment_id=f"A_p{page_num:03d}_s{idx:02d}",
                page=page_num,
                bbox=(0.0, 0.0, 0.0, 0.0),  # pdfplumber 可后续细化
                text=clean_text,
                language=Language.ZH,
                section=current_section,
                raw_text=raw_text,
            )
        )
    return segments


def _detect_section(text: str) -> str | None:
    """通过关键词检测所属章节。"""
    # 优先匹配完整关键词
    for keyword, section_code in SECTION_KEYWORDS.items():
        if keyword in text[:100]:  # 只看前 100 字，提高效率
            return section_code
    return None


def _section_pages(texts: list[TextSegment], sections: tuple[str, ...]) -> dict[str, set[int]]:
    wanted = set(sections)
    result: dict[str, set[int]] = {section: set() for section in sections}
    for segment in texts:
        section = (segment.section or "").strip()
        if section in wanted:
            result.setdefault(section, set()).add(segment.page)
    return result


def _build_a_table_coverage(
    texts: list[TextSegment],
    table_pages: set[int],
    tables: list[FinancialTable] | None = None,
) -> dict[str, Any]:
    core_pages = _section_pages(texts, _A_CORE_SECTIONS)
    note_pages = _section_pages(texts, _A_KEY_NOTE_SECTIONS)

    # 从表格内容推断 section
    table_text_by_page = _a_table_text_by_page(tables or [])
    inferred_page_map: dict[str, set[int]] = {}
    for page in table_pages:
        combined_text = " ".join(
            part
            for part in (
                " ".join(t.text for t in texts if t.page == page),
                table_text_by_page.get(page, ""),
            )
            if part
        )
        for section in _infer_a_core_sections_from_text(combined_text):
            inferred_page_map.setdefault(section, set()).add(page)

    # 从表格自身 section 字段收集
    table_section_map: dict[str, set[int]] = {}
    for table in (tables or []):
        if table.section:
            table_section_map.setdefault(table.section, set()).add(table.page)

    covered_core = sorted(
        section
        for section in _A_CORE_SECTIONS
        if (
            (core_pages.get(section, set()) & table_pages)
            or (inferred_page_map.get(section, set()) & table_pages)
            or (table_section_map.get(section, set()) & table_pages)
        )
    )
    missing_core = sorted(section for section in _A_CORE_SECTIONS if section not in covered_core)
    covered_notes = sorted(
        section
        for section in _A_KEY_NOTE_SECTIONS
        if note_pages.get(section, set()) & table_pages
    )
    section_counts = {
        section: len(pages)
        for section, pages in {**core_pages, **note_pages}.items()
        if pages
    }
    return {
        "required_core_sections": list(_A_CORE_SECTIONS),
        "covered_core_sections": covered_core,
        "direct_core_sections": sorted(
            section for section in _A_CORE_SECTIONS if core_pages.get(section, set()) & table_pages
        ),
        "inferred_core_sections": sorted(
            section for section in _A_CORE_SECTIONS if inferred_page_map.get(section, set()) & table_pages
        ),
        "missing_core_sections": missing_core,
        "covered_key_note_sections": covered_notes,
        "table_pages": sorted(table_pages),
        "table_page_count": len(table_pages),
        "section_page_counts": section_counts,
    }


def _missing_a_core_pages(texts: list[TextSegment], coverage: dict[str, Any]) -> set[int]:
    missing_sections = set(coverage.get("missing_core_sections") or [])
    if not missing_sections:
        return set()
    pages: set[int] = set()
    for segment in texts:
        if (segment.section or "").strip() in missing_sections:
            pages.add(segment.page)
    return pages


def _a_table_text_by_page(tables: list[FinancialTable]) -> dict[int, str]:
    """聚合每张表格的标题与表头文本，按页索引。"""
    result: dict[int, list[str]] = {}
    for table in tables:
        parts = [
            table.title.zh or "",
            table.title.en or "",
        ]
        for cell in table.cells:
            if cell.is_header or cell.row <= 2 or cell.col <= 1:
                parts.append(cell.text or "")
        text = " ".join(part.strip() for part in parts if part and part.strip())
        if text:
            result.setdefault(table.page, []).append(text)
    return {page: " ".join(items) for page, items in result.items()}


def _page_range_string(pages: set[int]) -> str:
    clean = sorted({int(page) for page in pages if page > 0})
    return ",".join(str(page) for page in clean) if clean else "all"


def _convert_table(raw_table: list[list[str | None]], page: int, table_idx: int, table_id: str) -> FinancialTable:
    """将 pdfplumber 原始表格转为 FinancialTable。"""
    cells: list[TableCell] = []
    if not raw_table:
        return FinancialTable(
            table_id=table_id,
            title=LocalizedString(zh=""),
            page=page,
            bbox=(0.0, 0.0, 0.0, 0.0),
            cells=cells,
        )

    # 推断标题（第一行或前一页文本中的关键词）
    title = ""
    if raw_table and raw_table[0]:
        title = " ".join(str(c) for c in raw_table[0] if c)[:50]

    for r_idx, row in enumerate(raw_table):
        for c_idx, cell in enumerate(row):
            text = str(cell or "").strip()
            # 第一行或包含"项目/科目/金额"视为表头
            is_header = r_idx == 0 or any(k in text for k in ("项目", "科目", "金额", "本期", "上期", "202", "Notes"))
            cells.append(TableCell(row=r_idx, col=c_idx, text=text, is_header=is_header))

    # 从标题推断 section
    section = None
    inferred = _infer_a_core_sections_from_text(title)
    if inferred:
        section = sorted(inferred)[0]

    return FinancialTable(
        table_id=table_id,
        title=LocalizedString(zh=title),
        page=page,
        bbox=(0.0, 0.0, 0.0, 0.0),
        cells=cells,
        section=section,
    )


def _is_garbled_text(text: str) -> bool:
    """检测文本是否为乱码（PDF 字体编码损坏）。

    启发式规则：
    1. 包含 Unicode 替换字符 �
    2. 中文字符占比过低（<10%）且文本较长
    """
    if not text or len(text) < 10:
        return False
    replacement_chars = text.count("�")
    if replacement_chars > 0:
        return True
    # 统计中文字符
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    total_chars = len(text.strip())
    if total_chars > 50 and chinese_chars / total_chars < 0.05:
        return True
    return False


def _detect_unit_currency(texts: list[TextSegment]) -> tuple[str | None, Currency | None]:
    """从文本中检测金额单位和币种。

    策略：
    1. 优先从财务报表主表（bs/pl/cf/equity）中检测单位声明
    2. 要求单位声明带有"单位"、"除另有说明"等上下文，避免正文叙述误匹配
    3. 遇到乱码文本时跳过
    A 股默认人民币元。
    """
    # 严格模式：带单位声明上下文的模式
    strict_patterns = [
        (r"单位[:：\s]*人民币[\s]*千[\s]*元", "人民币千元", Currency.CNY),
        (r"单位[:：\s]*人民币[\s]*百[\s]*万[\s]*元", "人民币百万元", Currency.CNY),
        (r"单位[:：\s]*人民币[\s]*亿[\s]*元", "人民币亿元", Currency.CNY),
        (r"单位[:：\s]*人民币[\s]*万[\s]*元", "人民币万元", Currency.CNY),
        (r"单位[:：\s]*人民币[\s]*元", "人民币元", Currency.CNY),
        (r"除另有说明外.*?人民币[\s]*千[\s]*元", "人民币千元", Currency.CNY),
        (r"除另有说明外.*?人民币[\s]*百[\s]*万[\s]*元", "人民币百万元", Currency.CNY),
        (r"除另有说明外.*?人民币[\s]*亿[\s]*元", "人民币亿元", Currency.CNY),
        (r"除另有说明外.*?人民币[\s]*万[\s]*元", "人民币万元", Currency.CNY),
        (r"除另有说明外.*?人民币[\s]*元", "人民币元", Currency.CNY),
        (r"金额单位[:：\s]*人民币[\s]*千[\s]*元", "人民币千元", Currency.CNY),
        (r"金额单位[:：\s]*人民币[\s]*百[\s]*万[\s]*元", "人民币百万元", Currency.CNY),
        (r"金额单位[:：\s]*人民币[\s]*亿[\s]*元", "人民币亿元", Currency.CNY),
        (r"金额单位[:：\s]*人民币[\s]*万[\s]*元", "人民币万元", Currency.CNY),
        (r"金额单位[:：\s]*人民币[\s]*元", "人民币元", Currency.CNY),
        (r'RMB[\s]*[\'"](\d+)[\s]*million', '人民币百万元', Currency.CNY),
    ]

    # 宽松模式（仅用于财务主表章节）：直接匹配单位关键词
    loose_patterns = [
        (r"人民币[\s]*千[\s]*元", "人民币千元", Currency.CNY),
        (r"人民币[\s]*百[\s]*万[\s]*元", "人民币百万元", Currency.CNY),
        (r"人民币[\s]*亿[\s]*元", "人民币亿元", Currency.CNY),
        (r"人民币[\s]*万[\s]*元", "人民币万元", Currency.CNY),
        (r"人民币[\s]*元", "人民币元", Currency.CNY),
        (r"千元", "千元", Currency.CNY),
        (r"百万元", "百万元", Currency.CNY),
        (r"亿元", "亿元", Currency.CNY),
        (r"万元", "万元", Currency.CNY),
        (r"港元", "港元", Currency.HKD),
        (r"港币", "港元", Currency.HKD),
        (r"HKD", "港元", Currency.HKD),
        (r"美元", "美元", Currency.USD),
        (r"USD", "美元", Currency.USD),
    ]

    # 阶段 1: 优先扫描财务报表主表章节（bs/pl/cf/equity），先严格后宽松，跳过乱码页
    financial_sections = ("bs", "pl", "cf", "equity")
    financial_texts = [t for t in texts if t.section in financial_sections and not _is_garbled_text(t.text)]
    for patterns in (strict_patterns, loose_patterns):
        for t in financial_texts:
            for pattern, unit_str, curr in patterns:
                if re.search(pattern, t.text):
                    return unit_str, curr

    # 阶段 2: 扫描其余文本，只用严格模式，跳过乱码页
    for t in texts:
        if _is_garbled_text(t.text):
            continue
        for pattern, unit_str, curr in strict_patterns:
            if re.search(pattern, t.text):
                return unit_str, curr

    # 阶段 3: A 股默认人民币元
    return "人民币元", Currency.CNY
