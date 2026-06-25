"""分支机构披露一致性检查。

针对银行年报中"分支机构（不含子公司）具体情况"表的跨报告比对。
策略：从 A/H 报告的文本段中提取分支行名称-资产规模-机构数量，按名称对齐后比较。
"""
from __future__ import annotations

import re

from ahcc.align.glossary import to_simplified
from ahcc.schemas import (
    Diff,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportDocument,
    ReportSide,
)
from ahcc.check.explanation import make_value_explanation


def extract_branch_table(doc: ReportDocument) -> dict[str, dict]:
    """从报告文本段中提取分支机构分布表数据。

    匹配模式：分支机构名称 + 数量 + 资产规模（百万元）
    例：北京分行 75 810,136 北京市西城区宣武门内大街1号
    """
    branches: dict[str, dict] = {}

    for seg in doc.texts:
        text = to_simplified(seg.text)
        # 只搜索包含"分行"且包含大数字的文本段
        if "分行" not in text:
            continue

        # 匹配模式：XXX分行 <数量> <资产规模> <地址>
        # 数量：1-3位整数；资产规模：带逗号的千分位数字
        pattern = r"([一-龥]{2,6}分行)\s+(\d{1,3})\s+([\d,]{5,12})"
        for match in re.finditer(pattern, text):
            name = match.group(1)
            count_str = match.group(2)
            asset_str = match.group(3)

            try:
                count = int(count_str)
                asset = float(asset_str.replace(",", ""))
            except ValueError:
                continue

            # 跳过已存在（取第一次出现的）
            if name not in branches:
                branches[name] = {
                    "name": name,
                    "count": count,
                    "asset": asset,
                    "page": seg.page,
                    "snippet": match.group(0),
                }

    return branches


def compare_branch_tables(
    a_doc: ReportDocument, h_doc: ReportDocument
) -> list[Diff]:
    """比对 A/H 两份报告的分支机构表，返回差异列表。"""
    a_branches = extract_branch_table(a_doc)
    h_branches = extract_branch_table(h_doc)
    matched_names = sorted(set(a_branches) & set(h_branches))

    if not _branch_alignment_confident(a_branches, h_branches, matched_names):
        return []

    diffs: list[Diff] = []

    # 双边比对
    for name in matched_names:
        a_data = a_branches[name]
        h_data = h_branches.get(name)
        if not h_data:
            continue  # H-share 缺失该分支，暂不处理
        if a_data.get("count") != h_data.get("count"):
            continue
        if not _branch_row_evidence_confident(a_data) or not _branch_row_evidence_confident(h_data):
            continue

        a_asset = a_data["asset"]
        h_asset = h_data["asset"]

        if a_asset != h_asset:
            # 计算差异百分比
            if a_asset != 0:
                pct_diff = (h_asset - a_asset) / a_asset * 100
            else:
                pct_diff = 0.0

            severity = _severity_for_branch_diff(abs(pct_diff))
            delta = h_asset - a_asset
            a_evidence = Evidence(
                side=ReportSide.A_SHARE,
                page=a_data["page"],
                snippet=a_data["snippet"],
                section="分支机构",
            )
            h_evidence = Evidence(
                side=ReportSide.H_SHARE,
                page=h_data["page"],
                snippet=h_data["snippet"],
                section="分支机构",
            )
            evidence = [a_evidence, h_evidence]

            diffs.append(
                Diff(
                    diff_id=f"BRANCH_{name}",
                    diff_type=DiffType.DISCLOSURE,
                    severity=severity,
                    canonical_key=None,
                    topic=LocalizedString(
                        zh=f"分支机构资产规模：{name}",
                        en=f"Branch asset scale: {name}",
                    ),
                    summary=LocalizedString(
                        zh=f"A股报告该分支资产规模为 {a_asset:,.0f} 百万元，"
                           f"H股报告为 {h_asset:,.0f} 百万元，差异 {pct_diff:+.1f}%",
                        en=f"A-share: {a_asset:,.0f} vs H-share: {h_asset:,.0f} ({pct_diff:+.1f}%)",
                    ),
                    a_value=a_asset,
                    h_value=h_asset,
                    delta=delta,
                    tolerance=None,
                    evidence=evidence,
                    diff_explanation=make_value_explanation(
                        headline=f"{name}分支机构资产规模不一致",
                        label="资产规模（百万元）",
                        role="branch_asset_scale",
                        a_value=a_asset,
                        h_value=h_asset,
                        delta=delta,
                        evidence=evidence,
                        review_hint="该分支机构名称和机构数量已匹配，优先核对同一行资产规模披露。",
                    ),
                    standard_reasoning=None,
                    chart_cross=None,
                    rule_id="branch_asset_scale_match",
                )
            )

    return diffs


def _branch_alignment_confident(a_branches: dict[str, dict], h_branches: dict[str, dict], matched_names: list[str]) -> bool:
    if not a_branches or not h_branches or not matched_names:
        return False
    smaller_side = min(len(a_branches), len(h_branches))
    if smaller_side < 5:
        return True
    return len(matched_names) / smaller_side >= 0.6


def _branch_row_evidence_confident(data: dict) -> bool:
    name = str(data.get("name") or "")
    count = str(data.get("count") or "")
    asset = data.get("asset")
    snippet = re.sub(r"\s+", " ", str(data.get("snippet") or ""))
    if not name or not count or asset is None or not snippet:
        return False
    if name not in snippet:
        return False
    if not re.search(rf"\b{re.escape(count)}\b", snippet):
        return False
    asset_text = f"{float(asset):,.0f}"
    asset_plain = asset_text.replace(",", "")
    snippet_digits = snippet.replace(",", "")
    return asset_text in snippet or asset_plain in snippet_digits


def _severity_for_branch_diff(pct_abs: float) -> DiffSeverity:
    """根据差异百分比判定严重度。"""
    if pct_abs > 50:
        return DiffSeverity.HIGH
    elif pct_abs > 20:
        return DiffSeverity.MEDIUM
    elif pct_abs > 5:
        return DiffSeverity.LOW
    else:
        return DiffSeverity.INFO
