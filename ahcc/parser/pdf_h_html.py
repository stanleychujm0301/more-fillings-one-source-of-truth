"""H 股 PDF/HTML 解析（P2 实现）— 支持 HTML 年报和 PDF 双模式。

H 股特点：
- 语言：英文为主，部分含繁体中文
- 格式：PDF（扫描或文本型）或 HTML（港交所披露易）
- 章节命名：英文，如 "Consolidated Statement of Financial Position"
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from ahcc.config import settings
from ahcc.parser.audit import attach_audit, build_extraction_audit
from ahcc.parser.table_extract import (
    extract_tables_camelot,
    extract_tables_ppstructure,
    merge_tables,
)

H_PDF_CACHE_VERSION = "h_pdf_v3"

# 解析心跳回调：长页循环（尤其乱码页 OCR 兜底）每页触发一次，供 worker 子进程刷新
# heartbeat 文件，避免"预算内但慢"的解析被父进程误判为卡死。inline 模式保持 None。
_parse_heartbeat: Callable[[], None] | None = None


def set_parse_heartbeat(callback: Callable[[], None] | None) -> None:
    global _parse_heartbeat
    _parse_heartbeat = callback


def _emit_parse_heartbeat() -> None:
    if _parse_heartbeat is not None:
        try:
            _parse_heartbeat()
        except Exception:  # pragma: no cover - 心跳失败不影响解析
            pass


def _h_pdf_cache_dir() -> Path:
    return settings.storage_dir / "parser_cache" / "h_pdf"


def _h_pdf_cache_key(file_path: str) -> str:
    file_bytes = Path(file_path).read_bytes()
    digest = hashlib.sha256(file_bytes).hexdigest()
    return f"{H_PDF_CACHE_VERSION}_{digest}"


def _h_pdf_cache_path(cache_key: str) -> Path:
    return _h_pdf_cache_dir() / f"{cache_key}.json"


def _with_parser_cache_metadata(doc: ReportDocument, *, hit: bool, cache_key: str) -> ReportDocument:
    metadata = dict(doc.metadata or {})
    metadata["parser_cache"] = {
        "hit": hit,
        "key": cache_key,
        "version": H_PDF_CACHE_VERSION,
    }
    return doc.model_copy(update={"metadata": metadata})


def _load_h_pdf_cache(file_path: str, cache_key: str) -> ReportDocument | None:
    cache_path = _h_pdf_cache_path(cache_key)
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        doc = ReportDocument.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to load H PDF parser cache {cache_path}: {exc}")
        return None
    return _with_parser_cache_metadata(
        doc.model_copy(update={"file_path": file_path}),
        hit=True,
        cache_key=cache_key,
    )


def _save_h_pdf_cache(doc: ReportDocument, cache_key: str) -> None:
    cache_dir = _h_pdf_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _h_pdf_cache_path(cache_key)
    metadata = dict(doc.metadata or {})
    runtime_cache = dict(metadata.get("parser_cache") or {})
    if runtime_cache:
        runtime_cache["hit"] = False
        metadata["parser_cache"] = runtime_cache
    serializable_doc = doc.model_copy(update={"metadata": metadata})
    cache_path.write_text(
        json.dumps(serializable_doc.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
from ahcc.schemas import (
    Currency,
    FinancialTable,
    Language,
    LocalizedString,
    ReportDocument,
    ReportSide,
    TableCell,
    TextSegment,
)


# H 股章节检测关键词（英文 + 繁体中文）
H_SECTION_KEYWORDS = {
    # 英文关键词
    "consolidated statement of financial position": "bs",
    "consolidated balance sheet": "bs",
    "consolidated income statement": "pl",
    "consolidated statement of profit or loss": "pl",
    "consolidated statement of comprehensive income": "pl",
    "consolidated cash flow statement": "cf",
    "consolidated statement of cash flows": "cf",
    "consolidated statement of changes in equity": "equity",
    "directors' report": "directors_report",
    "management discussion": "mda",
    "related party": "related_party",
    "significant events": "significant_events",
    "accounting policies": "accounting_policy",
    "notes to the financial statements": "notes",
    "financial statements": "financial_statements",
    "corporate information": "company_profile",
    "corporate governance": "corporate_governance",
    "environmental, social": "esg",
    "sustainability": "esg",
    "share capital": "share_changes",
    "perpetual bonds": "perpetual_bond",
    "preference shares": "preference_shares",
    "goodwill": "goodwill",
    "research and development": "rnd",
    "research & development": "rnd",
    "income tax": "income_tax",
    "earnings per share": "eps",
    "segment reporting": "segment_report",
    "operating segments": "segment_report",
    "government grants": "government_grant",
    "leases": "leases",
    "financial instruments": "financial_instruments",
    "revenue": "revenue",
    "inventories": "inventories",
    "property, plant": "ppe",
    "construction in progress": "construction_in_progress",
    "intangible assets": "intangible_assets",
    "investment property": "investment_property",
    "investments in subsidiaries": "long_term_investment",
    "employee benefits": "employee_benefits",
    "provisions": "provisions",
    "share premium": "capital_reserve",
    "retained profits": "retained_earnings",
    "non-controlling interests": "minority_interest",
    "minority interests": "minority_interest",
    "cost of sales": "cogs",
    "selling and distribution": "selling_expenses",
    "administrative expenses": "admin_expenses",
    "finance costs": "finance_expenses",
    "profit for the year": "net_profit",
    "profit attributable": "net_profit_attributable",
    "basic earnings per share": "basic_eps",
    "diluted earnings per share": "diluted_eps",
    "cash flows from operating activities": "cfo",
    "cash flows from investing activities": "cfi",
    "cash flows from financing activities": "cff",
    "cash and cash equivalents": "cash_equivalents",
    "trade receivables": "receivables",
    "accounts receivable": "receivables",
    "prepayments": "prepayments",
    "other receivables": "other_receivables",
    "current assets": "current_assets",
    "non-current assets": "non_current_assets",
    "total assets": "total_assets",
    "bank borrowings": "short_term_borrowings",
    "trade payables": "payables",
    "accounts payable": "payables",
    "contract liabilities": "contract_liabilities",
    "other payables": "other_payables",
    "current liabilities": "current_liabilities",
    "non-current liabilities": "non_current_liabilities",
    "total liabilities": "total_liabilities",
    "issued capital": "share_capital",
    "other reserves": "oci",
    "total equity": "total_equity",
    # 繁体中文关键词（国泰海通 H 股年报主要使用繁体中文）
    "資產負債表": "bs",
    "損益表": "pl",
    "綜合損益表": "pl",
    "現金流量表": "cf",
    "權益變動表": "equity",
    "股東權益變動表": "equity",
    "財務報表": "financial_statements",
    "財務報告": "financial_statements",
    "附註": "notes",
    "會計政策": "accounting_policy",
    "重要會計估計": "accounting_estimate",
    "董事會報告": "directors_report",
    "管理層討論與分析": "mda",
    "管理層討論及分析": "mda",
    "重要事項": "significant_events",
    "關聯方": "related_party",
    "關連方": "related_party",
    "關聯交易": "related_party",
    "關連交易": "related_party",
    "公司簡介": "company_profile",
    "公司資料": "company_profile",
    "公司治理": "corporate_governance",
    "企業管治": "corporate_governance",
    "環境與社會": "esg",
    "環境、社會": "esg",
    "可持續發展": "esg",
    "股份變動": "share_changes",
    "股本變動": "share_changes",
    "優先股": "preference_shares",
    "債券": "bonds",
    "商譽": "goodwill",
    "所得稅": "income_tax",
    "每股盈利": "eps",
    "分部報告": "segment_report",
    "經營分部": "segment_report",
    "政府補助": "government_grant",
    "政府補貼": "government_grant",
    "租賃": "leases",
    "金融工具": "financial_instruments",
    "營業收入": "revenue",
    "收益": "revenue",
    "存貨": "inventories",
    "物業、廠房": "ppe",
    "固定資產": "ppe",
    "在建工程": "construction_in_progress",
    "在建項目": "construction_in_progress",
    "無形資產": "intangible_assets",
    "投資性房地產": "investment_property",
    "投資物業": "investment_property",
    "長期股權投資": "long_term_investment",
    "於附屬公司之投資": "long_term_investment",
    "僱員福利": "employee_benefits",
    "應付職工薪酬": "employee_benefits",
    "撥備": "provisions",
    "預計負債": "provisions",
    "股份溢價": "capital_reserve",
    "資本公積": "capital_reserve",
    "保留溢利": "retained_earnings",
    "未分配利潤": "retained_earnings",
    "非控制性權益": "minority_interest",
    "少數股東權益": "minority_interest",
    "銷售成本": "cogs",
    "營業成本": "cogs",
    "分銷及銷售費用": "selling_expenses",
    "銷售費用": "selling_expenses",
    "行政費用": "admin_expenses",
    "管理費用": "admin_expenses",
    "融資成本": "finance_expenses",
    "財務費用": "finance_expenses",
    "年內溢利": "net_profit",
    "淨利潤": "net_profit",
    "歸屬於母公司股東的淨利潤": "net_profit_attributable",
    "歸母淨利潤": "net_profit_attributable",
    "股東應佔溢利": "net_profit_attributable",
    "基本每股盈利": "basic_eps",
    "攤薄每股盈利": "diluted_eps",
    "經營活動現金流量": "cfo",
    "經營活動所得現金流量": "cfo",
    "投資活動現金流量": "cfi",
    "投資活動所得現金流量": "cfi",
    "融資活動現金流量": "cff",
    "融資活動所得現金流量": "cff",
    "現金及現金等價物": "cash_equivalents",
    "貨幣資金": "cash",
    "應收賬款": "receivables",
    "應收賬項": "receivables",
    "預付款項": "prepayments",
    "其他應收款": "other_receivables",
    "其他應收賬款": "other_receivables",
    "流動資產": "current_assets",
    "非流動資產": "non_current_assets",
    "資產總額": "total_assets",
    "資產總計": "total_assets",
    "財務狀況表": "bs",
    "綜合收益表": "pl",
    "收益表": "pl",
    "銀行借款": "short_term_borrowings",
    "短期借款": "short_term_borrowings",
    "應付賬款": "payables",
    "應付賬項": "payables",
    "合約負債": "contract_liabilities",
    "合同負債": "contract_liabilities",
    "其他應付款": "other_payables",
    "流動負債": "current_liabilities",
    "非流動負債": "non_current_liabilities",
    "負債總額": "total_liabilities",
    "負債總計": "total_liabilities",
    "已發行股本": "share_capital",
    "股本": "share_capital",
    "其他儲備": "oci",
    "其他綜合收益": "oci",
    "權益總額": "total_equity",
    "權益總計": "total_equity",
    "負債和權益總計": "total_liabilities_equity",
    # 证券行业特有（繁体）
    "手續費及佣金收入": "fee_commission_income",
    "手續費及佣金支出": "fee_commission_expense",
    "利息收入": "interest_income",
    "利息支出": "interest_expense",
    "融出資金": "margin_loans",
    "買入返售金融資產": "reverse_repurchase",
    "賣出回購金融資產款": "repo_payable",
    "交易性金融資產": "trading_assets",
    "債權投資": "debt_investment",
    "其他債權投資": "other_debt_investment",
    "其他權益工具投資": "other_equity_investment",
    "金融負債": "financial_liabilities",
    "代理買賣證券款": "agency_trading_payable",
    "應付短期融資款": "short_term_financing",
    "應付債券": "bonds_payable",
    "衍生金融工具": "derivative_instruments",
    "公允價值變動": "fv_change",
    "投資收益": "investment_income",
    "資產減值損失": "impairment_loss",
    "信用減值損失": "credit_impairment",
    "其他資產": "other_assets",
    "其他負債": "other_liabilities",
    "代理承銷證券款": "agency_underwriting",
    "證券承銷業務": "underwriting",
    "資產管理業務": "asset_management",
    "融資融券業務": "margin_trading",
    "股票質押回購": "stock_pledge_repo",
    "期貨經紀業務": "futures_brokerage",
}


def parse_h_pdf(file_path: str) -> ReportDocument:
    """H 股 PDF 解析入口。"""
    logger.info(f"开始解析 H 股 PDF: {file_path}")

    suffix = Path(file_path).suffix.lower()
    if suffix in (".html", ".htm"):
        return _parse_h_html(file_path)

    cache_key = _h_pdf_cache_key(file_path)
    cached = _load_h_pdf_cache(file_path, cache_key)
    if cached is not None:
        return cached

    parsed = _parse_h_pdf(file_path)
    parsed = _with_parser_cache_metadata(parsed, hit=False, cache_key=cache_key)
    try:
        _save_h_pdf_cache(parsed, cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to save H PDF parser cache for {file_path}: {exc}")
    return parsed


# H 股财务关键词（英文/繁体 — 更宽泛以覆盖港式年报排版）
_H_FINANCIAL_KEYWORDS = [
    # 报表名称（英文）
    "Balance Sheet", "Statement of", "Cash Flow", "Equity",
    "Consolidated", "Notes", "Financial Statements",
    "COMPREHENSIVE INCOME", "PROFIT OR LOSS", "FINANCIAL POSITION",
    "CHANGES IN EQUITY", "CASH FLOWS",
    # 关键科目（英文）
    "Revenue", "Profit", "Total assets", "Total liabilities",
    "Assets", "Liabilities", "Equity", "Income", "Expenses",
    "Borrowings", "Deposits", "Margin", "Trading",
    # 证券行业特有（英文）
    "Commission", "Brokerage", "Underwriting", "Investment",
    "Securities", "Futures", "Asset management",
    # 繁体中文报表名称
    "資產負債表", "損益表", "綜合損益表", "現金流量表", "權益變動表",
    "股東權益變動表", "財務報表", "財務報告", "附註",
    # 繁体中文关键科目
    "營業收入", "收益", "利潤", "資產", "負債", "權益",
    "流動資產", "非流動資產", "流動負債", "非流動負債",
    "總額", "總計", "合計", "股本", "已發行股本",
    "現金及現金等價物", "應收賬款", "應收賬項", "存貨",
    "手續費及佣金", "利息收入", "融出資金", "買入返售",
    "交易性金融資產", "代理買賣證券", "應付債券",
    "銀行借款", "短期借款", "長期借款",
    "投資收益", "融資成本", "行政費用", "分銷及銷售費用",
    "所得稅", "每股盈利", "保留溢利", "股份溢價",
]

# H 股主表关键词（用于限制 pdfplumber 只提取主表页面，大幅提速）
_H_STATEMENT_KEYWORDS = [
    # 繁体中文
    "資產負債表", "損益表", "綜合損益表", "綜合收益表", "收益表",
    "現金流量表", "權益變動表", "股東權益變動表",
    # 简体中文（部分H股含简体）
    "资产负债表", "利润表", "现金流量表", "所有者权益变动表",
    # 英文（覆盖常见变体）
    "Balance Sheet", "Statement of Financial Position",
    "Consolidated Statement of Financial Position",
    "Income Statement", "Statement of Profit or Loss",
    "Consolidated Statement of Profit or Loss",
    "Comprehensive Income", "Statement of Comprehensive Income",
    "Consolidated Statement of Comprehensive Income",
    "Cash Flow Statement", "Statement of Cash Flows",
    "Consolidated Statement of Cash Flows",
    "Changes in Equity", "Statement of Changes in Equity",
    "Consolidated Statement of Changes in Equity",
    "FINANCIAL POSITION", "PROFIT OR LOSS", "CASH FLOWS", "CHANGES IN EQUITY",
    "STATEMENT OF FINANCIAL POSITION", "STATEMENT OF PROFIT OR LOSS",
    "STATEMENT OF COMPREHENSIVE INCOME", "STATEMENT OF CASH FLOWS",
    "STATEMENT OF CHANGES IN EQUITY",
]


def _is_h_financial_page(page_text: str) -> bool:
    """判断 H 股页面是否可能包含财务表格。"""
    if not page_text:
        return False
    # 看前800字，因为英文标题可能不在最前面
    text = page_text[:800]
    return any(kw in text for kw in _H_FINANCIAL_KEYWORDS)


def _is_h_statement_page(page_text: str) -> bool:
    """判断 H 股页面是否包含主表（资产负债表/损益表/现金流量表/权益变动表）。

    用于限制 pdfplumber 只处理主表页面，避免在大量附注页上浪费时间。
    """
    if not page_text:
        return False
    # 扩大检测范围到1200字符，因为英文标题可能在页面中部
    text = page_text[:1200].lower()
    # 大小写不敏感匹配（某些PDF标题是全大写的，有些是小写的）
    return any(kw.lower() in text for kw in _H_STATEMENT_KEYWORDS)


_H_CORE_SECTIONS = ("bs", "pl", "cf", "equity")
_H_KEY_NOTE_SECTIONS = ("financial_instruments", "related_party", "segment_report", "income_tax", "eps")
_H_CORE_TITLE_PATTERNS = {
    "bs": (
        "statement of financial position",
        "balance sheet",
    ),
    "pl": (
        "statement of profit or loss",
        "income statement",
        "statement of comprehensive income",
        "comprehensive income",
    ),
    "cf": (
        "statement of cash flows",
        "cash flow statement",
        "condensed consolidated statement of cash flows",
        "consolidated statement of cash flows",
        "cash flows from operating activities",
        "cash flows from investing activities",
        "cash flows from financing activities",
        "net cash generated from operating activities",
        "net cash used in operating activities",
        "net cash generated from investing activities",
        "net cash used in investing activities",
        "net cash generated from financing activities",
        "net cash used in financing activities",
        "net increase in cash",
        "net decrease in cash",
        "cash and cash equivalents at end of year",
        "cash and cash equivalents at end of period",
        "cash and cash equivalents at end",
        "cash and cash equivalents at beginning",
        "operating cash flows",
        "investing cash flows",
        "financing cash flows",
        "proceeds from",
        "payments for",
        "dividends paid",
        "interest paid",
        "tax paid",
        "purchase of property",
        "disposal of subsidiaries",
    ),
    "equity": (
        "statement of changes in equity",
        "changes in equity",
    ),
}
_H_CORE_SUPPORTING_PATTERNS = {
    "bs": (
        "current assets",
        "total assets",
        "current liabilities",
        "total liabilities",
        "non-current assets",
        "non-current liabilities",
        "assets",
        "liabilities",
        "investment property",
        "property, plant and equipment",
        "intangible assets",
        "goodwill",
        "deferred tax",
        "inventories",
        "trade receivables",
        "cash and cash equivalents",
        "bank balances",
        "deposits",
        "borrowings",
        "lease liabilities",
        "provisions",
    ),
    "pl": (
        "revenue",
        "profit for the year",
        "earnings per share",
        "profit attributable",
        "income tax expense",
        "gross profit",
        "operating profit",
        "profit before tax",
        "total comprehensive income",
        "other income",
        "finance costs",
        "administrative expenses",
        "selling and distribution expenses",
        "cost of sales",
        "depreciation",
        "amortisation",
        "interest income",
        "interest expense",
        "net profit",
        "loss for the year",
    ),
    "cf": (
        "cash flows from operating activities",
        "cash flows from investing activities",
        "cash flows from financing activities",
        "net cash generated from operating activities",
        "net cash used in operating activities",
        "net cash generated from investing activities",
        "net cash used in investing activities",
        "net cash generated from financing activities",
        "net cash used in financing activities",
        "net increase in cash",
        "net decrease in cash",
        "cash and cash equivalents at end of year",
        "cash and cash equivalents at end of period",
        "cash and cash equivalents at end",
        "cash and cash equivalents at beginning",
        "operating cash flows",
        "investing cash flows",
        "financing cash flows",
        "proceeds from",
        "payments for",
        "dividends paid",
        "interest paid",
        "tax paid",
        "purchase of property",
        "disposal of subsidiaries",
    ),
    "equity": (
        "share capital",
        "reserves",
        "retained profits",
        "non-controlling interests",
        "total equity",
        "changes in equity",
        "movement in equity",
        "opening balance",
        "closing balance",
        "dividends",
        "bonus issue",
        "share premium",
        "other reserves",
        "capital redemption reserve",
        "statutory reserve",
        "exchange reserve",
    ),
}
_H_PDFPLUMBER_TEXT_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "edge_min_length": 2,
    "min_words_vertical": 1,
    "min_words_horizontal": 1,
    "text_tolerance": 5,
}


def _expand_pages(pages: set[int], total_pages: int, window: int = 1) -> set[int]:
    expanded: set[int] = set()
    for page in pages:
        for candidate in range(max(1, page - window), min(total_pages, page + window) + 1):
            expanded.add(candidate)
    return expanded


def _section_pages(texts: list[TextSegment], section_codes: tuple[str, ...]) -> set[int]:
    codes = set(section_codes)
    return {segment.page for segment in texts if segment.section in codes}


def _page_range_string(pages: set[int]) -> str:
    clean = sorted({int(page) for page in pages if page > 0})
    return ",".join(str(page) for page in clean) if clean else "all"


def _looks_like_table_page(page_text: str) -> bool:
    if not page_text:
        return False
    text = re.sub(r"\s+", " ", page_text)
    if len(text) < 80:
        return False
    digit_count = sum(ch.isdigit() for ch in text)
    digit_ratio = digit_count / max(len(text), 1)
    line_count = page_text.count("\n") + 1
    separator_count = sum(page_text.count(ch) for ch in ("|", "│", "─", "━", "—", "="))
    table_markers = ("項目", "项目", "單位", "单位", "2025年", "2024年")
    if any(marker in text for marker in table_markers) and digit_ratio >= 0.05:
        return True
    if digit_ratio >= 0.22 and line_count >= 10:
        return True
    return separator_count >= 4 and line_count >= 5


def _is_useful_h_raw_table(raw_table: list[list[str | None]]) -> bool:
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


def _select_h_table_candidate_pages(
    *,
    total_pages: int,
    statement_pages: set[int],
    financial_pages: set[int],
    page_texts: dict[int, str],
    texts: list[TextSegment],
) -> list[int]:
    core_pages = set(statement_pages) | _section_pages(texts, _H_CORE_SECTIONS)
    note_pages = _section_pages(texts, _H_KEY_NOTE_SECTIONS)
    table_like_pages = {page for page, page_text in page_texts.items() if _looks_like_table_page(page_text)}
    candidate_pages = core_pages | note_pages | table_like_pages
    candidate_pages |= _expand_pages(core_pages, total_pages, window=1)
    if len(candidate_pages) < 8 and financial_pages:
        candidate_pages |= set(financial_pages)
    if not candidate_pages:
        candidate_pages = set(range(1, total_pages + 1))
    return sorted(candidate_pages)


def _build_h_table_coverage(
    texts: list[TextSegment],
    table_pages: set[int],
    *,
    page_texts: dict[int, str] | None = None,
    tables: list[FinancialTable] | None = None,
) -> dict[str, Any]:
    section_page_map: dict[str, set[int]] = {}
    for segment in texts:
        if segment.section:
            section_page_map.setdefault(segment.section, set()).add(segment.page)

    table_text_by_page = _h_table_text_by_page(tables or [])
    page_text_payload = page_texts or {}
    inferred_page_map: dict[str, set[int]] = {}
    for page in table_pages:
        combined_text = " ".join(
            part
            for part in (
                page_text_payload.get(page, ""),
                table_text_by_page.get(page, ""),
            )
            if part
        )
        for section in _infer_h_core_sections_from_text(combined_text):
            inferred_page_map.setdefault(section, set()).add(page)

    # 从表格标题直接推断 section
    title_inferred_map: dict[str, set[int]] = {}
    table_section_map: dict[str, set[int]] = {}
    for table in (tables or []):
        if table.section:
            table_section_map.setdefault(table.section, set()).add(table.page)
        title_text = " ".join(filter(None, [table.title.zh, table.title.en])).lower()
        if title_text:
            for section in _infer_h_core_sections_from_text(title_text):
                title_inferred_map.setdefault(section, set()).add(table.page)

    direct_core = sorted(
        section
        for section in _H_CORE_SECTIONS
        if section_page_map.get(section, set()) & table_pages
    )
    inferred_core = sorted(
        section
        for section in _H_CORE_SECTIONS
        if (
            inferred_page_map.get(section, set()) & table_pages
            or title_inferred_map.get(section, set()) & table_pages
            or table_section_map.get(section, set()) & table_pages
        )
    )
    covered_core = sorted(
        section
        for section in _H_CORE_SECTIONS
        if section in set(direct_core) | set(inferred_core)
    )
    missing_core = sorted(section for section in _H_CORE_SECTIONS if section not in covered_core)

    # 相邻页传播：对仍缺失的 section，若其相邻 table 页已推断出该 section，则传播
    propagated_map: dict[str, set[int]] = {}
    if missing_core:
        max_page = max(
            (max(table_pages) if table_pages else 0),
            (max(page_text_payload.keys()) if page_text_payload else 0),
        )
        for section in missing_core:
            source_pages = (
                inferred_page_map.get(section, set())
                | title_inferred_map.get(section, set())
                | table_section_map.get(section, set())
            )
            if not source_pages:
                continue
            for page in table_pages:
                if any(abs(page - src) <= 2 for src in source_pages):
                    propagated_map.setdefault(section, set()).add(page)
        for section, pages in propagated_map.items():
            if pages:
                covered_core.append(section)
        covered_core = sorted(set(covered_core))
        missing_core = sorted(section for section in _H_CORE_SECTIONS if section not in covered_core)

    covered_key_notes = sorted(
        section
        for section in _H_KEY_NOTE_SECTIONS
        if section_page_map.get(section, set()) & table_pages
    )
    section_codes = set(_H_CORE_SECTIONS) | set(_H_KEY_NOTE_SECTIONS)
    return {
        "required_core_sections": list(_H_CORE_SECTIONS),
        "direct_core_sections": direct_core,
        "inferred_core_sections": inferred_core,
        "inference_sources": {
            section: sorted(pages)
            for section, pages in sorted(inferred_page_map.items())
            if section in _H_CORE_SECTIONS
        },
        "covered_core_sections": covered_core,
        "missing_core_sections": missing_core,
        "covered_key_note_sections": covered_key_notes,
        "table_pages": sorted(table_pages),
        "table_page_count": len(table_pages),
        "section_page_counts": {
            section: len(pages)
            for section, pages in sorted(section_page_map.items())
            if section in section_codes
        },
    }


def _h_table_text_by_page(tables: list[FinancialTable]) -> dict[int, str]:
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


def _infer_h_core_sections_from_text(text: str) -> set[str]:
    if not text:
        return set()
    normalized = re.sub(r"\s+", " ", text.lower())
    inferred: set[str] = set()
    for section, patterns in _H_CORE_TITLE_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            inferred.add(section)
    for section, patterns in _H_CORE_SUPPORTING_PATTERNS.items():
        hits = sum(1 for pattern in patterns if pattern in normalized)
        if section == "cf" and hits >= 2:
            inferred.add(section)
        elif section in {"bs", "pl", "equity"} and hits >= 3:
            inferred.add(section)
    return inferred


def _missing_core_pages(texts: list[TextSegment], coverage: dict[str, Any]) -> set[int]:
    missing = set(coverage.get("missing_core_sections") or [])
    if not missing:
        return set()
    return {segment.page for segment in texts if segment.section in missing}


def _parse_h_pdf(file_path: str) -> ReportDocument:
    """H 股 PDF 模式（文本型/扫描型）。

    性能策略：
    1. PyMuPDF 快速扫描所有页面提取文本（比 pdfplumber 快 5-10 倍）
    2. 标记财务页面
    3. pdfplumber 只对财务页面提取表格（表格质量更好）
    """
    try:
        import pdfplumber
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("pdfplumber / PyMuPDF 未安装，无法解析 PDF")
        return _empty_doc(file_path)

    tables: list[FinancialTable] = []
    texts: list[TextSegment] = []
    total_pages = 0
    scanned_pages: set[int] = set()
    text_pages: set[int] = set()
    table_page_nums: set[int] = set()
    audit_flags: list[str] = []
    audit_warnings: list[str] = []
    page_texts: dict[int, str] = {}

    # ---- 阶段 1: PyMuPDF 快速文本提取 + 页面标记 ----
    financial_pages: set[int] = set()
    statement_pages: set[int] = set()
    garbled_fallback_count = 0  # 乱码页 pdfplumber 回退计数
    ocr_recovered_pages: set[int] = set()  # OCR 成功恢复的页码
    # 乱码页 OCR 兜底预算：扫描版 PDF 可能几百页全部乱码，无预算时每页 OCR 会把任务
    # 拖到数小时（历史任务卡死 33000+ 秒的根因）。超预算的乱码页跳过 OCR、只记 warning。
    garbled_ocr_started = time.monotonic()
    garbled_ocr_pages_used = 0
    garbled_ocr_budget_exhausted = False
    fallback_pdf = None  # 共享 pdfplumber 句柄——此前每个乱码页重开一次整个 PDF
    try:
        doc = fitz.open(file_path)
        total_pages = len(doc)
        for page_idx in range(total_pages):
            page_num = page_idx + 1
            page = doc[page_idx]
            scanned_pages.add(page_num)
            _emit_parse_heartbeat()
            try:
                page_text = page.get_text() or ""
            except Exception as e:
                page_text = ""
                audit_flags.append("page_text_failed")
                audit_warnings.append(f"Text extraction failed on page {page_num}: {e}")
            # ── 乱码检测 + pdfplumber 回退 + OCR 兜底 ──
            # PyMuPDF 在某些 PDF 上对繁体中文的提取会产生乱码（� 字符或极低可读比例）。
            # 检测到乱码时先通过 pdfplumber 重新提取，仍乱码则启用 OCR。
            if page_text and _is_h_garbled(page_text):
                logger.info(
                    f"PyMuPDF p{page_num} 提取疑似乱码，尝试 pdfplumber 回退 "
                    f"(len={len(page_text)}, readable_ratio={len(re.findall(r'[a-zA-Z一-鿿]', page_text))/max(len(page_text), 1):.2f})"
                )
                try:
                    if fallback_pdf is None:
                        fallback_pdf = pdfplumber.open(file_path)
                    if page_num <= len(fallback_pdf.pages):
                        fb_text = fallback_pdf.pages[page_num - 1].extract_text() or ""
                    else:
                        fb_text = ""
                except Exception:
                    fb_text = ""
                if fb_text and not _is_h_garbled(fb_text):
                    page_text = fb_text
                    garbled_fallback_count += 1
                    logger.info(f"PyMuPDF p{page_num} pdfplumber 回退成功")
                else:
                    ocr_budget_ok = (
                        garbled_ocr_pages_used < settings.parse_garbled_ocr_max_pages
                        and time.monotonic() - garbled_ocr_started < settings.parse_garbled_ocr_max_seconds
                    )
                    if not ocr_budget_ok:
                        if not garbled_ocr_budget_exhausted:
                            garbled_ocr_budget_exhausted = True
                            audit_flags.append("garbled_ocr_budget_exhausted")
                            audit_warnings.append(
                                f"Garbled-page OCR budget exhausted after {garbled_ocr_pages_used} page(s); "
                                "remaining garbled pages keep raw text."
                            )
                            logger.warning(
                                f"乱码页 OCR 预算用尽（{garbled_ocr_pages_used} 页 / "
                                f"{time.monotonic() - garbled_ocr_started:.0f}s），后续乱码页跳过 OCR"
                            )
                        audit_warnings.append(f"Page {page_num} text may be garbled (OCR skipped: budget)")
                    else:
                        logger.warning(f"PyMuPDF p{page_num} pdfplumber 回退也失败，尝试 OCR")
                        try:
                            from ahcc.parser.ocr_fallback import ocr_page

                            garbled_ocr_pages_used += 1
                            ocr_result = ocr_page(file_path, page_num, dpi=200)
                            ocr_text = "\n".join(r["text"] for r in ocr_result if r.get("text"))
                            if ocr_text and not _is_h_garbled_loose(ocr_text):
                                page_text = ocr_text
                                ocr_recovered_pages.add(page_num)
                                garbled_fallback_count += 1
                                logger.info(f"PyMuPDF p{page_num} OCR 回退成功")
                            else:
                                logger.warning(f"PyMuPDF p{page_num} OCR 也失败，保留原始文本")
                                audit_warnings.append(f"Page {page_num} text may be garbled (both engines)")
                        except Exception as ocr_e:
                            logger.warning(f"PyMuPDF p{page_num} OCR 异常: {ocr_e}")
                            audit_warnings.append(f"Page {page_num} text may be garbled (both engines)")
                        _emit_parse_heartbeat()
            page_texts[page_num] = page_text
            if page_text:
                text_pages.add(page_num)
                segments = _split_h_text(page_text, page_num)
                texts.extend(segments)
                if _is_h_financial_page(page_text):
                    financial_pages.add(page_num)
                if _is_h_statement_page(page_text):
                    statement_pages.add(page_num)
                # 现金流量表可能在附录深处，全文搜索确保不遗漏
                if "现金流量表" in page_text or "現金流量表" in page_text or "Cash Flow Statement" in page_text or "Statement of Cash Flows" in page_text:
                    statement_pages.add(page_num)
        doc.close()
        if ocr_recovered_pages:
            logger.info(
                f"OCR fallback recovered {len(ocr_recovered_pages)} garbled page(s): "
                f"{sorted(ocr_recovered_pages)[:10]}"
            )
    except Exception as e:
        logger.warning(f"PyMuPDF 快速扫描失败，回退到 pdfplumber: {e}")
        audit_flags.append("pymupdf_failed")
        audit_warnings.append(f"PyMuPDF fast scan failed: {e}")
        try:
            with pdfplumber.open(file_path) as pdf:
                total_pages = len(pdf.pages)
                for i, page in enumerate(pdf.pages, start=1):
                    scanned_pages.add(i)
                    page_text = page.extract_text() or ""
                    page_texts[i] = page_text
                    if not page_text:
                        continue
                    text_pages.add(i)
                    texts.extend(_split_h_text(page_text, i))
                    if _is_h_financial_page(page_text):
                        financial_pages.add(i)
                    if _is_h_statement_page(page_text):
                        statement_pages.add(i)
        except Exception as fallback_e:
            audit_flags.append("pdfplumber_text_failed")
            audit_warnings.append(f"pdfplumber text fallback failed: {fallback_e}")
        if not financial_pages:
            financial_pages = set(range(1, total_pages + 1))
        if not statement_pages:
            statement_pages = set(range(1, total_pages + 1))
    finally:
        if fallback_pdf is not None:
            try:
                fallback_pdf.close()
            except Exception:  # pragma: no cover
                pass

    # 传播章节类型到延续页（BS/PL/CF/Equity 通常跨多页）
    _propagate_statement_sections(texts)

    # ---- 阶段 2: pdfplumber 提取表格 ----
    pages_to_extract = _select_h_table_candidate_pages(
        total_pages=total_pages,
        statement_pages=statement_pages,
        financial_pages=financial_pages,
        page_texts=page_texts,
        texts=texts,
    )
    logger.info(
        f"H 股候选表格页: {len(pages_to_extract)} 页（主表 {len(statement_pages)} 页，财务 {len(financial_pages)} 页）"
    )

    core_section_pages = _section_pages(texts, _H_CORE_SECTIONS)
    pdfplumber_default_pages: set[int] = set()
    pdfplumber_text_pages: set[int] = set()

    if pages_to_extract:
        with pdfplumber.open(file_path) as pdf:
            for i in pages_to_extract:
                if i > len(pdf.pages):
                    continue
                page = pdf.pages[i - 1]
                page_text = page_texts.get(i, "")
                page_tables: list[list[list[str | None]]] = []
                try:
                    page_tables = page.extract_tables() or []
                    page_tables = [t for t in page_tables if _is_useful_h_raw_table(t)]
                except Exception as e:
                    audit_flags.append("table_page_failed")
                    audit_warnings.append(f"Table extraction failed on page {i}: {e}")
                if page_tables:
                    pdfplumber_default_pages.add(i)
                if not page_tables and (i in core_section_pages or _looks_like_table_page(page_text)):
                    try:
                        page_tables = page.extract_tables(table_settings=_H_PDFPLUMBER_TEXT_SETTINGS) or []
                        page_tables = [t for t in page_tables if _is_useful_h_raw_table(t)]
                        if page_tables:
                            pdfplumber_text_pages.add(i)
                    except Exception as e:
                        audit_flags.append("table_page_failed")
                        audit_warnings.append(f"Text-strategy table extraction failed on page {i}: {e}")
                if page_tables:
                    table_page_nums.add(i)
                    for j, t in enumerate(page_tables, start=1):
                        source = "text" if i in pdfplumber_text_pages and i not in pdfplumber_default_pages else "pl"
                        ft = _convert_h_table(t, i, j, f"H_p{i:03d}_{source}_t{j:02d}")
                        tables.append(ft)

    logger.info(f"H 股 pdfplumber 提取: {len(tables)} 个表格({len(table_page_nums)} 个财务页)")

    initial_coverage = _build_h_table_coverage(texts, table_page_nums, page_texts=page_texts, tables=tables)
    missing_core_pages = _missing_core_pages(texts, initial_coverage)

    camelot_attempted = False
    camelot_added_tables = 0
    ppstructure_attempted = False
    ppstructure_added_tables = 0
    pp_pages: list[int] = []

    camelot_pages = set(missing_core_pages)
    if not camelot_pages and len(tables) < 15:
        camelot_pages = {
            page
            for page in pages_to_extract
            if page in core_section_pages or _looks_like_table_page(page_texts.get(page, ""))
        }
        if not camelot_pages:
            camelot_pages = set(pages_to_extract)

    if camelot_pages:
        camelot_attempted = True
        try:
            page_range = _page_range_string(camelot_pages)
            camelot_tables = extract_tables_camelot(file_path, page_range=page_range, use_lattice=True)
            before = len(tables)
            tables = merge_tables(tables, camelot_tables)
            camelot_added_tables = len(tables) - before
            table_page_nums.update(t.page for t in tables)
            logger.info(f"camelot 补充提取: {camelot_added_tables} 个新表格")
        except Exception as e:
            audit_flags.append("camelot_failed")
            audit_warnings.append(f"camelot table extraction failed: {e}")
            logger.warning(f"camelot 补充提取失败: {e}")
    else:
        logger.info(f"pdfplumber 已提取 {len(tables)} 个表格，跳过 camelot 补充")

    coverage_after_camelot = _build_h_table_coverage(texts, table_page_nums, page_texts=page_texts, tables=tables)
    missing_core_pages = _missing_core_pages(texts, coverage_after_camelot)

    if missing_core_pages:
        ppstructure_attempted = True
        pp_pages = sorted(missing_core_pages)[:24]
        try:
            pp_tables = extract_tables_ppstructure(file_path, pages=pp_pages)
            before = len(tables)
            tables = merge_tables(tables, pp_tables)
            ppstructure_added_tables = len(tables) - before
            table_page_nums.update(t.page for t in tables)
            if ppstructure_added_tables:
                logger.info(f"PPStructure 补充提取: {ppstructure_added_tables} 个新表格")
        except Exception as e:
            audit_flags.append("ppstructure_failed")
            audit_warnings.append(f"PPStructure table extraction failed: {e}")
            logger.warning(f"PPStructure 兜底提取失败: {e}")

    primary_lang = _detect_primary_language(texts)
    unit, currency = _detect_h_unit_currency(texts)
    for t in tables:
        if not t.unit:
            t.unit = unit
        if not t.currency:
            t.currency = currency

    table_page_nums.update(t.page for t in tables)
    final_coverage = _build_h_table_coverage(texts, table_page_nums, page_texts=page_texts, tables=tables)
    if final_coverage.get("missing_core_sections") and camelot_attempted and camelot_added_tables == 0:
        if "camelot_no_tables" not in audit_flags:
            audit_flags.append("camelot_no_tables")
            audit_warnings.append("Camelot supplement produced no tables while core statement tables remain incomplete.")

    engine_info = {
        "text": "pymupdf",
        "tables": ["pdfplumber", "pdfplumber_text", "camelot_lattice", "ppstructure"],
        "statement_pages": len(statement_pages),
        "financial_pages": len(financial_pages),
        "candidate_page_count": len(pages_to_extract),
        "candidate_pages": pages_to_extract,
        "pdfplumber_default_pages": sorted(pdfplumber_default_pages),
        "pdfplumber_text_pages": sorted(pdfplumber_text_pages),
        "camelot": {
            "attempted": camelot_attempted,
            "pages": sorted(camelot_pages),
            "added_tables": camelot_added_tables,
        },
        "ppstructure": {
            "attempted": ppstructure_attempted,
            "pages": pp_pages,
            "added_tables": ppstructure_added_tables,
        },
        "table_count": len(tables),
        "table_pages": sorted(table_page_nums),
    }
    logger.info(f"H 股解析完成: {total_pages} 页, {len(tables)} 表({len(table_page_nums)} 个财务页), {len(texts)} 文本段, 语言={primary_lang.value}")
    doc = ReportDocument(
        doc_id=Path(file_path).stem,
        side=ReportSide.H_SHARE,
        file_path=file_path,
        total_pages=total_pages,
        primary_language=primary_lang,
        tables=tables,
        texts=texts,
        charts=[],
        metadata={
            "unit": unit,
            "currency": currency.value if currency else None,
            "extraction_engines": engine_info,
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
        engines=engine_info,
        table_coverage=final_coverage,
    )
    return attach_audit(doc, audit)


def _parse_h_html(file_path: str) -> ReportDocument:
    """H 股 HTML 模式（港交所披露易网页年报）。"""
    logger.info(f"解析 H 股 HTML: {file_path}")

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("BeautifulSoup 未安装，无法解析 HTML")
        return _empty_doc(file_path)

    content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(content, "html.parser")

    tables: list[FinancialTable] = []
    texts: list[TextSegment] = []

    # 提取所有 table
    for idx, html_table in enumerate(soup.find_all("table"), start=1):
        ft = _html_table_to_financial(html_table, idx)
        if ft:
            tables.append(ft)

    # 提取文本段落
    for idx, p in enumerate(soup.find_all(["p", "div", "h1", "h2", "h3"]), start=1):
        text = p.get_text(strip=True)
        if len(text) < 5:
            continue
        section = _detect_h_section(text)
        texts.append(
            TextSegment(
                segment_id=f"H_html_s{idx:03d}",
                page=1,
                bbox=(0.0, 0.0, 0.0, 0.0),
                text=text,
                language=Language.EN,
                section=section,
            )
        )

    primary_lang = _detect_primary_language(texts)
    unit, currency = _detect_h_unit_currency(texts)
    for t in tables:
        if not t.unit:
            t.unit = unit
        if not t.currency:
            t.currency = currency

    doc = ReportDocument(
        doc_id=Path(file_path).stem,
        side=ReportSide.H_SHARE,
        file_path=file_path,
        total_pages=1,
        primary_language=primary_lang,
        tables=tables,
        texts=texts,
        charts=[],
        metadata={
            "unit": unit,
            "currency": currency.value if currency else None,
            "extraction_engines": {"text": "beautifulsoup", "tables": ["beautifulsoup"], "format": "html"},
            "table_count": len(tables),
        },
    )
    audit = build_extraction_audit(
        total_pages=1,
        scanned_pages=[1],
        text_pages=[1] if texts else [],
        table_pages=[1] if tables else [],
        text_segments=texts,
        warning_flags=[],
        warnings=[],
        engines={"text": "beautifulsoup", "tables": ["beautifulsoup"], "format": "html", "table_count": len(tables)},
    )
    return attach_audit(doc, audit)


def parse_h_html(file_path: str) -> ReportDocument:
    """Public HTML parser entry point."""
    return _parse_h_html(file_path)


def _html_table_to_financial(html_table, idx: int) -> FinancialTable | None:
    """BeautifulSoup table 转 FinancialTable。"""
    rows = html_table.find_all("tr")
    if not rows:
        return None

    cells: list[TableCell] = []
    title = ""

    caption = html_table.find("caption")
    if caption:
        title = caption.get_text(strip=True)
    else:
        prev = html_table.find_previous(["h2", "h3", "h4", "p"])
        if prev:
            title = prev.get_text(strip=True)[:50]

    for r_idx, row in enumerate(rows):
        cols = row.find_all(["td", "th"])
        for c_idx, col in enumerate(cols):
            text = col.get_text(strip=True)
            is_header = col.name == "th" or r_idx == 0
            cells.append(TableCell(row=r_idx, col=c_idx, text=text, is_header=is_header))

    return FinancialTable(
        table_id=f"H_html_t{idx:02d}",
        title=LocalizedString(en=title),
        page=1,
        bbox=(0.0, 0.0, 0.0, 0.0),
        cells=cells,
    )


def _split_h_text(page_text: str, page_num: int) -> list[TextSegment]:
    """将页面文本按空行切分，标记 section。"""
    raw_segments = re.split(r"\n{2,}", page_text.strip())
    segments: list[TextSegment] = []
    current_section: str | None = None

    for idx, seg in enumerate(raw_segments):
        seg = seg.strip()
        if not seg:
            continue

        detected = _detect_h_section(seg)
        if detected:
            current_section = detected

        clean_text = re.sub(r"\s+", " ", seg.replace("\n", " "))
        if len(clean_text) < 5:
            continue

        # 保留排版结构的 raw_text：仅压缩连续空行，保留段落内换行和空格
        # 供 LLM 翻译比对使用——LLM 能利用换行信息理解表格/多栏布局
        raw_text = re.sub(r"\n{3,}", "\n\n", seg.strip())

        lang = _detect_segment_language(clean_text)

        segments.append(
            TextSegment(
                segment_id=f"H_p{page_num:03d}_s{idx:02d}",
                page=page_num,
                bbox=(0.0, 0.0, 0.0, 0.0),
                text=clean_text,
                language=lang,
                section=current_section,
                raw_text=raw_text,
            )
        )
    return segments


# 特定报表名称（优先级最高）
_STATEMENT_KEYWORDS: dict[str, str] = {
    # 繁体中文
    "資產負債表": "bs",
    "財務狀況表": "bs",
    "財務狀況": "bs",
    "損益表": "pl",
    "綜合損益表": "pl",
    "綜合收益表": "pl",
    "收益表": "pl",
    "現金流量表": "cf",
    "權益變動表": "equity",
    "股東權益變動表": "equity",
    "所有者權益變動表": "equity",
    # 简体中文（部分H股含简体）
    "资产负债表": "bs",
    "利润表": "pl",
    "现金流量表": "cf",
    "所有者权益变动表": "equity",
    "股东权益变动表": "equity",
    # 英文（常见变体）
    "statement of financial position": "bs",
    "balance sheet": "bs",
    "statement of profit or loss": "pl",
    "income statement": "pl",
    "statement of comprehensive income": "pl",
    "comprehensive income statement": "pl",
    "statement of cash flows": "cf",
    "cash flow statement": "cf",
    "statement of changes in equity": "equity",
    "changes in equity statement": "equity",
}


def _detect_h_section(text: str) -> str | None:
    """检测章节类型。优先匹配特定报表名称，再匹配其他关键词。"""
    lower = text[:200].lower()

    # 优先级1：特定报表名称（资产负债表/损益表/现金流量表/权益变动表）
    for kw, code in _STATEMENT_KEYWORDS.items():
        if kw in lower:
            # 排除分析/讨论页面（如"財務狀況表分析"、"現金流量表分析"）
            if any(x in lower for x in ("分析", "討論與分析", "management discussion")):
                return "mda"
            return code

    # 优先级2：其他章节关键词
    for keyword, section_code in H_SECTION_KEYWORDS.items():
        if keyword in lower:
            return section_code
    return None


def _propagate_statement_sections(texts: list[TextSegment]) -> None:
    """将 bs/pl/cf/equity 章节类型向延续页传播。

    H 股年报中，资产负债表/损益表等通常跨多页，只有首页有标题。
    此函数将首页的 section 传播到后续延续页（直到遇到下一个特定报表页）。
    """
    # 收集已有特定 section 的页码
    page_sections: dict[int, str] = {}
    for t in texts:
        if t.section in ("bs", "pl", "cf", "equity"):
            page_sections[t.page] = t.section

    sorted_pages = sorted(page_sections.keys())
    for i, page in enumerate(sorted_pages):
        section = page_sections[page]
        # 传播到下一个特定报表页之前，最多5页（安全限制）
        next_specific = sorted_pages[i + 1] if i + 1 < len(sorted_pages) else page + 100
        next_boundary = min(page + 6, next_specific)
        for next_page in range(page + 1, next_boundary):
            next_texts = [t for t in texts if t.page == next_page]
            if not next_texts:
                continue
            # 如果下一页已有自己的特定 section，停止传播
            has_specific = any(t.section in ("bs", "pl", "cf", "equity") for t in next_texts)
            if has_specific:
                break
            for t in next_texts:
                if t.section in (None, "financial_statements"):
                    t.section = section


def _is_english_dominant(text: str) -> bool:
    """判断文本是否以英文为主（ASCII 字符占比 > 60%）。"""
    if not text:
        return False
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / len(text) > 0.6


def _is_chinese_dominant(text: str) -> bool:
    """判断文本是否以中文为主（CJK 字符占比 > 30%）。"""
    if not text:
        return False
    # CJK Unified Ideographs + 扩展A区
    cjk_chars = sum(
        1 for c in text
        if ("一" <= c <= "鿿") or ("㐀" <= c <= "䶿")
    )
    return cjk_chars / len(text) > 0.30


def _detect_segment_language(text: str) -> Language:
    """检测单个文本段的语言。"""
    if _is_chinese_dominant(text):
        return Language.ZH
    if _is_english_dominant(text):
        return Language.EN
    return Language.BILINGUAL


def _detect_primary_language(texts: list[TextSegment]) -> Language:
    """检测整份报告的主要语言。"""
    if not texts:
        return Language.EN
    en_count = sum(1 for t in texts if t.language == Language.EN)
    zh_count = sum(1 for t in texts if t.language == Language.ZH)
    bi_count = sum(1 for t in texts if t.language == Language.BILINGUAL)
    if zh_count > en_count and zh_count > bi_count:
        return Language.ZH
    if en_count > zh_count and en_count > bi_count:
        return Language.EN
    return Language.BILINGUAL


def _is_h_garbled(text: str) -> bool:
    """检测 H 股文本是否为乱码。"""
    if not text or len(text) < 10:
        return False
    replacement_chars = text.count("�")
    if replacement_chars > 0:
        return True
    # H 股以英文为主，检查可读字符比例
    readable_chars = len(re.findall(r"[a-zA-Z一-鿿]", text))
    total_chars = len(text.strip())
    if total_chars > 50 and readable_chars / total_chars < 0.1:
        return True
    return False


def _is_h_garbled_loose(text: str, threshold: float = 0.05) -> bool:
    """宽松的乱码检测，主要用于 OCR 兜底结果。

    OCR 对扫描件/低质量页面识别率较低，但只要有少量可读文本就值得保留，
    避免因严格阈值把尚可利用的 OCR 结果丢弃。
    """
    if not text or len(text) < 5:
        return False
    if text.count("�") > 0:
        return True
    readable_chars = len(re.findall(r"[a-zA-Z一-鿿]", text))
    total_chars = len(text.strip())
    if total_chars > 30 and readable_chars / total_chars < threshold:
        return True
    return False


def _detect_h_unit_currency(texts: list[TextSegment]) -> tuple[str | None, Currency | None]:
    """从文本中检测金额单位和币种（H 股版 — 支持繁体中文）。

    策略：优先扫描财务报表相关章节的单位声明，避免被正文中的外币描述干扰。
    遇到乱码文本时跳过。
    """
    # 第1优先级：报表单位声明（带上下文，避免正文叙述误匹配）
    # RMB 模式优先于 HKD/USD，避免正文提及子公司币种时误匹配
    rmb_patterns = [
        (r"除另有說明外[，,]?\s*.*以人民幣千元列示", "RMB thousand", Currency.CNY),
        (r"除另有说明外[，,]?\s*.*以人民币千元列示", "RMB thousand", Currency.CNY),
        (r"除另有說明外[，,]?\s*.*以人民幣百萬元列示", "RMB million", Currency.CNY),
        (r"除另有说明外[，,]?\s*.*以人民币百万元列示", "RMB million", Currency.CNY),
        (r"除另有說明外[，,]?\s*.*以人民幣元列示", "RMB 元", Currency.CNY),
        (r"除另有说明外[，,]?\s*.*以人民币元列示", "RMB 元", Currency.CNY),
        (r"以人民幣千元列示", "RMB thousand", Currency.CNY),
        (r"以人民币千元列示", "RMB thousand", Currency.CNY),
        (r"以人民幣百萬元列示", "RMB million", Currency.CNY),
        (r"以人民币百万元列示", "RMB million", Currency.CNY),
        (r"以人民幣元列示", "RMB 元", Currency.CNY),
        (r"以人民币元列示", "RMB 元", Currency.CNY),
        (r"人民幣千元", "RMB thousand", Currency.CNY),
        (r"人民币千元", "RMB thousand", Currency.CNY),
        (r"人民幣百萬元", "RMB million", Currency.CNY),
        (r"人民币百万元", "RMB million", Currency.CNY),
        (r"人民幣億元", "RMB 亿元", Currency.CNY),
        (r"人民币亿元", "RMB 亿元", Currency.CNY),
        (r"人民幣元", "RMB 元", Currency.CNY),
        (r"人民币元", "RMB 元", Currency.CNY),
        (r"RMB['\"]?\s*million", "RMB million", Currency.CNY),
        (r"RMB['\"]?\s*thousand", "RMB thousand", Currency.CNY),
    ]

    hkd_patterns = [
        (r"港元千元", "HK$ thousand", Currency.HKD),
        (r"港幣千元", "HK$ thousand", Currency.HKD),
        (r"HK\$['\"]?\s*million", "HK$ million", Currency.HKD),
        (r"HK\$['\"]?\s*thousand", "HK$ thousand", Currency.HKD),
    ]

    usd_patterns = [
        (r"US\$['\"]?\s*million", "US$ million", Currency.USD),
        (r"US\$['\"]?\s*thousand", "US$ thousand", Currency.USD),
        (r"美元", "美元", Currency.USD),
    ]

    # 注意：港幣/港元/港币 极易在正文误匹配（如"H股以港元计价"），
    # 只在财务主表章节（bs/pl/cf/equity）中匹配，其他位置忽略
    hkd_loose_patterns = [
        (r"港幣", "港元", Currency.HKD),
        (r"港币", "港元", Currency.HKD),
        (r"港[\s]*元", "港元", Currency.HKD),
        (r"HKD", "港元", Currency.HKD),
    ]

    # 第2优先级：含数字的金额描述（正文容易误报，仅用于财务主表）
    numeric_patterns = [
        (r"RMB['\"]?\s*(\d+)\s*million", "RMB million", Currency.CNY),
        (r"RMB['\"]?\s*(\d+)\s*thousand", "RMB thousand", Currency.CNY),
        (r"HK\$['\"]?\s*(\d+)\s*million", "HK$ million", Currency.HKD),
        (r"HK\$['\"]?\s*(\d+)\s*thousand", "HK$ thousand", Currency.HKD),
        (r"US\$['\"]?\s*(\d+)\s*million", "US$ million", Currency.USD),
        (r"人民幣\s*(\d+)\s*百萬元", "RMB million", Currency.CNY),
        (r"人民幣\s*(\d+)\s*億元", "RMB 亿元", Currency.CNY),
        (r"人民幣\s*(\d+)\s*千元", "RMB thousand", Currency.CNY),
        (r"人民幣\s*(\d+)\s*萬元", "RMB 万元", Currency.CNY),
        (r"人民币\s*(\d+)\s*百万元", "RMB million", Currency.CNY),
        (r"人民币\s*(\d+)\s*亿元", "RMB 亿元", Currency.CNY),
        (r"人民币\s*(\d+)\s*千元", "RMB thousand", Currency.CNY),
        (r"人民币\s*(\d+)\s*万元", "RMB 万元", Currency.CNY),
        (r"港元\s*(\d+)\s*百万元", "HK$ million", Currency.HKD),
        (r"港元\s*(\d+)\s*千元", "HK$ thousand", Currency.HKD),
    ]

    # 阶段1: 优先扫描财务报表主表章节（bs/pl/cf/equity），先RMB后HKD/USD
    financial_sections = {"bs", "pl", "cf", "equity", "financial_statements", "notes"}
    financial_texts = [t for t in texts if t.section in financial_sections and not _is_h_garbled(t.text)]
    for patterns in (rmb_patterns, hkd_patterns, usd_patterns, hkd_loose_patterns):
        for t in financial_texts:
            for pattern, unit_str, curr in patterns:
                if re.search(pattern, t.text, re.IGNORECASE):
                    return unit_str, curr

    # 阶段2: 扫描全部文本段，只用 RMB 严格模式 + HKD/USD 严格模式
    # 不再扫描 hkd_loose_patterns，避免正文误匹配
    for t in texts:
        if _is_h_garbled(t.text):
            continue
        for patterns in (rmb_patterns, hkd_patterns, usd_patterns):
            for pattern, unit_str, curr in patterns:
                if re.search(pattern, t.text, re.IGNORECASE):
                    return unit_str, curr
        for pattern, unit_str, curr in numeric_patterns:
            if re.search(pattern, t.text, re.IGNORECASE):
                return unit_str, curr

    # 默认：H股年报通常使用 RMB thousand（ mainland 公司）
    return "RMB thousand", Currency.CNY


def _convert_h_table(raw_table: list[list[str | None]], page: int, table_idx: int, table_id: str) -> FinancialTable:
    """将 pdfplumber 原始表格转为 FinancialTable（H 股版）。"""
    cells: list[TableCell] = []
    title = ""
    if raw_table and raw_table[0]:
        title = " ".join(str(c) for c in raw_table[0] if c)[:50]

    for r_idx, row in enumerate(raw_table):
        for c_idx, cell in enumerate(row):
            text = str(cell or "").strip()
            is_header = r_idx == 0 or any(
                k in text.lower()
                for k in ("notes", "item", "amount", "202", "consolidated", "total")
            )
            cells.append(TableCell(row=r_idx, col=c_idx, text=text, is_header=is_header))

    # 从标题推断 section
    section = None
    inferred = _infer_h_core_sections_from_text(title)
    if inferred:
        section = sorted(inferred)[0]

    return FinancialTable(
        table_id=table_id,
        title=LocalizedString(en=title),
        page=page,
        bbox=(0.0, 0.0, 0.0, 0.0),
        cells=cells,
        section=section,
    )


def _empty_doc(file_path: str) -> ReportDocument:
    """返回空的 ReportDocument（降级）。"""
    doc = ReportDocument(
        doc_id=Path(file_path).stem,
        side=ReportSide.H_SHARE,
        file_path=file_path,
        total_pages=0,
        primary_language=Language.EN,
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
        warnings=["H-share parser returned an empty document."],
        engines={},
    )
    return attach_audit(doc, audit)
