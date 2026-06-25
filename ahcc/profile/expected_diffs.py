"""预期差异白名单 — 已知的A/H单边合规披露。

这些差异不是因为错误或遗漏，而是因为A股和H股监管要求不同。
标记为预期的差异会被降级为INFO，避免审计师误判。
"""

from __future__ import annotations

from ahcc.schemas import ReportSide


# ============================================================
# 数值指标白名单
# ============================================================

# canonical_key -> (通常披露/更详细披露在哪边, 解释)
EXPECTED_METRICS: dict[str, tuple[str, str]] = {
    "preferred_stock": ("A", "A股优先股披露要求，H股无此融资工具"),
    "corporate_bonds": ("A", "A股公司债章节，H股通常单独公告"),
    "esg_details": ("A", "A股ESG在年报中披露，H股通常有独立ESG报告"),
    "environmental": ("A", "A股环境信息披露在年报中，H股通常独立报告"),
    "social": ("A", "A股社会责任披露在年报中，H股通常独立报告"),
    # H股特有的
    "fair_value_hierarchy": ("H", "IFRS 13要求披露金融工具公允价值层级，CAS无此要求"),
    "lease_liabilities_detail": ("H", "IFRS 16比CAS披露更详细的租赁负债分解"),
    "share_based_payments": ("H", "IFRS 2要求更详细的股份支付披露"),
    "operating_segments": ("H", "IFRS 8要求更细的分部报告披露"),
    "foreign_currency_risk": ("H", "IFRS 7要求披露汇率风险敏感性分析"),
}


# ============================================================
# 叙述主题白名单
# ============================================================

# (关键词, 预期在哪边, 解释)
EXPECTED_TOPICS: list[tuple[str, str, str]] = [
    ("优先股", "A", "A股优先股披露要求，H股无此融资工具"),
    ("公司债券", "A", "A股公司债章节，H股通常单独公告"),
    ("环境社会责任", "A", "A股ESG在年报中披露，H股通常有独立ESG报告"),
    ("募集资金", "A", "A股IPO/再融资特有披露要求"),
    ("内部控制", "A", "A股特有的内控自我评价要求"),
    ("股东名册", "A", "A股特有的详细股东名册披露"),
    ("股份变动", "A", "A股特有的股份变动及股东情况"),
    ("ESG", "H", "H股ESG通常在独立报告中详细披露"),
    ("公允价值层级", "H", "IFRS 13要求披露金融工具公允价值层级，CAS无此要求"),
    ("租赁负债", "H", "IFRS 16比CAS披露更详细的租赁负债分解"),
    ("股份支付", "H", "IFRS 2要求更详细的股份支付披露"),
    ("业务分部", "H", "IFRS 8要求更细的分部报告披露"),
    ("汇率风险", "H", "IFRS 7要求披露汇率风险敏感性分析"),
    ("金融工具", "H", "HKFRS通常要求更详细的金融工具披露"),
]


EXPECTED_TOPIC_KEYS: dict[str, tuple[str, str]] = {
    "share_changes": ("A", "A股通常披露更完整的股份变动及股东情况"),
    "esg_environment": ("A", "A股年报内环境信息披露与H股独立ESG报告口径可能不同"),
    "esg_social": ("A", "A股年报内社会责任披露与H股独立ESG报告口径可能不同"),
    "segment_report": ("H", "IFRS 8要求更细的分部报告披露"),
    "leases": ("H", "IFRS 16比CAS披露更详细的租赁负债分解"),
    "financial_instruments": ("H", "IFRS 7/13要求更详细的金融工具及公允价值披露"),
}


# ============================================================
# 查询接口
# ============================================================

def is_expected_metric(canonical_key: str, missing_side: ReportSide) -> tuple[bool, str]:
    """判断某个数值指标的单边缺失是否为预期差异。

    Args:
        canonical_key: 指标的规范化key
        missing_side: 缺失的一边（H_SHARE 表示A有H无，A_SHARE 表示H有A无）

    Returns:
        (是否为预期差异, 解释文本)
    """
    expected = EXPECTED_METRICS.get(canonical_key)
    if not expected:
        return False, ""
    expected_side, rationale = expected
    # missing_side 是缺失的一边；若该项目通常只在另一边披露，则为预期差异。
    if (missing_side == ReportSide.H_SHARE and expected_side == "A") or \
       (missing_side == ReportSide.A_SHARE and expected_side == "H"):
        return True, rationale
    return False, ""


def is_expected_topic(
    topic_label: str,
    missing_side: ReportSide,
    topic_key: str | None = None,
) -> tuple[bool, str]:
    """判断某个叙述主题的单边缺失是否为预期差异。

    Args:
        topic_label: 动态主题名
        missing_side: 缺失的一边

    Returns:
        (是否为预期差异, 解释文本)
    """
    if topic_key:
        expected = EXPECTED_TOPIC_KEYS.get(topic_key)
        if expected:
            expected_side, rationale = expected
            if (missing_side == ReportSide.H_SHARE and expected_side == "A") or \
               (missing_side == ReportSide.A_SHARE and expected_side == "H"):
                return True, rationale

    topic_lower = topic_label.lower()
    for keyword, expected_side, rationale in EXPECTED_TOPICS:
        if keyword.lower() in topic_lower:
            if (missing_side == ReportSide.H_SHARE and expected_side == "A") or \
               (missing_side == ReportSide.A_SHARE and expected_side == "H"):
                return True, rationale
    return False, ""
