"""Profile 模块 — 年报画像提取与比对。

入口函数：
- build_profile: 从 ReportDocument 提取完整画像
- compare_profiles: 比对 A/H 两份画像，生成差异
"""

from __future__ import annotations

import asyncio

from ahcc.profile.compare import compare_profiles as _compare_profiles
from ahcc.profile.extract_metrics import extract_metrics
from ahcc.profile.extract_narratives import extract_narratives
from ahcc.profile.extract_structure import extract_structure
from ahcc.profile.models import ReportProfile
from ahcc.schemas import ReportDocument


async def build_profile(doc: ReportDocument) -> ReportProfile:
    """从 ReportDocument 提取完整画像。

    三层提取：数值 + 叙述 + 结构，可以并发执行。
    """
    # 数值和叙述提取是纯CPU操作，用线程池并行
    loop = asyncio.get_event_loop()
    metrics_task = loop.run_in_executor(None, extract_metrics, doc)
    narratives_task = loop.run_in_executor(None, extract_narratives, doc)
    structure_task = loop.run_in_executor(None, extract_structure, doc)

    metrics = await metrics_task
    narratives = await narratives_task
    structure = await structure_task

    profile = ReportProfile(
        doc_id=doc.doc_id,
        side=doc.side,
        total_pages=doc.total_pages,
        metrics=metrics,
        narratives=narratives,
        structure=structure,
        metadata=doc.metadata,
        source_doc=doc,
    )
    profile.profile_summary = summarize_profile(profile)
    return profile


async def compare_profiles(profile_a: ReportProfile, profile_h: ReportProfile) -> list:
    """比对 A/H 两份画像，返回 ProfileDiff 列表。"""
    # 纯CPU操作
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _compare_profiles, profile_a, profile_h)


def summarize_profile(profile: ReportProfile, max_items: int | None = None) -> dict:
    """Return a compact, JSON-safe profile snapshot for API/report output."""
    source_doc = getattr(profile, "source_doc", None)
    audit_obj = getattr(source_doc, "extraction_audit", None)
    if audit_obj is not None:
        extraction_audit = audit_obj.model_dump(mode="json")
    else:
        extraction_audit = (profile.metadata or {}).get("extraction_audit") or None

    metrics = []
    metric_items = profile.metrics if max_items is None else profile.metrics[:max_items]
    for occ in metric_items:
        primary = occ.primary
        metrics.append({
            "canonical_key": occ.canonical_key,
            "name": primary.name.model_dump(mode="json"),
            "value": primary.value,
            "value_text": primary.value_text,
            "unit": primary.unit,
            "currency": primary.currency.value if primary.currency else None,
            "page": primary.page,
            "confidence": primary.confidence,
            "source": primary.source,
            "occurrence_count": len(occ.all_occurrences),
            "is_internally_consistent": occ.is_internally_consistent,
            "evidence": primary.evidence.model_dump(mode="json"),
            "all_occurrences": [
                {
                    "value": item.value,
                    "value_text": item.value_text,
                    "unit": item.unit,
                    "currency": item.currency.value if item.currency else None,
                    "page": item.page,
                    "confidence": item.confidence,
                    "source": item.source,
                    "evidence": item.evidence.model_dump(mode="json"),
                }
                for item in occ.all_occurrences
            ],
        })

    narratives = []
    narrative_items = profile.narratives if max_items is None else profile.narratives[:max_items]
    for block in narrative_items:
        narratives.append({
            "topic_key": block.topic_key,
            "topic_label": block.topic_label,
            "page_range": block.page_range,
            "word_count": block.word_count,
            "detail_level": block.detail_level,
            "keywords": block.keywords[:10],
            "summary": block.summary[:300],
            "evidence": [ev.model_dump(mode="json") for ev in block.evidence[:3]],
            "segments": [
                {
                    "segment_id": seg.segment_id,
                    "page": seg.page,
                    "bbox": seg.bbox,
                    "section": seg.section,
                    "language": seg.language.value,
                    "text": seg.text,
                }
                for seg in block.segments
            ],
        })

    return {
        "doc_id": profile.doc_id,
        "side": profile.side.value,
        "total_pages": profile.total_pages,
        "metric_keys": len(profile.metrics),
        "metric_occurrences": sum(len(occ.all_occurrences) for occ in profile.metrics),
        "narrative_blocks": len(profile.narratives),
        "structure_nodes": _count_structure_nodes(profile.structure),
        "extraction_audit": extraction_audit,
        "warning_flags": (extraction_audit or {}).get("warning_flags", []) if isinstance(extraction_audit, dict) else [],
        "warnings": (extraction_audit or {}).get("warnings", []) if isinstance(extraction_audit, dict) else [],
        "metrics": metrics,
        "narratives": narratives,
        "structure": [node.model_dump(mode="json") for node in profile.structure],
    }


def _count_structure_nodes(nodes) -> int:
    return sum(1 + _count_structure_nodes(node.children) for node in nodes)
