"""Stable topic taxonomy for A/H report profiles.

The profile layer uses this taxonomy to keep narrative blocks and
section-level comparisons deterministic across A/H reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from typing import Iterable

from ahcc.align.glossary import to_simplified


@dataclass(frozen=True)
class TopicDef:
    topic_id: str
    name_zh: str
    name_en: str
    keywords_zh: tuple[str, ...] = ()
    keywords_en: tuple[str, ...] = ()
    section_codes: tuple[str, ...] = ()
    parent: str | None = None


TOPIC_TAXONOMY: dict[str, TopicDef] = {
    "company_profile": TopicDef(
        "company_profile", "公司概况", "Company profile",
        ("公司概况", "公司简介", "主要财务指标", "corporate information", "financial highlights"),
        ("company profile", "corporate information", "financial highlights"),
        ("company_profile",),
    ),
    "mda_business_review": TopicDef(
        "mda_business_review", "业务回顾", "Business review",
        ("业务回顾", "经营情况讨论与分析", "经营情况", "主要业务", "主要经营", "收入构成", "业务表现"),
        ("business review", "operating review", "management discussion and analysis", "operating performance"),
        ("mda", "directors_report"),
        "mda",
    ),
    "mda_financial_analysis": TopicDef(
        "mda_financial_analysis", "财务分析", "Financial analysis",
        ("财务分析", "盈利能力", "资产负债", "现金流", "资本充足", "净资本"),
        ("financial analysis", "financial review", "performance analysis"),
        ("mda", "directors_report"),
        "mda",
    ),
    "mda_risk_management": TopicDef(
        "mda_risk_management", "风险管理", "Risk management",
        ("风险管理", "信用风险", "市场风险", "流动性风险", "操作风险", "风险控制", "内部控制"),
        ("risk management", "risk factors", "internal control", "risk control"),
        ("mda", "corporate_governance", "directors_report"),
        "mda",
    ),
    "mda_outlook": TopicDef(
        "mda_outlook", "未来展望", "Outlook",
        ("未来展望", "展望", "发展策略", "经营计划", "前景"),
        ("outlook", "prospects", "future outlook", "business outlook"),
        ("mda", "directors_report"),
        "mda",
    ),
    "corporate_governance": TopicDef(
        "corporate_governance", "公司治理", "Corporate governance",
        ("公司治理", "企业管治", "董事会", "监事会", "审计委员会", "薪酬委员会"),
        ("corporate governance", "board of directors", "audit committee", "remuneration committee"),
        ("corporate_governance",),
    ),
    "esg_environment": TopicDef(
        "esg_environment", "环境信息", "Environmental",
        ("环境", "碳排放", "温室气体", "排放", "环保", "节能", "environment", "emissions"),
        ("environmental", "carbon", "emissions", "greenhouse gas", "environment"),
        ("esg",),
        "esg",
    ),
    "esg_social": TopicDef(
        "esg_social", "社会责任", "Social",
        ("社会责任", "员工", "培训", "社区", "公益", "客户服务", "labor", "training"),
        ("social", "employees", "community", "labor", "safety"),
        ("esg",),
        "esg",
    ),
    "significant_events": TopicDef(
        "significant_events", "重要事项", "Significant events",
        ("重要事项", "重大事项", "诉讼", "仲裁", "关联交易", "承诺", "担保"),
        ("significant events", "material events", "contingencies", "related party"),
        ("significant_events",),
    ),
    "dividend_distribution": TopicDef(
        "dividend_distribution", "利润分配及股息", "Profit distribution and dividends",
        ("利润分配", "利潤分配", "现金股利", "現金股利", "现金分红", "現金分紅", "建议股息", "建議股息", "股利", "股息"),
        ("profit distribution", "profit appropriation", "cash dividend", "dividends", "proposed dividend", "total dividends"),
        ("significant_events", "notes", "equity"),
        "significant_events",
    ),
    "bond_events": TopicDef(
        "bond_events", "债券发行及偿还", "Bond issuance and repayment",
        ("债券", "債券", "公司债", "公司債", "短期融资券", "短期融資券", "收益凭证", "收益憑證", "发行金额", "發行金額", "偿还", "償還"),
        ("bond", "bonds", "notes", "issuance amount", "repayment", "redeemed"),
        ("significant_events", "notes"),
        "significant_events",
    ),
    "share_changes": TopicDef(
        "share_changes", "股份变动", "Share changes",
        ("股份变动", "股本变动", "股东情况", "持股情况", "股东名册"),
        ("share capital", "share changes", "shareholder", "shareholdings"),
        ("share_changes",),
    ),
    "financial_statements": TopicDef(
        "financial_statements", "财务报表", "Financial statements",
        ("财务报表", "合并报表", "financial statements"),
        ("financial statements", "consolidated financial statements"),
        ("financial_statements", "bs", "pl", "cf", "equity", "notes"),
    ),
    "accounting_policies": TopicDef(
        "accounting_policies", "会计政策", "Accounting policies",
        ("会计政策", "会计估计", "重要会计政策", "重要会计估计"),
        ("accounting policies", "accounting estimates"),
        ("accounting_policy", "accounting_estimate", "notes"),
        "financial_statements",
    ),
    "related_party": TopicDef(
        "related_party", "关联方交易", "Related party",
        ("关联方", "关联交易", "关联方交易", "关联方关系"),
        ("related party", "related party transactions"),
        ("related_party", "notes"),
        "financial_statements",
    ),
    "segment_report": TopicDef(
        "segment_report", "分部报告", "Segment report",
        ("分部报告", "经营分部", "业务分部"),
        ("segment reporting", "operating segments"),
        ("segment_report", "notes"),
        "financial_statements",
    ),
    "revenue": TopicDef(
        "revenue", "收入", "Revenue",
        ("收入", "营业收入", "经营收入", "手续费及佣金收入"),
        ("revenue", "income", "turnover", "commission income"),
        ("revenue", "pl", "notes"),
        "financial_statements",
    ),
    "inventories": TopicDef(
        "inventories", "存货", "Inventories",
        ("存货", "库存", "存货跌价"),
        ("inventories", "inventory"),
        ("inventories", "bs", "notes"),
        "financial_statements",
    ),
    "ppe": TopicDef(
        "ppe", "固定资产", "Property, plant and equipment",
        ("固定资产", "在建工程", "物业及设备"),
        ("property, plant and equipment", "ppe", "construction in progress"),
        ("ppe", "construction_in_progress", "bs", "notes"),
        "financial_statements",
    ),
    "intangible_assets": TopicDef(
        "intangible_assets", "无形资产", "Intangible assets",
        ("无形资产", "商标权", "专利权"),
        ("intangible assets",),
        ("intangible_assets", "bs", "notes"),
        "financial_statements",
    ),
    "investment_property": TopicDef(
        "investment_property", "投资性房地产", "Investment property",
        ("投资性房地产", "投资物业"),
        ("investment property",),
        ("investment_property", "bs", "notes"),
        "financial_statements",
    ),
    "goodwill": TopicDef(
        "goodwill", "商誉", "Goodwill",
        ("商誉", "goodwill"),
        ("goodwill",),
        ("goodwill", "notes"),
        "financial_statements",
    ),
    "tax": TopicDef(
        "tax", "所得税", "Income tax",
        ("所得税", "税项", "税费"),
        ("income tax", "tax"),
        ("income_tax", "notes"),
        "financial_statements",
    ),
    "eps": TopicDef(
        "eps", "每股收益", "Earnings per share",
        ("每股收益", "每股盈利", "基本每股收益", "稀释每股收益"),
        ("earnings per share", "basic earnings per share", "diluted earnings per share"),
        ("eps", "pl", "notes"),
        "financial_statements",
    ),
    "leases": TopicDef(
        "leases", "租赁", "Leases",
        ("租赁", "租賃", "使用权资产", "租赁负债"),
        ("leases", "lease liabilities", "right-of-use assets"),
        ("leases", "notes"),
        "financial_statements",
    ),
    "financial_instruments": TopicDef(
        "financial_instruments", "金融工具", "Financial instruments",
        ("金融工具", "公允价值", "衍生金融资产", "衍生金融负债"),
        ("financial instruments", "fair value", "derivative"),
        ("financial_instruments", "notes"),
        "financial_statements",
    ),
    "cash_flow": TopicDef(
        "cash_flow", "现金流量", "Cash flow",
        ("现金流量", "经营活动现金流量", "投资活动现金流量", "筹资活动现金流量"),
        ("cash flow", "cash flows", "operating activities", "investing activities", "financing activities"),
        ("cf", "notes"),
        "financial_statements",
    ),
    "equity": TopicDef(
        "equity", "所有者权益", "Equity",
        ("所有者权益", "股本", "资本公积", "盈余公积", "未分配利润", "少数股东权益"),
        ("equity", "share capital", "retained earnings", "minority interests"),
        ("equity", "bs", "notes"),
        "financial_statements",
    ),
    "uncategorized": TopicDef("uncategorized", "未分类", "Uncategorized"),
}


def _build_keyword_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for topic_id, topic in TOPIC_TAXONOMY.items():
        for kw in (*topic.keywords_zh, *topic.keywords_en, *topic.section_codes, topic.name_zh, topic.name_en):
            if not kw:
                continue
            for form in {kw, to_simplified(kw), kw.lower(), to_simplified(kw).lower()}:
                # index 是 defaultdict(list)，无需手动初始化
                if topic_id not in index[form]:
                    index[form].append(topic_id)
    return index


KEYWORD_INDEX = _build_keyword_index()


def _score_topics(text: str) -> dict[str, float]:
    simplified = to_simplified(text)
    lowered = simplified.lower()
    scores: dict[str, float] = defaultdict(float)
    for keyword, topic_ids in KEYWORD_INDEX.items():
        if len(keyword) < 2:
            continue
        if keyword in lowered:
            boost = min(len(keyword), 12) / 3.0
            for topic_id in topic_ids:
                scores[topic_id] += boost
    return scores


def get_topic_for_text(text: str, max_topics: int = 3) -> list[str]:
    """Return the most likely topic ids for a text segment."""
    if not text or not text.strip():
        return ["uncategorized"]
    scores = _score_topics(text)
    if not scores:
        return ["uncategorized"]
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    selected = [topic_id for topic_id, score in ordered if score > 0][:max_topics]
    return selected or ["uncategorized"]


def get_topic_name(topic_id: str, lang: str = "zh") -> str:
    topic = TOPIC_TAXONOMY.get(topic_id)
    if not topic:
        return topic_id
    if lang.startswith("en"):
        return topic.name_en
    return topic.name_zh


def get_topics_for_section(section_code: str) -> list[str]:
    if not section_code:
        return ["uncategorized"]
    matched = [topic_id for topic_id, topic in TOPIC_TAXONOMY.items() if section_code in topic.section_codes]
    return matched or ["uncategorized"]


def iter_topic_defs() -> Iterable[TopicDef]:
    return TOPIC_TAXONOMY.values()
