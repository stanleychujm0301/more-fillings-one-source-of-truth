"""分支机构披露一致性检查。

针对银行年报中"分支机构（不含子公司）具体情况"表的跨报告比对。
策略：从 A/H 报告的文本段中提取分支行名称-资产规模-机构数量，按名称对齐后比较。
"""
from __future__ import annotations

import re

from loguru import logger

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

_BRANCH_ROW_PATTERN = re.compile(r"([一-龥]{2,6}分行)\s+(\d{1,3})\s+([\d,]{5,12})")
_BRANCH_NAME_T2S_OVERRIDES = str.maketrans(
    {
        "廣": "广",
        "烏": "乌",
        "魯": "鲁",
        "齊": "齐",
        "瀋": "沈",
        "臺": "台",
        "長": "长",
        "蘇": "苏",
        "寧": "宁",
        "廈": "厦",
        "門": "门",
        "連": "连",
        "薩": "萨",
        "無": "无",
        "錫": "锡",
        "盧": "卢",
        "莊": "庄",
        "島": "岛",
        "龍": "龙",
        "濟": "济",
        "鄭": "郑",
        "蘭": "兰",
        "貴": "贵",
        "陽": "阳",
        "慶": "庆",
        "漢": "汉",
        "濱": "滨",
        "爾": "尔",
        "煙": "烟",
        "內": "内",
        "銀": "银",
        "灣": "湾",
    }
)


def extract_branch_table(doc: ReportDocument) -> dict[str, dict]:
    """从报告文本段与结构化表格中提取分支机构分布表数据。

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
        _add_branch_matches(branches, text, seg.page)

    for row_text, page in _branch_table_rows_from_cells(doc):
        if "分行" not in row_text:
            continue
        _add_branch_matches(branches, row_text, page)

    return branches


def branch_table_diagnostics(
    a_doc: ReportDocument | None,
    h_doc: ReportDocument | None,
    diffs: list[Diff] | None = None,
) -> dict[str, object]:
    """Return stable diagnostics for deployment parity checks."""
    source_doc_available = bool(a_doc and h_doc)
    a_branches = extract_branch_table(a_doc) if a_doc else {}
    h_branches = extract_branch_table(h_doc) if h_doc else {}
    matched_names = sorted(set(a_branches) & set(h_branches))
    branch_diff_count = (
        sum(1 for diff in diffs or [] if diff.rule_id == "branch_asset_scale_match")
        if diffs is not None
        else _count_branch_asset_diffs(a_branches, h_branches, matched_names)
    )
    return {
        "branch_source_doc_available": source_doc_available,
        "a_branch_count": len(a_branches),
        "h_branch_count": len(h_branches),
        "matched_branch_count": len(matched_names),
        "branch_diff_count": branch_diff_count,
        "branch_alignment_ratio": _branch_alignment_ratio(a_branches, h_branches, matched_names),
    }


def _add_branch_matches(branches: dict[str, dict], text: str, page: int) -> None:
    for match in _BRANCH_ROW_PATTERN.finditer(text):
        raw_name = match.group(1)
        name = _normalize_branch_name(raw_name)
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
                "raw_name": raw_name,
                "count": count,
                "asset": asset,
                "page": page,
                "snippet": match.group(0),
            }


def _branch_table_rows_from_cells(doc: ReportDocument) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for table in doc.tables:
        grouped: dict[int, list[tuple[int, str]]] = {}
        for cell in table.cells:
            text = to_simplified(str(cell.text or "").strip())
            if not text:
                continue
            grouped.setdefault(cell.row, []).append((cell.col, text))
        for _, cells in sorted(grouped.items()):
            parts = [text for _, text in sorted(cells, key=lambda item: item[0])]
            row_text = re.sub(r"\s+", " ", " ".join(parts)).strip()
            if row_text:
                rows.append((row_text, table.page))
    return rows


def compare_branch_tables(
    a_doc: ReportDocument, h_doc: ReportDocument
) -> list[Diff]:
    """比对 A/H 两份报告的分支机构表，返回差异列表。"""
    a_branches = extract_branch_table(a_doc)
    h_branches = extract_branch_table(h_doc)
    matched_names = sorted(set(a_branches) & set(h_branches))
    alignment_ratio = _branch_alignment_ratio(a_branches, h_branches, matched_names)

    if not _branch_alignment_confident(a_branches, h_branches, matched_names):
        logger.info(
            "分支机构披露检查：A={} H={} matched={} alignment={:.2f} diffs=0 skipped=alignment",
            len(a_branches),
            len(h_branches),
            len(matched_names),
            alignment_ratio,
        )
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

    logger.info(
        "分支机构披露检查：A={} H={} matched={} alignment={:.2f} diffs={}",
        len(a_branches),
        len(h_branches),
        len(matched_names),
        alignment_ratio,
        len(diffs),
    )
    return diffs


def _count_branch_asset_diffs(a_branches: dict[str, dict], h_branches: dict[str, dict], matched_names: list[str]) -> int:
    if not _branch_alignment_confident(a_branches, h_branches, matched_names):
        return 0
    count = 0
    for name in matched_names:
        a_data = a_branches[name]
        h_data = h_branches[name]
        if a_data.get("count") != h_data.get("count"):
            continue
        if not _branch_row_evidence_confident(a_data) or not _branch_row_evidence_confident(h_data):
            continue
        if a_data.get("asset") != h_data.get("asset"):
            count += 1
    return count


def _branch_alignment_confident(a_branches: dict[str, dict], h_branches: dict[str, dict], matched_names: list[str]) -> bool:
    if not a_branches or not h_branches or not matched_names:
        return False
    smaller_side = min(len(a_branches), len(h_branches))
    if smaller_side < 5:
        return True
    return len(matched_names) / smaller_side >= 0.6


def _branch_alignment_ratio(a_branches: dict[str, dict], h_branches: dict[str, dict], matched_names: list[str]) -> float:
    smaller_side = min(len(a_branches), len(h_branches))
    if not smaller_side:
        return 0.0
    return round(len(matched_names) / smaller_side, 4)


def _branch_row_evidence_confident(data: dict) -> bool:
    name = str(data.get("name") or "")
    count = str(data.get("count") or "")
    asset = data.get("asset")
    snippet = re.sub(r"\s+", " ", str(data.get("snippet") or ""))
    if not name or not count or asset is None or not snippet:
        return False
    if name not in _normalize_branch_name(snippet):
        return False
    if not re.search(rf"\b{re.escape(count)}\b", snippet):
        return False
    asset_text = f"{float(asset):,.0f}"
    asset_plain = asset_text.replace(",", "")
    snippet_digits = snippet.replace(",", "")
    return asset_text in snippet or asset_plain in snippet_digits


def _normalize_branch_name(text: str) -> str:
    simplified = to_simplified(str(text or ""))
    simplified = simplified.translate(_BRANCH_NAME_T2S_OVERRIDES)
    return re.sub(r"\s+", "", simplified)


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
