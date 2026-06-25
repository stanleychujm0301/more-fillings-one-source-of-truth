"""报告结构提取 — 从已解析的文本中提取章节层级结构。

策略：
1. 利用 parser 已标记的 section 代码（bs, pl, cf, mda, notes 等）
2. 按 section 分组，计算每个 section 的页码范围
3. 构建章节层级树
"""

from __future__ import annotations

from ahcc.profile.models import ChapterNode
from ahcc.schemas import LocalizedString, ReportDocument


# section 代码 → 中英文标题映射
_SECTION_TITLES = {
    "bs": LocalizedString(zh="合并资产负债表", en="Consolidated Statement of Financial Position"),
    "pl": LocalizedString(zh="合并利润表", en="Consolidated Statement of Profit or Loss"),
    "cf": LocalizedString(zh="合并现金流量表", en="Consolidated Statement of Cash Flows"),
    "equity": LocalizedString(zh="合并所有者权益变动表", en="Consolidated Statement of Changes in Equity"),
    "notes": LocalizedString(zh="财务报表附注", en="Notes to the Financial Statements"),
    "mda": LocalizedString(zh="管理层讨论与分析", en="Management Discussion and Analysis"),
    "esg": LocalizedString(zh="环境、社会及管治", en="Environmental, Social and Governance"),
    "governance": LocalizedString(zh="公司治理", en="Corporate Governance"),
    "related_party": LocalizedString(zh="关联方及关联交易", en="Related Party Transactions"),
    "risk": LocalizedString(zh="风险管理", en="Risk Management"),
    "segment": LocalizedString(zh="分部报告", en="Segment Reporting"),
    "commitments": LocalizedString(zh="承诺及或有事项", en="Commitments and Contingencies"),
    "goodwill": LocalizedString(zh="商誉", en="Goodwill"),
    "tax": LocalizedString(zh="所得税", en="Income Tax"),
    "employees": LocalizedString(zh="员工", en="Employees"),
    "financial_statements": LocalizedString(zh="财务报表", en="Financial Statements"),
    "company_profile": LocalizedString(zh="公司概况", en="Company Profile"),
    "directors_report": LocalizedString(zh="董事会报告", en="Directors' Report"),
    "corporate_governance": LocalizedString(zh="公司治理", en="Corporate Governance"),
    "significant_events": LocalizedString(zh="重要事项", en="Significant Events"),
    "share_changes": LocalizedString(zh="股份变动及股东情况", en="Share Changes and Shareholders"),
    "preference_shares": LocalizedString(zh="优先股相关情况", en="Preference Shares"),
    "bonds": LocalizedString(zh="债券相关情况", en="Bonds"),
    "accounting_policy": LocalizedString(zh="会计政策", en="Accounting Policies"),
    "accounting_estimate": LocalizedString(zh="会计估计", en="Accounting Estimates"),
    "segment_report": LocalizedString(zh="分部报告", en="Segment Reporting"),
    "leases": LocalizedString(zh="租赁", en="Leases"),
    "financial_instruments": LocalizedString(zh="金融工具", en="Financial Instruments"),
    "revenue": LocalizedString(zh="收入", en="Revenue"),
    "cash_equivalents": LocalizedString(zh="现金及现金等价物", en="Cash and Cash Equivalents"),
    "eps": LocalizedString(zh="每股收益", en="Earnings per Share"),
}


_SECTION_ALIASES = {
    "governance": "corporate_governance",
    "segment": "segment_report",
    "basic_eps": "eps",
    "diluted_eps": "eps",
    "cfo": "cf",
    "cfi": "cf",
    "cff": "cf",
}


_TREE_GROUPS: tuple[tuple[str, LocalizedString, tuple[str, ...]], ...] = (
    (
        "company_overview",
        LocalizedString(zh="公司与业务概览", en="Company and Business Overview"),
        ("company_profile", "mda", "directors_report", "risk"),
    ),
    (
        "governance_and_shareholders",
        LocalizedString(zh="治理与股东信息", en="Governance and Shareholders"),
        ("corporate_governance", "share_changes", "preference_shares", "bonds", "significant_events"),
    ),
    (
        "financial_statements_tree",
        LocalizedString(zh="财务报表及附注", en="Financial Statements and Notes"),
        (
            "financial_statements",
            "bs",
            "pl",
            "cf",
            "equity",
            "notes",
            "accounting_policy",
            "accounting_estimate",
            "revenue",
            "segment_report",
            "related_party",
            "goodwill",
            "tax",
            "eps",
            "leases",
            "financial_instruments",
            "commitments",
            "employees",
            "cash_equivalents",
        ),
    ),
    (
        "esg_and_social",
        LocalizedString(zh="环境、社会及责任", en="ESG and Social Responsibility"),
        ("esg",),
    ),
)


def extract_structure(doc: ReportDocument) -> list[ChapterNode]:
    """从 ReportDocument 中提取章节结构。

    返回章节列表，每个节点包含页码范围和子节点。
    """
    # 按 section 分组，收集页码。section 先做归一化，避免 A/H 命名小差异导致结构树误判。
    section_pages: dict[str, set[int]] = {}
    for seg in doc.texts:
        if seg.section:
            code = _normalize_section_code(seg.section)
            section_pages.setdefault(code, set()).add(seg.page)

    if not section_pages:
        return []

    # 构建递归章节树：顶层为报告披露域，第二层为解析出的章节/附注。
    nodes: list[ChapterNode] = []
    assigned: set[str] = set()
    for group_code, group_title, child_codes in _TREE_GROUPS:
        children: list[ChapterNode] = []
        for sec_code in child_codes:
            pages = section_pages.get(sec_code)
            if not pages:
                continue
            children.append(_make_node(sec_code, pages, level=2))
            assigned.add(sec_code)
        if not children:
            continue
        page_start = min(child.page_start for child in children)
        page_end = max(child.page_end for child in children)
        nodes.append(
            ChapterNode(
                title=group_title,
                section_code=group_code,
                page_start=page_start,
                page_end=page_end,
                children=sorted(children, key=lambda node: (node.page_start, node.section_code or "")),
                level=1,
            )
        )

    remaining = sorted(
        ((sec_code, pages) for sec_code, pages in section_pages.items() if sec_code not in assigned),
        key=lambda item: min(item[1]),
    )
    if remaining:
        children = [_make_node(sec_code, pages, level=2) for sec_code, pages in remaining]
        nodes.append(
            ChapterNode(
                title=LocalizedString(zh="其他披露", en="Other Disclosures"),
                section_code="other_disclosures",
                page_start=min(child.page_start for child in children),
                page_end=max(child.page_end for child in children),
                children=children,
                level=1,
            )
        )

    return sorted(nodes, key=lambda node: node.page_start)


def _normalize_section_code(section_code: str) -> str:
    code = section_code.strip()
    return _SECTION_ALIASES.get(code, code)


def _make_node(sec_code: str, pages: set[int], level: int) -> ChapterNode:
    title = _SECTION_TITLES.get(sec_code, LocalizedString(zh=sec_code, en=sec_code))
    return ChapterNode(
        title=title,
        section_code=sec_code,
        page_start=min(pages),
        page_end=max(pages),
        level=level,
    )
