"""准则差异智能解读（P4 实现）— 模块 B / 亮点 1 / 项目护城河。

流程：
1. 收到上游 numeric_diffs（已识别为"数值不等"的 Diff 列表）
2. 对每条差异：
   a. 用 canonical_key + topic 查询 ChromaDB RAG
   b. 检索 top-k 相关准则条款
   c. 调用 LLM (standard_reasoning prompt) 推理"是否符合预期"
   d. 把结果填到 Diff.standard_reasoning 字段
3. 若 expected=True 且 confidence ≥ 0.85，则把 severity 降级
4. 输出新增的 DISCLOSURE 类差异（披露范围/格式差异，由 P6 提供的披露规则触发）
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Iterable

from loguru import logger

from ahcc.config import settings
from ahcc.llm.client import cached_call, load_prompt
from ahcc.rag.retriever import retrieve_clauses
from ahcc.schemas import (
    AlignedPair,
    Diff,
    DiffSeverity,
    DiffType,
    LocalizedString,
    StandardCitation,
    StandardReasoning,
)
from ahcc.check.explanation import make_value_explanation


async def run_standard_checks(pairs: Iterable[AlignedPair]) -> list[Diff]:
    """对每对数据点做准则差异 RAG 推理。"""
    pairs_list = [
        p
        for p in pairs
        if p.a_point
        and p.h_point
        and p.a_point.value is not None
        and p.h_point.value is not None
        # 用 isclose 比浮点，避免微小舍入差触发无谓的 LLM 推理
        and not math.isclose(p.a_point.value, p.h_point.value, rel_tol=1e-6, abs_tol=1e-9)
        and _pair_reporting_scope_compatible(p)
    ]
    if not pairs_list:
        return []

    logger.info(f"对 {len(pairs_list)} 对差异做准则推理")

    # 限流：避免对 provider 发起 N 个并发请求触发 429 / 烧配额
    sem = asyncio.Semaphore(max(1, settings.llm_concurrency))

    async def _guarded(p: AlignedPair) -> Diff | None:
        async with sem:
            return await _reason_one(p)

    results = await asyncio.gather(*[_guarded(p) for p in pairs_list], return_exceptions=True)

    diffs: list[Diff] = []
    failures = 0
    for pair, result in zip(pairs_list, results, strict=True):
        if isinstance(result, Exception):
            failures += 1
            logger.warning(f"准则推理失败 {pair.canonical_key}: {result}")
            continue
        if result:
            diffs.append(result)
    if failures:
        # 整体失败率过高通常意味着 RAG 为空或 LLM 全挂，避免静默失效
        logger.warning(f"准则推理：{failures}/{len(pairs_list)} 对失败")
    return diffs


async def _reason_one(pair: AlignedPair) -> Diff | None:
    """对单对差异做准则推理。"""
    clauses = retrieve_clauses(query=f"{pair.topic_zh} {pair.topic_en}", top_k=4)
    if not clauses:
        return None

    prompt = load_prompt("standard_reasoning.txt").format(
        topic_zh=pair.topic_zh,
        topic_en=pair.topic_en,
        a_value=pair.a_point.value if pair.a_point else "",
        a_unit=pair.a_point.unit if pair.a_point else "",
        h_value=pair.h_point.value if pair.h_point else "",
        h_unit=pair.h_point.unit if pair.h_point else "",
        a_period=pair.a_point.period if pair.a_point else "",
        h_period=pair.h_point.period if pair.h_point else "",
        a_page=pair.a_point.evidence.page if pair.a_point else "",
        h_page=pair.h_point.evidence.page if pair.h_point else "",
        delta=_delta(pair),
        delta_pct=_delta_pct(pair),
        retrieved_clauses="\n\n".join(c["text"] for c in clauses),
    )

    result = await asyncio.to_thread(
        cached_call,
        "reason",
        [{"role": "user", "content": prompt}],
        json_mode=True,
        temperature=0.1,
    )

    # LLM 未配置/失败时返回 {} 或 ""；空结果不应伪造一条「非预期」准则差异
    if not isinstance(result, dict) or not result:
        return None

    # 逐条 citation 容错：单条字段不合法不应让整条推理崩溃
    citations: list[StandardCitation] = []
    for c in result.get("citations", []) or []:
        try:
            citations.append(StandardCitation(**c))
        except Exception:
            continue

    # confidence 可能是 "high"/null 等非数值，需兜底
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    reasoning = StandardReasoning(
        expected=bool(result.get("expected")),
        rationale=str(result.get("rationale", "") or ""),
        citations=citations,
        confidence=confidence,
        llm_model="reason-default",
    )

    severity = DiffSeverity.LOW if reasoning.expected and reasoning.confidence >= 0.85 else DiffSeverity.MEDIUM
    triage = "expected" if reasoning.expected and reasoning.confidence >= 0.85 else "real"
    evidence = [pair.a_point.evidence, pair.h_point.evidence] if pair.a_point and pair.h_point else []
    a_value = pair.a_point.value if pair.a_point else None
    h_value = pair.h_point.value if pair.h_point else None

    # 用全键 hash 而非前缀截断，避免 canonical_key 前 8 字符相同导致 diff_id 撞号
    key_hash = hashlib.sha1(pair.canonical_key.encode("utf-8")).hexdigest()[:8]
    return Diff(
        diff_id=f"std-{key_hash}",
        diff_type=DiffType.STANDARD,
        severity=severity,
        triage=triage,
        canonical_key=pair.canonical_key,
        topic=LocalizedString(zh=pair.topic_zh, en=pair.topic_en),
        summary=LocalizedString(
            zh=f"{pair.topic_zh}：{reasoning.rationale[:80]}",
            en=f"{pair.topic_en}: {reasoning.rationale[:80]}",
        ),
        a_value=a_value,
        h_value=h_value,
        standard_reasoning=reasoning,
        evidence=evidence,
        diff_explanation=make_value_explanation(
            headline=f"{pair.topic_zh}准则口径判断",
            label=pair.topic_zh,
            role=pair.canonical_key,
            a_value=a_value,
            h_value=h_value,
            delta=_delta(pair),
            evidence=evidence,
            review_hint=reasoning.rationale,
        ),
    )


def _delta(pair: AlignedPair) -> float:
    if pair.a_point and pair.h_point and pair.a_point.value is not None and pair.h_point.value is not None:
        return pair.a_point.value - pair.h_point.value
    return 0.0


def _delta_pct(pair: AlignedPair) -> float:
    if pair.a_point and pair.h_point and pair.a_point.value and pair.h_point.value:
        base = max(abs(pair.a_point.value), abs(pair.h_point.value), 1e-9)
        return round(abs(pair.a_point.value - pair.h_point.value) / base * 100, 2)
    return 0.0


def _point_scope_text(pair: AlignedPair, side: str) -> str:
    point = pair.a_point if side == "a" else pair.h_point
    if not point:
        return ""
    evidence = point.evidence
    parts = [
        point.name.zh,
        point.name.en,
        point.period,
        point.value_text,
        evidence.section if evidence else None,
        evidence.snippet if evidence else None,
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _is_quarterly_scope_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "").lower()
    markers = (
        "本年度分季度",
        "分季度经营指标",
        "第一季度",
        "一季度",
        "第1季度",
        "1季度",
        "第二季度",
        "二季度",
        "第2季度",
        "2季度",
        "第三季度",
        "三季度",
        "第3季度",
        "3季度",
        "第四季度",
        "四季度",
        "第4季度",
        "4季度",
        "quarterly",
        "firstquarter",
        "secondquarter",
        "thirdquarter",
        "fourthquarter",
    )
    return any(marker in compact for marker in markers) or bool(re.search(r"\bq[1-4]\b|[1-4]q\b", text or ""))


def _scope_bucket(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "").lower()
    if _is_quarterly_scope_text(text):
        return "quarterly"
    if any(marker in compact for marker in ("分部", "业务分部", "经营分部", "segment")):
        return "segment"
    if any(marker in compact for marker in ("母公司", "本公司财务报表", "parentcompany", "companystatement")):
        return "parent_company"
    if any(marker in compact for marker in ("公允价值", "fairvalue", "风险敞口", "riskexposure")):
        return "detail"
    return "annual_or_unspecified"


def _pair_reporting_scope_compatible(pair: AlignedPair) -> bool:
    a_bucket = _scope_bucket(_point_scope_text(pair, "a"))
    h_bucket = _scope_bucket(_point_scope_text(pair, "h"))
    if a_bucket != h_bucket:
        return False
    if pair.a_point and pair.h_point and pair.a_point.period and pair.h_point.period:
        return str(pair.a_point.period) == str(pair.h_point.period)
    return True


# ============================================================
# Profile 适配器
# ============================================================

async def run_standard_checks_on_profiles(profile_a, profile_h) -> list[Diff]:
    """基于画像的准则差异检测。

    只对 glossary 中有定义的财务指标、且两边都有值的数据对做准则推理。
    """
    from ahcc.align.glossary import glossary
    from ahcc.profile.models import MetricItem
    from ahcc.schemas import DataPoint

    glossary_keys = glossary.all_canonical_keys()

    def _to_datapoint(m: MetricItem, side) -> DataPoint:
        return DataPoint(
            name=m.name,
            canonical_key=m.canonical_key,
            value=m.value,
            value_text=m.value_text,
            unit=m.unit,
            currency=m.currency,
            period=m.period,
            evidence=m.evidence,
            confidence=m.confidence,
        )

    # 只保留 glossary 中有定义的、confidence >= 0.5 的指标
    from ahcc.profile.models import MetricOccurrences

    def _extract_primary(metrics):
        items = []
        for occ in metrics:
            if isinstance(occ, MetricOccurrences):
                items.append(occ.primary)
            else:
                items.append(occ)
        return items

    a_filtered = {m.canonical_key: m for m in _extract_primary(profile_a.metrics) if m.confidence >= 0.5 and m.canonical_key in glossary_keys}
    h_filtered = {m.canonical_key: m for m in _extract_primary(profile_h.metrics) if m.confidence >= 0.5 and m.canonical_key in glossary_keys}

    # 只对两边都有的 key 做准则推理
    common_keys = set(a_filtered.keys()) & set(h_filtered.keys())

    pairs: list[AlignedPair] = []
    for key in common_keys:
        a_item = a_filtered[key]
        h_item = h_filtered[key]
        pairs.append(
            AlignedPair(
                canonical_key=key,
                topic_zh=a_item.name.zh or key,
                topic_en=a_item.name.en or h_item.name.en or key,
                a_point=_to_datapoint(a_item, profile_a.side),
                h_point=_to_datapoint(h_item, profile_h.side),
                alignment_confidence=1.0,
            )
        )

    return await run_standard_checks(pairs)
