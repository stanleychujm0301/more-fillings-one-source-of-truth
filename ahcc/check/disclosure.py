"""披露差异检查（P4 实现）— 基于披露映射规则识别 A/H 报告的位置和详略差异。

检查维度：
1. 双边披露后的事实一致性（如分支机构资产规模）
2. 单边披露、位置缺失和详略覆盖不再作为 Diff，由 coverage_items 展示
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from ahcc.schemas import (
    Diff,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportDocument,
    ReportSide,
)


# ============================================================
# 1. 规则加载
# ============================================================

def _load_framework_map() -> dict[str, str]:
    """加载框架层章节映射（a_section -> h_section）。"""
    path = Path(__file__).resolve().parents[2] / "kb" / "disclosure_map" / "framework" / "framework_map.yaml"
    if not path.exists():
        logger.warning(f"framework_map.yaml 不存在: {path}")
        return {}

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mapping: dict[str, str] = {}
    for m in data.get("mappings", []):
        a_name = m.get("a_section_name", "")
        h_name = m.get("h_section_name", "")
        if a_name and h_name:
            mapping[a_name] = h_name
    return mapping


def _load_depth_rules() -> list[dict[str, Any]]:
    """加载披露详略差异规则。"""
    path = Path(__file__).resolve().parents[2] / "kb" / "disclosure_map" / "depth_rules" / "all_rules.yaml"
    if not path.exists():
        logger.warning(f"all_rules.yaml 不存在: {path}")
        return []

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("rules", [])


# ============================================================
# 2. 主入口
# ============================================================

async def run_disclosure_checks(doc_a: ReportDocument, doc_h: ReportDocument) -> list[Diff]:
    """对两份报告做披露差异检查。"""
    diffs: list[Diff] = []

    # 单边披露/位置缺失/详略覆盖只进入 coverage_items，不计入差异。
    # 分支机构资产规模属于双边事实核查，仍作为披露事实差异输出。
    from ahcc.check.branch_disclosure import compare_branch_tables
    diffs.extend(compare_branch_tables(doc_a, doc_h))

    logger.info(f"披露差异检查完成，发现 {len(diffs)} 条差异")
    return diffs


# ============================================================
# 3. 披露位置差异检查
# ============================================================

def _check_location_differences(doc_a: ReportDocument, doc_h: ReportDocument) -> list[Diff]:
    """检查 A/H 报告的章节位置差异。

    策略：
    - 统计每份报告中各 section 的出现页码
    - 基于 framework_map 检查映射关系是否成立
    - 若 A 股某 section 在 H 股未找到对应 section，标记差异
    """
    diffs: list[Diff] = []
    framework_map = _load_framework_map()

    # 统计 A/H 的 section -> 页码集合
    a_sections = _collect_sections(doc_a)
    h_sections = _collect_sections(doc_h)

    # 检查 A 股独有 section（映射表中标为 H 股无对应）
    a_only = {"优先股相关情况", "债券相关情况"}
    for a_sec_name in a_sections:
        if a_sec_name in a_only:
            continue
        expected_h = framework_map.get(a_sec_name)
        if expected_h and expected_h not in h_sections:
            diffs.append(
                Diff(
                    diff_id=f"discl-loc-{uuid.uuid4().hex[:6]}",
                    diff_type=DiffType.DISCLOSURE,
                    severity=DiffSeverity.LOW,
                    triage="real",
                    topic=LocalizedString(zh=f"披露位置：{a_sec_name}", en=f"Disclosure location: {a_sec_name}"),
                    summary=LocalizedString(
                        zh=f"A 股章节「{a_sec_name}」在 H 股未找到对应章节「{expected_h}」",
                        en=f"A-section '{a_sec_name}' not found in H-section '{expected_h}'",
                    ),
                    evidence=[
                        Evidence(
                            side=ReportSide.A_SHARE,
                            page=min(a_sections[a_sec_name]),
                            snippet=f"A 股「{a_sec_name}」出现页码: {sorted(a_sections[a_sec_name])}",
                        ),
                    ],
                )
            )

    return diffs


def _collect_sections(doc: ReportDocument) -> dict[str, set[int]]:
    """收集报告中各 section 出现的页码。"""
    sections: dict[str, set[int]] = {}
    for seg in doc.texts:
        if seg.section:
            sections.setdefault(seg.section, set()).add(seg.page)
    # 同时从表格标题中收集
    for table in doc.tables:
        title = table.title.zh or table.title.en or ""
        if title:
            # 尝试映射到已知 section
            for sec_code in ("bs", "pl", "cf", "equity", "notes", "mda", "esg", "related_party"):
                if sec_code in title.lower():
                    sections.setdefault(sec_code, set()).add(table.page)
    return sections


# ============================================================
# 4. 披露详略差异检查
# ============================================================

def _check_depth_differences(doc_a: ReportDocument, doc_h: ReportDocument) -> list[Diff]:
    """基于 depth_rules 检查 A/H 披露详略差异。

    策略：
    - 对每条 depth_rule，提取 check_logic 中的关键词
    - 检查 A 股和 H 股文本中是否包含这些关键词
    - 若 H 股有而 A 股无，标记为预期差异（HKFRS 通常要求更严）
    - 若 A 股有而 H 股无，标记为不符合预期
    """
    diffs: list[Diff] = []
    rules = _load_depth_rules()

    a_text = _concat_text(doc_a)
    h_text = _concat_text(doc_h)

    for rule in rules:
        topic_zh = rule.get("topic", {}).get("zh", "")
        topic_en = rule.get("topic", {}).get("en", "")
        check_logic = rule.get("check_logic", "")
        severity_str = rule.get("severity", "low")
        cas_req = rule.get("cas_requirement", "")
        ifrs_req = rule.get("ifrs_requirement", "")

        # 从 check_logic 中提取关键词（简单分词）
        keywords = _extract_keywords_from_check_logic(check_logic)
        if not keywords:
            continue

        a_has = any(kw in a_text for kw in keywords)
        h_has = any(kw in h_text for kw in keywords)

        if a_has and h_has:
            continue  # 双方都包含，无差异

        if not a_has and not h_has:
            continue  # 双方都不包含，无法判断

        # H 股有、A 股无 → 预期差异（HKFRS 要求更严）
        if h_has and not a_has:
            severity = _parse_severity(severity_str)
            diffs.append(
                Diff(
                    diff_id=f"discl-depth-{rule.get('rule_id', 'DR-00')}",
                    diff_type=DiffType.DISCLOSURE,
                    severity=severity,
                    triage="expected",
                    topic=LocalizedString(zh=topic_zh, en=topic_en),
                    summary=LocalizedString(
                        zh=f"{topic_zh}：H 股披露了 CAS 未要求的维度（{', '.join(keywords)}）",
                        en=f"{topic_en}: H-share discloses items not required by CAS",
                    ),
                    evidence=[
                        Evidence(
                            side=ReportSide.H_SHARE,
                            page=1,
                            snippet=f"CAS要求: {cas_req[:100]}... | IFRS要求: {ifrs_req[:100]}...",
                        ),
                    ],
                )
            )

        # A 股有、H 股无 → 不符合预期（较少见）
        if a_has and not h_has:
            diffs.append(
                Diff(
                    diff_id=f"discl-depth-{rule.get('rule_id', 'DR-00')}",
                    diff_type=DiffType.DISCLOSURE,
                    severity=DiffSeverity.MEDIUM,
                    triage="real",
                    topic=LocalizedString(zh=topic_zh, en=topic_en),
                    summary=LocalizedString(
                        zh=f"{topic_zh}：A 股披露但 H 股未披露（不符合预期）",
                        en=f"{topic_en}: A-share discloses but H-share does not",
                    ),
                    evidence=[
                        Evidence(
                            side=ReportSide.A_SHARE,
                            page=1,
                            snippet=f"CAS要求: {cas_req[:100]}...",
                        ),
                    ],
                )
            )

    return diffs


def _extract_keywords_from_check_logic(check_logic: str) -> list[str]:
    """从 check_logic 文本中提取关键词。

    简单策略：提取引号内的文本和关键短语。
    例："检查H股是否披露了'假设变更原因'或'历史对比'" -> ['假设变更原因', '历史对比']
    """
    import re

    keywords: list[str] = []

    # 提取引号/书名号内的文本
    quoted = re.findall(r"['\"](.+?)['\"]", check_logic)
    keywords.extend(quoted)

    # 提取「」内的文本
    bracketed = re.findall(r"「(.+?)」", check_logic)
    keywords.extend(bracketed)

    # 如果没有任何引号内容，提取关键动词短语
    if not keywords:
        # 提取"披露了"后面的名词短语
        patterns = re.findall(r"披露了?(.+?)(?:或|，|,|；|;|$)", check_logic)
        for p in patterns:
            keywords.append(p.strip())

    # 去重并过滤太短的关键词
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        kw = kw.strip()
        if len(kw) >= 2 and kw not in seen:
            result.append(kw)
            seen.add(kw)
    return result


def _concat_text(doc: ReportDocument) -> str:
    """将报告文本拼接为一个字符串（用于关键词搜索）。"""
    parts: list[str] = []
    for seg in doc.texts:
        parts.append(seg.text)
    # 也包含表格 cell 的文本
    for table in doc.tables:
        for cell in table.cells:
            parts.append(cell.text)
    return "\n".join(parts)


def _parse_severity(sev: str) -> DiffSeverity:
    """将规则中的 severity 字符串转为 DiffSeverity。"""
    mapping = {
        "low": DiffSeverity.LOW,
        "medium": DiffSeverity.MEDIUM,
        "high": DiffSeverity.HIGH,
        "critical": DiffSeverity.CRITICAL,
    }
    return mapping.get(sev.lower(), DiffSeverity.LOW)


def _is_junk_topic_label(label: str) -> bool:
    """检测关键词重复拼接的劣质 topic_label。

    正常标签如"商誉减值测试"（短、连贯），劣质标签如"可能面对的风可能面对论与分析重大风险提示"
    （长、无标点、关键词重复拼接）。
    """
    if not label or len(label) < 4:
        return True
    # 包含明显的垃圾关键词模式
    junk_patterns = ("不适用", "不適用", "号填列", "號填列")
    if any(p in label for p in junk_patterns):
        return True
    has_punct = any(c in label for c in "，。、；：！？,.;:!?·—–（）()[]【】")
    # 无标点的长标签几乎一定是关键词拼接
    if len(label) > 10 and not has_punct:
        return True
    # 2-gram 重复 → 关键词重叠拼接
    if len(label) > 6:
        seen: set[str] = set()
        for i in range(len(label) - 1):
            gram = label[i:i + 2]
            if gram in seen:
                return True
            seen.add(gram)
    return False


# ============================================================
# Profile 适配器
# ============================================================

async def run_disclosure_checks_on_profiles(profile_a, profile_h) -> list[Diff]:
    """基于画像的披露差异检查 — 包含全量旧检查 + 画像比对。"""
    from ahcc.profile.compare import compare_profiles, _is_garbled_text, _is_garbled_key

    diffs: list[Diff] = []

    # ---- A: 画像比对差异 ----
    profile_diffs = compare_profiles(profile_a, profile_h)

    for pd in profile_diffs:
        # metric_mismatch 由 numeric.py 处理；metric_missing 进入 coverage_items。
        if pd.diff_type in ("metric_mismatch", "metric_missing"):
            continue

        # internal_inconsistency：转为 INTERNAL 类型差异
        if pd.diff_type == "internal_inconsistency":
            diff = _profile_diff_to_diff(pd)
            diff.diff_type = DiffType.INTERNAL
            diffs.append(diff)
            continue

        # 单边披露和详略覆盖不再作为差异输出，由 coverage_items 展示。
        if pd.diff_type in ("topic_missing", "narrative_depth", "structure_missing"):
            continue

        topic_label = pd.topic_label or pd.canonical_key or ""
        if _is_garbled_text(pd.summary.zh) or (topic_label and _is_garbled_key(topic_label)):
            continue
        if topic_label and _is_junk_topic_label(topic_label):
            continue
        diffs.append(_profile_diff_to_diff(pd))

    # ---- B: 需要原始 ReportDocument 的检查 ----
    doc_a = getattr(profile_a, 'source_doc', None)
    doc_h = getattr(profile_h, 'source_doc', None)

    if doc_a and doc_h:
        # 单边位置/详略覆盖由 coverage_items 处理；这里仅保留双边事实核查。
        from ahcc.check.branch_disclosure import compare_branch_tables
        diffs.extend(compare_branch_tables(doc_a, doc_h))
    else:
        logger.warning("source_doc 不可用，跳过位置/详略/分支机构检查")

    logger.info(f"画像披露差异: {len(diffs)} 条")
    return diffs


def _profile_diff_to_diff(pd) -> Diff:
    """ProfileDiff → Diff 转换。"""
    diff_id = f"pdl-{uuid.uuid4().hex[:8]}"
    return Diff(
        diff_id=diff_id,
        diff_type=DiffType.DISCLOSURE,
        severity=pd.severity,
        triage=pd.triage,
        canonical_key=pd.canonical_key,
        topic=pd.topic,
        summary=pd.summary,
        a_value=pd.a_value,
        h_value=pd.h_value,
        evidence=pd.evidence,
    )
