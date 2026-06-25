"""Narrative profile extraction with stable topic taxonomy.

This module keeps the extraction deterministic:
- every text segment is assigned to a stable topic or ``uncategorized``;
- blocks are aggregated by topic and continuous page ranges;
- each block keeps source segment ids and page evidence.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from ahcc.align.glossary import to_simplified
from ahcc.profile.models import NarrativeBlock
from ahcc.profile.topic_map import get_topic_for_text, get_topic_name, get_topics_for_section
from ahcc.schemas import Evidence, ReportDocument, ReportSide, TextSegment


_CHINESE_STOPWORDS = {
    "公司", "集团", "本公司", "本集团", "报告", "年度", "以及", "进行", "相关", "主要",
    "情况", "如下", "说明", "其中", "包括", "由于", "根据", "截至", "年末", "人民币",
}

_ENGLISH_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "company", "group", "annual",
    "report", "during", "year", "ended", "limited", "including",
}


def extract_narratives(doc: ReportDocument) -> list[NarrativeBlock]:
    """Extract narrative blocks from all parsed text segments."""
    valid_segments = [
        seg for seg in doc.texts
        if seg.text and len(seg.text.strip()) >= 10
    ]
    if not valid_segments:
        return []

    grouped: dict[tuple[str, str], list[TextSegment]] = defaultdict(list)
    for seg in valid_segments:
        topic_key = _choose_primary_topic(seg)
        grouped[(topic_key, seg.section or "")].append(seg)

    blocks: list[NarrativeBlock] = []
    for (topic_key, section_code), segments in grouped.items():
        for chunk in _split_continuous_chunks(segments):
            block = _make_block(chunk, doc.side, topic_key, section_code)
            if block:
                blocks.append(block)

    blocks.sort(key=lambda b: (b.page_range[0], b.topic_key, b.page_range[1]))
    return blocks


def _choose_primary_topic(seg: TextSegment) -> str:
    section_topics = get_topics_for_section(seg.section or "")
    text_topics = get_topic_for_text(seg.text, max_topics=3)
    for topic in text_topics:
        if topic != "uncategorized":
            return topic
    for topic in section_topics:
        if topic != "uncategorized":
            return topic
    return "uncategorized"


def _split_continuous_chunks(segments: list[TextSegment], max_page_gap: int = 3) -> list[list[TextSegment]]:
    ordered = sorted(segments, key=lambda seg: (seg.page, seg.segment_id))
    chunks: list[list[TextSegment]] = []
    current: list[TextSegment] = []
    last_page: int | None = None

    for seg in ordered:
        if last_page is not None and seg.page - last_page > max_page_gap:
            if current:
                chunks.append(current)
            current = []
        current.append(seg)
        last_page = seg.page

    if current:
        chunks.append(current)
    return chunks


def _make_block(
    segments: list[TextSegment],
    side: ReportSide,
    topic_key: str,
    section_code: str,
) -> NarrativeBlock | None:
    if not segments:
        return None

    full_text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
    word_count = len(re.sub(r"\s+", "", full_text))
    if word_count < 20:
        return None

    pages = sorted({seg.page for seg in segments})
    page_range = (pages[0], pages[-1])
    keywords = _extract_keywords(full_text, top_k=20)
    language = "en" if side == ReportSide.H_SHARE else "zh"
    topic_label = get_topic_name(topic_key, language)

    evidence = _make_evidence(segments, side)
    return NarrativeBlock(
        topic_label=topic_label,
        topic_key=topic_key,
        keywords=keywords,
        segments=segments,
        page_range=page_range,
        word_count=word_count,
        summary=full_text[:300],
        evidence=evidence,
        detail_level=_detail_level(word_count),
        source_segments=[seg.segment_id for seg in segments],
        key_subtopics=_extract_key_subtopics(full_text, topic_key),
    )


def _make_evidence(segments: list[TextSegment], side: ReportSide) -> list[Evidence]:
    anchors: list[TextSegment] = []
    if segments:
        anchors.append(segments[0])
    if len(segments) > 1 and segments[-1].page != segments[0].page:
        anchors.append(segments[-1])

    evidence: list[Evidence] = []
    for seg in anchors[:3]:
        evidence.append(
            Evidence(
                side=side,
                page=seg.page,
                bbox=seg.bbox,
                snippet=seg.text.strip()[:300],
                section=seg.section,
            )
        )
    return evidence


def _extract_keywords(text: str, top_k: int = 20) -> list[str]:
    simplified = to_simplified(text)
    candidates: list[str] = []

    for length in range(6, 1, -1):
        for match in re.finditer(rf"[一-龥]{{{length}}}", simplified):
            word = match.group()
            if word not in _CHINESE_STOPWORDS:
                candidates.append(word)

    for match in re.finditer(r"[a-zA-Z]{3,}", text.lower()):
        word = match.group()
        if word not in _ENGLISH_STOPWORDS:
            candidates.append(word)

    counter = Counter(candidates)
    return [word for word, _ in counter.most_common(top_k)]


def _extract_key_subtopics(text: str, topic_key: str) -> list[str]:
    subtopic_map: dict[str, tuple[str, ...]] = {
        "mda_business_review": ("经纪业务", "投行业务", "资管业务", "自营业务", "信用业务", "brokerage", "investment banking"),
        "mda_risk_management": ("信用风险", "市场风险", "流动性风险", "操作风险", "credit risk", "market risk", "liquidity risk"),
        "esg_environment": ("碳排放", "温室气体", "环保", "节能", "emissions", "carbon"),
        "esg_social": ("员工", "培训", "社区", "公益", "employees", "community"),
        "financial_instruments": ("公允价值", "衍生工具", "金融风险", "fair value", "derivative"),
        "related_party": ("关联交易", "关联方", "related party"),
    }
    lowered = to_simplified(text).lower()
    matched = [sub for sub in subtopic_map.get(topic_key, ()) if sub.lower() in lowered]
    return list(dict.fromkeys(matched))[:5]


def _detail_level(word_count: int) -> str:
    if word_count < 200:
        return "brief"
    if word_count < 1000:
        return "medium"
    return "detailed"
