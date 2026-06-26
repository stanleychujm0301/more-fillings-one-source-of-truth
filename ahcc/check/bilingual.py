"""H-share Chinese/English report consistency checks."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any, Callable

from ahcc.align.glossary import to_simplified, glossary
from ahcc.config import settings
from ahcc.llm.client import cached_call, load_prompt
from loguru import logger
from ahcc.schemas import (
    Diff,
    DiffExplanation,
    DiffExplanationItem,
    DiffSeverity,
    DiffType,
    Evidence,
    LocalizedString,
    ReportDocument,
    ReportSide,
)

SemanticEvaluator = Callable[[list[dict[str, Any]]], list[dict[str, Any]] | None]
_MAX_PAIR_CANDIDATES = 20  # 从 40 降至 20：减少 75% 评分调用，对配对质量影响极小
_DATE_MENTION_PATTERNS = (
    re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(
        r"\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+20\d{2}",
        re.I,
    ),
    re.compile(
        r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+20\d{2}",
        re.I,
    ),
)


def _page_window(zh_page: int, zh_total: int, en_total: int) -> int:
    """根据两份报告页数比动态计算搜索窗口。英文报告通常比中文长 15-30%，页码偏移随位置增大。"""
    if zh_total <= 0:
        return 12
    
    scale = en_total / zh_total
    base = max(8, int(5 * scale))
    # 越往后偏移越大：前 50 页用 base，之后线性增长
    if zh_page > 50:
        base += (zh_page - 50) // 15
    return min(base, 30)


@dataclass
class BilingualCheckResult:
    diffs: list[Diff] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    coverage_items: list[Any] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Block:
    index: int
    page: int
    text: str
    section: str | None = None
    raw_text: str | None = None  # 保留排版原文，供 LLM 比对；为空时回退到 text


@dataclass(frozen=True)
class _Fact:
    kind: str
    role: str
    value: Any
    raw: str
    page: int
    text: str
    section: str | None = None
    currency: str | None = None  # 金额币种（CNY/HKD/USD），仅 amount 类事实携带
    unit: str | None = None  # 行内或报告级单位（千元/thousand 等），用于量级比差异降级


@dataclass(frozen=True)
class _SectionInfo:
    key: str
    page: int
    order: int
    text: str


@dataclass(frozen=True)
class _TableRow:
    table_id: str
    page: int
    title: str
    row: int
    text: str
    section: str | None = None
    unit: str | None = None  # 从 FinancialTable.unit 继承的单位声明


@dataclass(frozen=True)
class _DisclosureUnit:
    unit_id: str
    kind: str
    page: int
    text: str
    raw_text: str | None = None  # 保留排版原文，供 LLM 对比；为空时回退到 text
    section: str | None = None
    facts: tuple[_Fact, ...] = ()
    table_rows: tuple[_TableRow, ...] = ()
    confidence: float = 1.0


@dataclass(frozen=True)
class _UnitAlignment:
    zh: _DisclosureUnit
    en: _DisclosureUnit | None
    status: str
    score: int = 0
    confidence: float = 0.0
    reason: str = ""


def run_bilingual_checks(
    zh_doc: ReportDocument,
    en_doc: ReportDocument,
    *,
    semantic_evaluator: SemanticEvaluator | None = None,
    enable_semantic: bool = False,
) -> BilingualCheckResult:
    zh_blocks = _blocks_from_doc(zh_doc)
    en_blocks = _blocks_from_doc(en_doc)
    zh_total = zh_doc.total_pages
    en_total = en_doc.total_pages
    text_pairs = _pair_blocks(zh_blocks, en_blocks, zh_total, en_total)

    zh_units = _disclosure_units_from_doc(zh_doc, zh_blocks)
    en_units = _disclosure_units_from_doc(en_doc, en_blocks)
    text_alignments = _text_unit_alignments(zh_units, en_units, text_pairs)
    table_alignments = _table_unit_alignments(zh_units, en_units, zh_total, en_total)
    alignments = text_alignments + table_alignments

    zh_facts = [fact for unit in zh_units if unit.kind != "financial_table" for fact in unit.facts]
    en_facts = [fact for unit in en_units if unit.kind != "financial_table" for fact in unit.facts]

    diffs: list[Diff] = []
    section_diffs, section_stats = _section_diffs_from_units(
        zh_doc,
        en_doc,
        text_alignments,
        zh_blocks=zh_blocks,
        start_index=1,
    )
    diffs.extend(section_diffs)

    table_diffs, table_stats = _table_diffs_from_units(table_alignments, start_index=len(diffs) + 1, zh_total=zh_total, en_total=en_total)
    diffs.extend(table_diffs)

    fact_pairs = _legacy_pairs_from_alignments(text_alignments)
    # LLM 优先事实对比（替代正则提取+位置配对，大幅降低误报）
    fact_diffs, fact_stats = _llm_fact_diffs(text_alignments, start_index=len(diffs) + 1)
    diffs.extend(fact_diffs)

    paragraph_diffs = _unpaired_text_unit_diffs(text_alignments, start_index=len(diffs) + 1)
    diffs.extend(paragraph_diffs)

    warnings: list[dict[str, Any]] = []
    semantic_total_pairs = 0
    semantic_reviewed_pairs = 0
    if fact_stats.get("currency_ambiguous", 0) > 0:
        warnings.append(_currency_ambiguous_warning(fact_stats["currency_ambiguous"]))
    # 注意：LLM 事实对比已合并数字核对+语义审查，不再单独调用 semantic_evaluator
    # 保留 semantic_evaluator 参数做向后兼容（orchestrator 仍会传入），但不再执行

    paired_blocks = sum(1 for alignment in text_alignments if alignment.status == "matched")
    zh_table_count = sum(1 for unit in zh_units if unit.kind == "financial_table")
    en_table_count = sum(1 for unit in en_units if unit.kind == "financial_table")
    matched_table_count = sum(1 for alignment in table_alignments if alignment.status == "matched")
    stats = {
        "zh_blocks": len(zh_blocks),
        "en_blocks": len(en_blocks),
        "paired_blocks": paired_blocks,
        "translation_coverage": round(paired_blocks / len(zh_blocks), 4) if zh_blocks else 0.0,
        "unpaired_zh_blocks": len(zh_blocks) - paired_blocks,
        "unpaired_en_blocks": len(en_blocks) - sum(
            1 for alignment in text_alignments if alignment.status == "matched" and alignment.en is not None
        ),
        "zh_table_count": zh_table_count,
        "en_table_count": en_table_count,
        "matched_table_count": matched_table_count,
        "table_coverage": round(matched_table_count / zh_table_count, 4) if zh_table_count else 0.0,
        "zh_disclosure_unit_count": len(zh_units),
        "en_disclosure_unit_count": len(en_units),
        "matched_disclosure_unit_count": sum(1 for alignment in alignments if alignment.status == "matched"),
        "ambiguous_disclosure_unit_count": sum(1 for alignment in alignments if alignment.status == "ambiguous"),
        "unmatched_disclosure_unit_count": sum(1 for alignment in alignments if alignment.status == "unmatched"),
        "zh_fact_count": len(zh_facts),
        "en_fact_count": len(en_facts),
        "cross_currency_matched": fact_stats.get("cross_currency_matched", 0),
        "cross_currency_mismatch": fact_stats.get("cross_currency_mismatch", 0),
        "currency_ambiguous": fact_stats.get("currency_ambiguous", 0),
        "llm_unchecked_pairs": fact_stats.get("llm_unchecked_pairs", 0),  # LLM 不可用时未核查的段落对数
        **section_stats,
        "table_row_diff_count": len(table_diffs),
        "table_unit_diff_pairs": table_stats.get("table_unit_diff_pairs", 0),
        "paragraph_unpaired_count": len(paragraph_diffs),
        "fact_diff_count": sum(1 for diff in diffs if diff.rule_id == "bilingual_fact_mismatch"),
        "llm_fact_regex_backfill": fact_stats.get("llm_fact_regex_backfill", 0),
        "semantic_diff_count": sum(1 for diff in diffs if diff.rule_id == "bilingual_semantic_mismatch"),
        "semantic_total_pairs": semantic_total_pairs,
        "semantic_reviewed_pairs": semantic_reviewed_pairs,
        "semantic_coverage": round(semantic_reviewed_pairs / semantic_total_pairs, 4) if semantic_total_pairs else 0.0,
    }
    return BilingualCheckResult(
        diffs=diffs,
        warnings=warnings,
        coverage_items=_alignment_coverage_items(alignments),
        stats=stats,
    )


def _semantic_unavailable_warning() -> dict[str, Any]:
    return {
        "side": "ALL",
        "flag": "bilingual_semantic_unavailable",
        "message": (
            "LLM 翻译语义审查因模型不可用（未配置有效 API Key 或服务不通）已跳过；"
            "数字、金额、单位、日期核对结果不受影响。配置有效 API Key 后可启用完整翻译语义审查。"
        ),
        "category": "semantic_translation",
        "severity": "medium",
        "blocking": False,
        "total_pages": 0,
        "scanned_pages": 0,
        "missing_pages": 0,
        "blank_pages": 0,
        "ocr_pages": 0,
        "table_pages": 0,
        "coverage_ratio": 0.0,
    }


def _semantic_uncertain_warning(issue: dict[str, Any], pair: dict[str, Any] | None) -> dict[str, Any]:
    page = int(pair.get("zh_page", 0)) if pair else 0
    return {
        "side": "ALL",
        "flag": "bilingual_semantic_review_uncertain",
        "message": str(issue.get("issue") or issue.get("headline") or "Semantic review was uncertain."),
        "category": "semantic_translation",
        "severity": "low",
        "blocking": False,
        "total_pages": 0,
        "scanned_pages": page,
        "missing_pages": 0,
        "blank_pages": 0,
        "ocr_pages": 0,
        "table_pages": 0,
        "coverage_ratio": 0.0,
    }


def _currency_ambiguous_warning(count: int) -> dict[str, Any]:
    """一侧币种可识别、另一侧不可识别的金额不匹配项汇总警告，提示人工复核。"""
    return {
        "side": "ALL",
        "flag": "bilingual_currency_ambiguous",
        "message": (
            f"{count} 项金额事实仅一侧可识别币种，无法做币种换算核对，"
            "已按量级比对并降级处理，请人工确认币种口径。"
        ),
        "category": "currency",
        "severity": "low",
        "blocking": False,
        "total_pages": 0,
        "scanned_pages": 0,
        "missing_pages": 0,
        "blank_pages": 0,
        "ocr_pages": 0,
        "table_pages": 0,
        "coverage_ratio": 0.0,
    }


def _disclosure_units_from_doc(doc: ReportDocument, blocks: list[_Block]) -> list[_DisclosureUnit]:
    units: list[_DisclosureUnit] = []
    for block in blocks:
        facts = _extract_facts(block)
        section_key = _section_key(block.section, block.text) or block.section
        units.append(
            _DisclosureUnit(
                unit_id=f"text:{block.index}",
                kind=_text_unit_kind(block, facts),
                page=block.page,
                text=block.text,
                raw_text=block.raw_text or block.text,
                section=section_key,
                facts=facts,
                confidence=0.95,
            )
        )

    table_rows_by_id: dict[str, list[_TableRow]] = {}
    for row in _table_rows_from_doc(doc):
        table_rows_by_id.setdefault(row.table_id, []).append(row)
    for table_id, rows in table_rows_by_id.items():
        if not rows:
            continue
        first = rows[0]
        units.append(
            _DisclosureUnit(
                unit_id=f"table:{table_id}",
                kind="financial_table",
                page=first.page,
                text=" ".join(row.text for row in rows)[:4000],
                section=_section_key(first.section, first.text) or first.section,
                facts=(),
                table_rows=tuple(rows),
                confidence=_table_unit_confidence(first, rows),
            )
        )
    return units


def _text_unit_kind(block: _Block, facts: tuple[_Fact, ...]) -> str:
    section_key = _section_key(block.section, block.text)
    if section_key in {"notes", "accounting_policy", "accounting_estimate"}:
        return "note_item"
    if facts:
        return "key_fact"
    return "narrative"


def _table_unit_confidence(first_row: _TableRow, rows: list[_TableRow]) -> float:
    text = _normalize(" ".join(row.text for row in rows[:8]))
    if re.search(r"_text_t\d+$", first_row.table_id):
        return 0.0
    if _LOW_CONFIDENCE_TABLE_TEXT.search(text):
        return 0.2
    if len(rows) >= 2:
        return 0.9
    if _extract_facts(_Block(index=0, page=first_row.page, text=first_row.text, section=first_row.section)):
        return 0.75
    return 0.45


def _text_unit_alignments(
    zh_units: list[_DisclosureUnit],
    en_units: list[_DisclosureUnit],
    text_pairs: list[dict[str, Any]],
) -> list[_UnitAlignment]:
    zh_text_units = {int(unit.unit_id.split(":", 1)[1]): unit for unit in zh_units if unit.unit_id.startswith("text:")}
    en_text_units = {int(unit.unit_id.split(":", 1)[1]): unit for unit in en_units if unit.unit_id.startswith("text:")}
    used_zh: set[str] = set()
    alignments: list[_UnitAlignment] = []
    for pair in text_pairs:
        zh_unit = zh_text_units.get(int(pair["zh_index"]))
        en_unit = en_text_units.get(int(pair["en_index"]))
        if not zh_unit or not en_unit:
            continue
        confidence = _alignment_confidence(int(pair.get("score", 0)), zh_unit, en_unit)
        status = "matched" if confidence >= 0.65 else "ambiguous"
        alignments.append(_UnitAlignment(zh=zh_unit, en=en_unit, status=status, score=int(pair.get("score", 0)), confidence=confidence))
        used_zh.add(zh_unit.unit_id)
    for zh_unit in (unit for unit in zh_units if unit.unit_id.startswith("text:")):
        if zh_unit.unit_id in used_zh:
            continue
        status = "unmatched" if _is_material_disclosure_unit(zh_unit) else "ambiguous"
        alignments.append(_UnitAlignment(zh=zh_unit, en=None, status=status, confidence=0.0, reason="no text candidate"))
    return alignments


def _alignment_confidence(score: int, zh_unit: _DisclosureUnit, en_unit: _DisclosureUnit) -> float:
    confidence = min(0.95, max(0.0, score / 10.0))
    if zh_unit.section and en_unit.section and zh_unit.section == en_unit.section:
        confidence = max(confidence, 0.75)
    zh_sig = _fact_signature(zh_unit.facts)
    en_sig = _fact_signature(en_unit.facts)
    if zh_sig and en_sig and zh_sig == en_sig:
        confidence = max(confidence, 0.85)
    elif zh_sig and en_sig:
        # 事实签名不同：可能把不同事项的段落配在一起，降低置信度
        confidence = max(0.0, confidence - 0.15)
    if not zh_unit.facts and not en_unit.facts and _semantic_similarity(zh_unit.text, en_unit.text) >= 0.6:
        confidence = max(confidence, 0.7)
    return confidence


def _fact_signature(facts: tuple[_Fact, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((fact.kind, fact.role) for fact in facts))


def _table_unit_alignments(
    zh_units: list[_DisclosureUnit],
    en_units: list[_DisclosureUnit],
    zh_total: int,
    en_total: int,
) -> list[_UnitAlignment]:
    zh_tables = [unit for unit in zh_units if unit.kind == "financial_table" and unit.confidence >= 0.7]
    en_tables = [unit for unit in en_units if unit.kind == "financial_table" and unit.confidence >= 0.7]
    candidates: list[tuple[int, _DisclosureUnit, _DisclosureUnit]] = []
    for zh in zh_tables:
        for en in en_tables:
            if abs(zh.page - en.page) > _page_window(zh.page, zh_total, en_total):
                continue
            score = _table_unit_match_score(zh, en)
            if score >= 4:
                candidates.append((score, zh, en))
    candidates.sort(key=lambda item: -item[0])

    used_zh: set[str] = set()
    used_en: set[str] = set()
    alignments: list[_UnitAlignment] = []
    for score, zh, en in candidates:
        if zh.unit_id in used_zh or en.unit_id in used_en:
            continue
        used_zh.add(zh.unit_id)
        used_en.add(en.unit_id)
        confidence = min(0.95, score / 8.0)
        status = "matched" if confidence >= 0.7 else "ambiguous"
        alignments.append(_UnitAlignment(zh=zh, en=en, status=status, score=score, confidence=confidence))
    for zh in zh_tables:
        if zh.unit_id not in used_zh:
            alignments.append(_UnitAlignment(zh=zh, en=None, status="unmatched", confidence=0.0, reason="no table candidate"))
    return alignments


def _table_unit_match_score(zh: _DisclosureUnit, en: _DisclosureUnit) -> int:
    zh_title = zh.table_rows[0].title if zh.table_rows else zh.text[:120]
    en_title = en.table_rows[0].title if en.table_rows else en.text[:120]
    score = 0
    if _table_title_score(zh_title, en_title):
        score += 5
    score += min(4, len(_glossary_keys_from_text(zh.text) & _glossary_keys_from_text(en.text)) * 2)
    if abs(zh.page - en.page) <= 2:
        score += 1
    return score


def _section_diffs_from_units(
    zh_doc: ReportDocument,
    en_doc: ReportDocument,
    text_alignments: list[_UnitAlignment],
    *,
    zh_blocks: list[_Block],
    start_index: int,
) -> tuple[list[Diff], dict[str, Any]]:
    diffs: list[Diff] = []
    zh_sections = _sections_from_doc(zh_doc)
    en_sections = _sections_from_doc(en_doc)
    en_by_key = {section.key: section for section in en_sections}
    paired_sections = {
        alignment.zh.section
        for alignment in text_alignments
        if alignment.status == "matched" and alignment.zh.section
    }

    # 页面比例检查：若中英文页数差异极大（>1.8x 或 <0.6x），说明报告结构差异很大，
    # 放宽章节位置要求
    zh_total = zh_doc.total_pages
    en_total = en_doc.total_pages
    extreme_page_ratio = False
    if zh_total > 0 and en_total > 0:
        ratio = en_total / zh_total
        extreme_page_ratio = ratio > 1.8 or ratio < 0.6

    # 收集英文报告全文中是否存在审计相关关键词（用于跳过 audit 章节缺失误报）
    en_has_auditor_content = _en_doc_has_auditor_keywords(en_doc)

    common_keys = [section.key for section in zh_sections if section.key in en_by_key or section.key in paired_sections]
    for section in zh_sections:
        if section.key in en_by_key or section.key in paired_sections:
            continue
        if _is_low_confidence_section(section):
            continue

        # 对已知会重组的章节，检查英文报告中是否已有相关内容
        if _is_section_expected_to_differ(section, en_doc, text_alignments, en_has_auditor_content):
            continue

        section_blocks = [b for b in zh_blocks if _section_key(b.section, b.text) == section.key]
        if not _is_material_section_gap(section, section_blocks, extreme_page_ratio=extreme_page_ratio):
            continue
        diff = _make_section_missing_diff(section, start_index + len(diffs))
        diff.severity = DiffSeverity.LOW
        diff.triage = "unresolved"
        diffs.append(diff)
    return diffs, {
        "section_pair_count": len(common_keys),
        "section_diff_count": len(diffs),
    }


# 英文报告中审计相关关键词
_AUDITOR_EN_KEYWORDS = frozenset({
    "independent auditor", "auditor's report", "auditors' report",
    "audit report", "independent auditors", "audit opinion",
    "report of the independent", "registered public accounting",
    "certified public accountants", "核数师",
})

# 双语模式预期差异白名单 — 已知的中英文报告结构性差异，不应标记为错误
_BILINGUAL_EXPECTED_PATTERNS = {
    # 章节重组：英文版可能将多个中文章节合并
    "section_merge": {
        "zh_sections": {"audit", "governance", "directors"},
        "reason": "英文版审计报告与公司治理章节常合并重组，章节标签不一致属正常",
    },
    # 表格数量差异：英文版大幅精简附表
    "table_count_diff": {
        "max_ratio": 3.0,
        "reason": "英文版通常大幅精简附表，表格数量差异属正常结构差异",
    },
    # 段落未配对：英文版正常缩略非关键披露
    "unpaired_paragraph": {
        "sections": {"notes", "disclosure", "risk"},
        "reason": "英文版常缩略或合并附注段落，非错误",
    },
}


def _en_doc_has_auditor_keywords(en_doc: ReportDocument) -> bool:
    """检查英文报告中是否存在审计相关关键词。"""
    for text_seg in en_doc.texts:
        text_lower = text_seg.text.lower()
        if any(kw in text_lower for kw in _AUDITOR_EN_KEYWORDS):
            return True
    return False


def _is_section_expected_to_differ(
    section: _SectionInfo,
    en_doc: ReportDocument,
    text_alignments: list[_UnitAlignment],
    en_has_auditor_content: bool,
) -> bool:
    """检查章节差异是否为双语报告中的预期结构性差异。"""
    key = (section.key or "").strip().lower()

    # audit 章节：若英文报告中有审计相关内容，跳过
    if key == "audit" and en_has_auditor_content:
        return True

    # 预期会合并的章节
    merge_sections = _BILINGUAL_EXPECTED_PATTERNS["section_merge"]["zh_sections"]
    if key in merge_sections:
        # 检查该章节的文本段是否已有配对
        paired_count = sum(
            1 for a in text_alignments
            if a.status == "matched" and a.zh.section and a.zh.section.lower() == key
        )
        section_blocks = sum(
            1 for a in text_alignments
            if a.zh.section and a.zh.section.lower() == key
        )
        # 若该章节过半段落已配对，说明内容存在只是章节标签不同
        if section_blocks > 0 and paired_count / section_blocks >= 0.5:
            return True

    return False


def _is_material_section_gap(
    section: _SectionInfo,
    section_blocks: list[_Block],
    *,
    extreme_page_ratio: bool = False,
) -> bool:
    if any(_is_material_bilingual_block(block) for block in section_blocks):
        # 即使有实质性段落，若全部为纯单位声明则跳过
        if all(_is_unit_declaration_only(b.text) for b in section_blocks):
            return False
        return True
    key = (section.key or "").strip().lower()
    # 核心数据章节始终检查
    if key in {"bs", "pl", "cf", "balance_sheet", "income_statement", "cash_flow", "equity_statement"}:
        return len(_normalize(section.text)) >= 8
    # 非核心章节：页面比例极端时跳过（报告结构差异大）
    if extreme_page_ratio:
        return False
    if key in {"governance", "directors", "audit", "risk", "financial_statements", "notes"}:
        return len(_normalize(section.text)) >= 8
    return False


_UNIT_DECLARATION_PATTERN = re.compile(
    r"^(?:除(?:特别注明外|另有说明外|另有说明).*?(?:单位|金额)|"
    r"以人民币(?:千元|万元|亿元)为单位|"
    r"以人民幣(?:千元|萬元|億元)為單位|"
    r"All amounts in (?:RMB|HK\$|US\$).*?(?:thousands|millions|billions)|"
    r"Expressed in (?:thousands|millions) of Renminbi|"
    r"Stated in (?:RMB\s+)?(?:thousands|millions|billions)|"
    r"单位[：:]\s*人民币(?:千元|万元|亿元)|"
    r"本报告所述|本公司董事会.*保证|备查文件"
    r").*$",
    re.I | re.M,
)


def _is_unit_declaration_only(text: str) -> bool:
    """检查文本是否仅含单位声明/样板文本，无实质内容。"""
    cleaned = _UNIT_DECLARATION_PATTERN.sub("", text).strip()
    return len(cleaned) < 15


def _table_diffs_from_units(
    table_alignments: list[_UnitAlignment],
    *,
    start_index: int,
    zh_total: int,
    en_total: int,
) -> tuple[list[Diff], dict[str, Any]]:
    diffs: list[Diff] = []
    table_unit_diff_pairs = 0
    for alignment in table_alignments:
        if alignment.zh.confidence < 0.7:
            continue
        if alignment.status != "matched" or alignment.en is None:
            if alignment.zh.table_rows:
                # EN 报告表格远少于 ZH（87 vs 247）是正常结构差异
                # 只对核心财务章节（bs/pl/cf）报 INFO，其余完全抑制
                section = (alignment.zh.section or "").lower()
                # 兼容 parser code (bs/pl/cf) 和 canonical key (balance_sheet/income_statement/cash_flow)
                if section in {"bs", "pl", "cf", "balance_sheet", "income_statement", "cash_flow", "equity_statement"}:
                    diffs.append(_make_table_missing_diff(alignment.zh.table_rows[0], list(range(len(alignment.zh.table_rows))), start_index + len(diffs), severity=DiffSeverity.INFO))
                # 非核心章节：跳过，不报差异
            continue
        zh_rows = list(alignment.zh.table_rows)
        en_rows = list(alignment.en.table_rows)
        # 表级单位差异统计：中英文表格单位归一化后不一致的配对表计数
        # （数值经币种/量级换算仍可匹配，仅记录单位口径差异供报告标注）
        zh_unit = zh_rows[0].unit if zh_rows else None
        en_unit = en_rows[0].unit if en_rows else None
        if zh_unit and en_unit and _unit_multiplier(zh_unit) != _unit_multiplier(en_unit):
            table_unit_diff_pairs += 1
        paired_zh: set[int] = set()
        paired_en: set[int] = set()
        row_pairs = _pair_rows_within_tables(
            zh_rows,
            list(range(len(zh_rows))),
            en_rows,
            list(range(len(en_rows))),
            zh_total,
            en_total,
            paired_zh,
            paired_en,
        )
        # 表格行数字核对：对已配对行提取数字事实并比对，补表格数字漏检
        for zh_i, en_i in row_pairs:
            row_diffs = _compare_row_facts(zh_rows[zh_i], en_rows[en_i], start_index + len(diffs))
            diffs.extend(row_diffs)
        if not zh_rows:
            continue
        coverage = len(paired_zh) / len(zh_rows)
        if coverage >= 0.8:
            continue
        for index, row in enumerate(zh_rows):
            if index not in paired_zh and _is_material_table_row(row):
                diffs.append(_make_table_row_missing_diff(row, start_index + len(diffs)))
    return diffs, {"table_unit_diff_pairs": table_unit_diff_pairs}


def _is_material_table_row(row: _TableRow) -> bool:
    facts = _extract_facts(_Block(index=0, page=row.page, text=row.text, section=row.section))
    if facts:
        return any(f.kind in {"amount", "percentage", "date", "share_count"} for f in facts)
    text = _normalize(row.text)
    return bool(_glossary_keys_from_text(text)) and len(text) >= 20


def _legacy_pairs_from_alignments(alignments: list[_UnitAlignment], *, min_confidence: float = 0.65) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for alignment in alignments:
        if alignment.en is None or alignment.status != "matched" or alignment.confidence < min_confidence:
            continue
        if not alignment.zh.unit_id.startswith("text:") or not alignment.en.unit_id.startswith("text:"):
            continue

        # ── 段落配对实体交叉校验 ──
        # 检查两侧段落是否讨论同一事件/话题。若完全无共享实体（编号/金额/日期），
        # 该配对极可能跨事件错配（如中文案件5配到英文案件3），降低置信度。
        zh_text = alignment.zh.text
        en_text = alignment.en.text
        confidence = alignment.confidence
        has_shared = _pair_has_shared_entities(zh_text, en_text)
        if not has_shared:
            # 无共享实体 → 置信度减半，低于阈值则跳过
            confidence = confidence * 0.5
            if confidence < min_confidence:
                continue

        zh_raw = alignment.zh.raw_text or zh_text
        en_raw = alignment.en.raw_text or en_text
        pairs.append(
            {
                "zh_index": int(alignment.zh.unit_id.split(":", 1)[1]),
                "en_index": int(alignment.en.unit_id.split(":", 1)[1]),
                "zh_page": alignment.zh.page,
                "en_page": alignment.en.page,
                "zh_text": zh_text,
                "en_text": en_text,
                "zh_raw_text": zh_raw,
                "en_raw_text": en_raw,
                "zh_section": alignment.zh.section,
                "en_section": alignment.en.section,
                "score": alignment.score,
                "alignment_confidence": confidence,
                "zh_facts": list(alignment.zh.facts),
                "en_facts": list(alignment.en.facts),
                "_has_shared_entities": has_shared,  # 标记段落对是否有共享实体
            }
        )
    return pairs


def _pair_has_shared_entities(zh_text: str, en_text: str) -> bool:
    """检查中英文段落对是否讨论同一事件/话题。

    通过提取两侧的实体标识（案件编号、公司名、关键数字、日期）并检查交集，
    判断配对是否可靠。若完全无共享实体，该配对极可能跨事件错配。

    返回 True 表示至少有一类共享实体。
    """
    zh_norm = _normalize(zh_text)
    en_norm = _normalize(en_text)

    # 1) 数字编号模式：如 "(1)", "(2)", "5.", "case 5", "第5"
    zh_numbering = set(re.findall(r"(?:[(（]\s*(\d+)\s*[)）]|(?:case|案件|事项)\s*(\d+)|第\s*(\d+)\s*(?:项|條|条|案))", zh_norm))
    en_numbering = set(re.findall(r"(?:[(（]\s*(\d+)\s*[)）]|(?:case|item)\s*(\d+))", en_norm, re.I))
    # 展平多组匹配
    zh_nums = {n for t in zh_numbering for n in t if n}
    en_nums = {n for t in en_numbering for n in t if n}
    if zh_nums and en_nums and zh_nums & en_nums:
        return True

    # 2) 金额数字前2位有效数字匹配
    zh_amounts = set()
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)", zh_norm):
        val = m.group(1).replace(",", "")
        if len(val) >= 4:  # 至少4位数才算有效金额
            zh_amounts.add(val[:3])  # 前3位数字
    en_amounts = set()
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)", en_norm):
        val = m.group(1).replace(",", "")
        if len(val) >= 4:
            en_amounts.add(val[:3])
    if zh_amounts and en_amounts and zh_amounts & en_amounts:
        return True

    # 3) 日期匹配（年-月）
    zh_dates = set(re.findall(r"20\d{2}\s*年\s*\d{1,2}\s*月", zh_norm))
    en_dates = set(re.findall(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}", en_norm, re.I))
    if zh_dates and en_dates:
        # 提取年份比较
        zh_years = {re.search(r"(20\d{2})", d).group(1) for d in zh_dates if re.search(r"(20\d{2})", d)}
        en_years = {re.search(r"(20\d{2})", d).group(1) for d in en_dates if re.search(r"(20\d{2})", d)}
        if zh_years and en_years and zh_years & en_years:
            return True

    # 4) 共享关键术语（来自 glossary）至少1个
    # 注：阈值=1而非≥2，因为短段落（如单句披露）可能只有一个可识别术语
    zh_keys = _glossary_keys_from_text(zh_text)
    en_keys = _glossary_keys_from_text(en_text)
    common = zh_keys & en_keys
    if len(common) >= 1:
        return True

    return False


def _unpaired_text_unit_diffs(alignments: list[_UnitAlignment], *, start_index: int) -> list[Diff]:
    diffs: list[Diff] = []
    for alignment in alignments:
        if alignment.en is not None or alignment.status != "unmatched":
            continue
        if not _is_material_disclosure_unit(alignment.zh):
            continue
        # 收紧：文本太短（<100字符）的未配对段落多为标题/标签/单位声明，跳过
        if len(_clean_text(alignment.zh.text)) < 100:
            continue
        block = _Block(index=int(alignment.zh.unit_id.split(":", 1)[1]), page=alignment.zh.page, text=alignment.zh.text, section=alignment.zh.section)
        diff = _make_unpaired_paragraph_diff(block, start_index + len(diffs))
        # 未配对段落差异一律降为 INFO（H 股英文版正常缩略/重组，不构成翻译错误）
        diff.severity = DiffSeverity.INFO
        diff.triage = "unresolved"
        diffs.append(diff)
    return diffs


def _is_material_disclosure_unit(unit: _DisclosureUnit) -> bool:
    if unit.kind == "financial_table":
        return unit.confidence >= 0.7
    return _is_material_bilingual_block(_Block(index=0, page=unit.page, text=unit.text, section=unit.section))


def _alignment_coverage_items(alignments: list[_UnitAlignment]) -> list[dict[str, Any]]:
    return [
        {
            "coverage_id": f"bilingual:{alignment.zh.unit_id}",
            "category": alignment.zh.kind,
            "status": alignment.status,
            "confidence": round(alignment.confidence, 4),
            "zh_page": alignment.zh.page,
            "en_page": alignment.en.page if alignment.en else None,
            "reason": alignment.reason,
        }
        for alignment in alignments
    ]


def _semantic_review_outputs(
    pairs: list[dict[str, Any]],
    issues: list[dict[str, Any]] | None,
) -> tuple[list[Diff], list[dict[str, Any]]]:
    if not issues:
        return [], []
    pair_map = {(pair["zh_index"], pair["en_index"]): pair for pair in pairs}
    translation_errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for issue in issues:
        severity = str(issue.get("severity") or "").strip().lower()
        decision = str(issue.get("decision") or issue.get("status") or issue.get("category") or "").strip().lower()
        pair = pair_map.get((int(issue.get("zh_index", -1)), int(issue.get("en_index", -1))))
        # 明确非错误：跳过
        if severity == "low" or decision in {"no_issue", "ok", "consistent", "pass"}:
            continue
        # 确认错误：high severity 或旧 decision 明确为错误
        if severity == "high" or decision in {"translation_error", "real", "mismatch", "error"}:
            translation_errors.append(issue)
        else:
            # 无明确 severity/decision → 不当确认错误，走 uncertain（修复空字符串当错误的 bug）
            warnings.append(_semantic_uncertain_warning(issue, pair))
    return _semantic_diffs(pairs, translation_errors), warnings


def _select_semantic_pairs(pairs: list[dict[str, Any]], max_pairs: int) -> list[dict[str, Any]]:
    """成本护栏：超过上限时按优先级选 top-N（含金额/百分比事实、配对分高的优先）。"""
    if len(pairs) <= max_pairs:
        return pairs

    def priority(pair: dict[str, Any]) -> tuple[int, int]:
        has_financial = any(
            getattr(f, "kind", "") in ("amount", "percentage")
            for f in pair.get("zh_facts", [])
        )
        return (1 if has_financial else 0, int(pair.get("score", 0) or 0))

    return sorted(pairs, key=priority, reverse=True)[:max_pairs]


def _evaluate_semantic_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """对一批配对段落调用 LLM 做翻译质量审查，返回 issues 列表；该批失败返回 None。"""
    payload = [
        {
            "zh_index": pair["zh_index"],
            "en_index": pair["en_index"],
            "zh_page": pair["zh_page"],
            "en_page": pair["en_page"],
            "zh_text": pair["zh_text"][:900],
            "en_text": pair["en_text"][:900],
        }
        for pair in batch
    ]
    prompt = (
        "You are reviewing a Hong Kong listed company's annual reports: the Chinese original and its English translation. "
        "The Chinese text is the authoritative source. Report ONLY substantive ERRORS where the English translation "
        "misrepresents, omits, or contradicts the Chinese original in: number, amount, percentage, date, share count, "
        "subject/entity, action, status, scope, condition, or obligation. "
        "Do NOT flag wording style, synonym choice, sentence structure, or valid translation variations — these are NOT errors. "
        "If the English faithfully translates the Chinese (even with different wording), return NO issue for that pair. "
        "Be conservative: when uncertain, do not report. "
        "For each real error set severity=\"high\" and confidence (0-1, your certainty it is a real error). "
        "Return strict JSON: "
        '{"issues":[{"zh_index":0,"en_index":0,"severity":"high","confidence":0.9,"headline":"","issue":"","review_hint":""}]}.\n\n'
        f"Pairs:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    result = cached_call(
        "reason",
        [{"role": "user", "content": prompt}],
        json_mode=True,
        temperature=0.0,
        max_tokens=4096,
    )
    if not isinstance(result, dict):
        return None
    issues = result.get("issues")
    if not isinstance(issues, list):
        return None
    return [issue for issue in issues if isinstance(issue, dict)]


def evaluate_semantic_with_llm(pairs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """分批对全量配对段落做 LLM 翻译审查；所有批次均失败时返回 None 以触发降级提示。"""
    if not pairs:
        return []
    batch_size = max(1, settings.bilingual_semantic_batch_size)
    max_pairs = settings.bilingual_semantic_max_pairs
    selected = _select_semantic_pairs(pairs, max_pairs)
    all_issues: list[dict[str, Any]] = []
    any_succeeded = False
    for start in range(0, len(selected), batch_size):
        batch = selected[start:start + batch_size]
        try:
            batch_issues = _evaluate_semantic_batch(batch)
        except Exception as exc:
            # 单批 LLM 调用异常（网络/限流/模型错误）降级为跳过该批，其他批继续
            logger.warning(f"语义审查批次 {start}-{start + len(batch)} 失败，跳过: {exc}")
            batch_issues = None
        if batch_issues is None:
            continue
        any_succeeded = True
        all_issues.extend(batch_issues)
    if not any_succeeded:
        return None
    return all_issues


# ═══════════════════════════════════════════════════════════════════
# LLM 优先事实对比 — 替代正则提取 + 位置配对的旧逻辑
# ═══════════════════════════════════════════════════════════════════

def _select_fact_compare_pairs(pairs: list[dict[str, Any]], max_pairs: int) -> list[dict[str, Any]]:
    """按优先级选择段落对：含金额事实 + 高置信度 > 比率事实 > 日期事实 > 其他。"""
    if len(pairs) <= max_pairs:
        return pairs

    def priority(pair: dict[str, Any]) -> tuple[int, int, int]:
        zh_facts = pair.get("zh_facts", [])
        en_facts = pair.get("en_facts", [])
        has_amount = any(getattr(f, "kind", "") == "amount" for f in zh_facts + en_facts)
        has_pct = any(getattr(f, "kind", "") == "percentage" for f in zh_facts + en_facts)
        has_date = any(getattr(f, "kind", "") == "date" for f in zh_facts + en_facts)
        conf = int((pair.get("alignment_confidence", 0) or 0) * 100)
        score = (
            4 if has_amount else 3 if has_pct else 2 if has_date else 1,
            conf,
        )
        return score

    return sorted(pairs, key=priority, reverse=True)[:max_pairs]


def _llm_compare_batch(
    batch: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """对一批配对段落调用 LLM 做事实对比。

    返回 issues 列表；该批失败返回 None。
    """
    payload = []
    for i, pair in enumerate(batch):
        # 优先使用 raw_text（保留排版结构），回退到 clean_text
        zh_for_llm = (pair.get("zh_raw_text") if settings.bilingual_use_raw_text_for_llm else None) or pair["zh_text"]
        en_for_llm = (pair.get("en_raw_text") if settings.bilingual_use_raw_text_for_llm else None) or pair["en_text"]
        entry = {
            "pair_index": i,
            "zh_text": zh_for_llm[:1500],  # 1500 字符覆盖绝大多数年报段落，600 太短会截掉后半部分金额
            "en_text": en_for_llm[:1500],
        }
        # Step 3.3：注入章节/页码上下文，辅助 LLM 做 Step 0 翻译验证
        if pair.get("zh_section"):
            entry["zh_section"] = pair["zh_section"]
        if pair.get("en_section"):
            entry["en_section"] = pair["en_section"]
        if pair.get("zh_page") is not None:
            entry["zh_page"] = pair["zh_page"]
        if pair.get("en_page") is not None:
            entry["en_page"] = pair["en_page"]
        payload.append(entry)
    prompt_text = load_prompt("bilingual_fact_compare.txt")
    prompt_text = prompt_text.replace("{pairs_payload}", json.dumps(payload, ensure_ascii=False))
    result = cached_call(
        "reason",
        [{"role": "user", "content": prompt_text}],
        json_mode=True,
        temperature=0.0,
        max_tokens=6144,
    )
    if not isinstance(result, dict):
        # cached_call 可能返回字符串（JSON 解析失败时的原始输出）
        if isinstance(result, str) and result.strip():
            try:
                result = json.loads(result.strip())
            except json.JSONDecodeError:
                logger.warning(f"LLM 返回非法 JSON，尝试修复...")
                # 尝试截取第一个 { 到最后一个 } 之间的内容
                text = result.strip()
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    try:
                        result = json.loads(text[start:end])
                    except json.JSONDecodeError:
                        return None
                else:
                    return None
        else:
            return None
    if not isinstance(result, dict):
        return None
    issues = result.get("issues")
    if not isinstance(issues, list):
        return None
    valid_issues = [issue for issue in issues if isinstance(issue, dict)]
    return valid_issues


def _trace_discard(layer: str, reason: str, pair: dict[str, Any], index: int) -> None:
    """诊断 trace：记录 LLM issue 被过滤层丢弃的原因。

    使用 logger.info（非 debug），确保默认日志级别可见。
    仅在 settings.bilingual_trace_diagnostics 为 True 时输出。
    """
    if not getattr(settings, "bilingual_trace_diagnostics", True):
        return
    zh_snippet = (pair.get("zh_text", "") or "")[:120].replace("\n", " ")
    en_snippet = (pair.get("en_text", "") or "")[:120].replace("\n", " ")
    logger.info(
        f"[BILINGUAL-TRACE] [{layer}] pair#{index} "
        f"zh_p{pair.get('zh_page', '?')} en_p{pair.get('en_page', '?')} "
        f"| {reason} "
        f"| zh: {zh_snippet}... "
        f"| en: {en_snippet}..."
    )


def _make_llm_fact_diff(issue: dict[str, Any], pair: dict[str, Any], index: int) -> Diff | None:
    """将 LLM 返回的 issue 转为 Diff 对象。

    包含多层幻觉过滤：
    1. 数值相同时丢弃（LLM 声称不同但数值实际相同）
    2. 值存在性校验（LLM 声称的值必须在对应文本中可找到）
    3. 二次数值校验（对 amount 类型，独立重新提取事实并核对）
    """
    severity_map = {
        "high": DiffSeverity.HIGH,
        "medium": DiffSeverity.MEDIUM,
        "low": DiffSeverity.LOW,
    }
    sev_str = issue.get("severity", "medium")
    severity = severity_map.get(sev_str, DiffSeverity.MEDIUM)
    confidence = float(issue.get("confidence", 0.5))
    fact_type = issue.get("fact_type", "amount")
    zh_value = str(issue.get("zh_value", ""))
    en_value = str(issue.get("en_value", ""))
    issue_text = str(issue.get("issue", ""))
    # 新 schema 字段（兼容旧响应）：zh_role / en_role —— LLM 给数字打的语义标签
    zh_role = str(issue.get("zh_role", "") or "").strip()
    en_role = str(issue.get("en_role", "") or "").strip()

    # ── Layer 1 (通用): LLM 幻觉过滤 — 数值解析后若相同则丢弃 ──
    # 旧版仅对 fact_type=="amount" 生效，导致 disclosure/missing/numeric 等类型
    # 即便两侧数字相同也会被报为差异（典型：43.72万 vs 43.72万 的假阳性）。
    # 新版：所有"数值类"事实类型都做数值等价拒绝；对非数值类型（如真正的披露差异），
    # 若值相等则降级置信度（可能是角色错配，但保留可见性）。
    if zh_value and en_value:
        zh_parsed = _parse_numeric_value(zh_value)
        en_parsed = _parse_numeric_value(en_value)
        if zh_parsed and en_parsed and _numeric_values_match(zh_parsed, en_parsed):
            if fact_type in ("amount", "percentage", "numeric", "date", "missing"):
                _trace_discard(
                    "Layer1",
                    f"数值相等却被报为'{fact_type}'差异: zh='{zh_value}' en='{en_value}' "
                    f"parsed=({zh_parsed[0]}, {en_parsed[0]})",
                    pair, index,
                )
                return None
            # 其它类型（disclosure 等）：值相等可能是角色错配，保留但降级 confidence
            confidence = min(confidence, 0.70)

    # ── Layer 1.5 (新增): 自洽性校验 — LLM 在 issue 文本中提到的数字必须出现在 value 字段中 ──
    # 典型假阳性（Diff #2）：LLM 报 "Chinese states 2010 but English states 2020"，
    # 但提取的 zh_value="2,010"、en_value="21" — issue 中的数字与 value 字段完全不自洽，
    # 说明 LLM 基于含糊的 value pair 编造了 plausible reason。这种 diff 应直接丢弃。
    if issue_text and (zh_value or en_value):
        issue_nums = set(_extract_numbers_from_text(issue_text))
        # 过滤掉太短的数字（避免"21"匹配年份"2021"等误命中）
        issue_nums = {n for n in issue_nums if len(n) >= 3}
        val_nums = set(_extract_numbers_from_text(f"{zh_value} {en_value}"))
        # value 中也只保留较长数字（避免 1/0 等噪声）
        val_nums_signif = {n for n in val_nums if len(n) >= 3}
        if issue_nums and val_nums_signif and not (issue_nums & val_nums_signif):
            _trace_discard(
                "Layer1.5",
                f"issue 文本数字{sorted(issue_nums)}与 value 字段数字{sorted(val_nums_signif)}不自洽",
                pair, index,
            )
            return None

    # ── Layer 2: 值存在性校验 — LLM 声称的值必须在对应文本中可找到 ──
    if fact_type in ("amount", "percentage", "date") and zh_value and en_value:
        zh_text = pair.get("zh_text", "")
        en_text = pair.get("en_text", "")
        if zh_text and not _value_appears_in_text(zh_value, zh_text):
            _trace_discard("Layer2", f"zh_value '{zh_value}' 在中文文本中找不到", pair, index)
            return None
        if en_text and not _value_appears_in_text(en_value, en_text):
            _trace_discard("Layer2", f"en_value '{en_value}' 在英文文本中找不到", pair, index)
            return None

    # ── Layer 3: 二次数值校验 — 对 amount 类型独立重新提取事实并核对 ──
    if fact_type == "amount" and zh_value and en_value:
        zh_parsed = _parse_numeric_value(zh_value)
        en_parsed = _parse_numeric_value(en_value)
        if zh_parsed and en_parsed:
            # 从两侧文本重新提取事实
            zh_block = _Block(
                index=0,
                page=pair.get("zh_page", 0),
                text=pair.get("zh_text", ""),
                section=pair.get("zh_section"),
            )
            en_block = _Block(
                index=0,
                page=pair.get("en_page", 0),
                text=pair.get("en_text", ""),
                section=pair.get("en_section"),
            )
            zh_facts = list(_extract_amount_facts(zh_block))
            en_facts = list(_extract_amount_facts(en_block))

            # 查找与 LLM 声称值最接近的事实，检查是否真不匹配
            llm_zh_val = zh_parsed[0]
            llm_en_val = en_parsed[0]
            zh_matches = [f for f in zh_facts if abs(float(f.value) - llm_zh_val) / max(abs(llm_zh_val), 1.0) < 0.05]
            en_matches = [f for f in en_facts if abs(float(f.value) - llm_en_val) / max(abs(llm_en_val), 1.0) < 0.05]

            # 若两侧都能找到对应事实，重新用正则配对检查
            if zh_matches and en_matches:
                matched = _optimal_fact_pairs(zh_matches, en_matches)
                for zf, ef in matched:
                    if _single_value_match(
                        zf.value, ef.value, zf.raw, ef.raw,
                        zf.currency, ef.currency,
                    ):
                        # 正则判定为匹配 → LLM 可能误判，降级
                        _trace_discard("Layer3", f"正则重新提取后匹配: zh='{zh_value}' en='{en_value}'", pair, index)
                        severity = DiffSeverity.INFO
                        confidence = min(confidence, 0.5)
                        break

    # ── Layer 4: 事实角色对齐校验 ──
    # 检查 LLM 报告的中文值和英文值在各自文本中是否具有相同的语义角色。
    # 如果中文值的上下文是"股本"而英文值的上下文是"分红"，说明 LLM
    # 在跨角色比较不同指标，这是最常见的误报模式之一。
    #
    # ⚠️ 但若 LLM 以高置信度报告了差异，且两个值都各自存在于其源文本中
    # （Layer 2 已验证），则"角色不同"本身可能就是翻译错误——
    # 英文版在应写 A 值的位置误写了 B 值。此时不应丢弃，而应降级并标记。
    if fact_type in ("amount", "percentage") and zh_value and en_value:
        role_match = _verify_fact_role_alignment(zh_value, en_value, pair)
        if role_match == "different_role":
            # 角色明确不同 → 不再直接丢弃。
            # 如果 LLM 置信度足够高，角色不同本身就是值得核查的翻译差异
            # （英文版可能在应写 A 值的位置误用了 B 值）。
            # 降级为 MEDIUM 并降低置信度，让审计师自行判断。
            _trace_discard("Layer4", f"角色明确不同(降级为MEDIUM): zh_value='{zh_value}' vs en_value='{en_value}'", pair, index)
            severity = min(severity, DiffSeverity.MEDIUM)  # type: ignore[arg-type]
            confidence = min(confidence, 0.60)
        elif role_match == "likely_different_role":
            # 角色可能不同但不确定 → 降级为 MEDIUM（非 INFO），保留一定可见性
            _trace_discard("Layer4", f"角色可能不同(降级为MEDIUM): zh='{zh_value}' en='{en_value}'", pair, index)
            severity = min(severity, DiffSeverity.MEDIUM)  # type: ignore[arg-type]
            confidence = min(confidence, 0.55)

    # ── Step 1 (新增): 算术校验 — 检测"英文误用其它字段数值"硬规则 ──
    # 典型场景（Section 63）：英文 25,039,945（误用股本数）vs 中文 2,503,994（股利）。
    # 触发条件 (任一)：
    #   a) en_value 数字在段落中作为另一角色独立出现，且与 zh_value 数值显著不同（_detect_misused_number_pattern）
    #   b) LLM 明确报告 zh_role ≠ en_role，且双方都填了 role 标签（schema 新增字段）
    # 满足任一即视为数学/语义可验证的强证据，无视 LLM 自评 confidence，强制 triage='real'。
    zh_text_for_check = pair.get("zh_text", "")
    en_text_for_check = pair.get("en_text", "")
    misuse = _detect_misused_number_pattern(zh_value, en_value, zh_text_for_check, en_text_for_check)
    llm_role_mismatch = bool(zh_role and en_role and zh_role.lower() != en_role.lower())

    if misuse.get("detected") or llm_role_mismatch:
        reason_parts = []
        if misuse.get("detected"):
            reason_parts.append(
                f"算术校验: en_value 复用了段落内另一字段 "
                f"(borrowed_from={misuse['borrowed_from']}, ratio={misuse['ratio']})"
            )
        if llm_role_mismatch:
            reason_parts.append(f"LLM 角色分歧: zh_role={zh_role!r} en_role={en_role!r}")
        logger.info(
            f"[BILINGUAL-TRACE] [Step1-硬规则] pair#{index} "
            f"zh_p{pair.get('zh_page')} en_p{pair.get('en_page')} | "
            f"{' + '.join(reason_parts)} → 强制 triage=real, confidence ≥ 0.92"
        )
        confidence = max(confidence, 0.92)
        severity = DiffSeverity.HIGH
        triage = "real"
    else:
        # 标准 triage: high + 高置信度 → real；阈值默认 0.85
        triage_threshold = getattr(settings, "bilingual_llm_triage_confidence", 0.85)
        triage = "real" if severity == DiffSeverity.HIGH and confidence >= triage_threshold else "unresolved"

    # 将 LLM 报告的角色信息附加到 issue_text 末尾，方便审计师在前端直接看到"中文是 X 角色，英文是 Y 角色"
    if zh_role and en_role:
        role_annotation = f"\n[语义角色] 中文={zh_role}, 英文={en_role}"
        if role_annotation not in issue_text:
            issue_text = issue_text + role_annotation

    zh_ev = Evidence(side=ReportSide.A_SHARE, page=pair["zh_page"], snippet=pair["zh_text"][:300], section=pair.get("zh_section"))
    en_ev = Evidence(side=ReportSide.H_SHARE, page=pair["en_page"], snippet=pair["en_text"][:300], section=pair.get("en_section"))

    label = {"amount": "金额", "percentage": "百分比", "date": "日期", "missing": "缺失"}.get(fact_type, "关键事实")
    headline = f"英文报告{label}与中文原文不一致"
    diff_type = DiffType.NUMERIC if fact_type in ("amount", "percentage", "date") else DiffType.DISCLOSURE

    llm_review_hint = "以中文原文为准，核对英文翻译是否准确。"

    # a_value/h_value 只接受数字，LLM 返回的是原始文本（如"人民币470亿元"），
    # 所以只放入 diff_explanation.items，不在顶层 a_value/h_value 设置
    a_value = None
    h_value = None
    # 尝试提取纯数字作为 a_value/h_value（用于排序/筛选）
    try:
        import re as _re
        nums = _re.findall(r"[\d,]+\.?\d*", zh_value.replace(",", ""))
        if nums:
            a_value = float(nums[0].replace(",", ""))
    except (ValueError, IndexError):
        pass
    try:
        import re as _re
        nums = _re.findall(r"[\d,]+\.?\d*", en_value.replace(",", ""))
        if nums:
            h_value = float(nums[0].replace(",", ""))
    except (ValueError, IndexError):
        pass

    return Diff(
        diff_id=f"BILINGUAL_LLM_{index:04d}",
        diff_type=diff_type,
        severity=severity,
        triage=triage,
        topic=LocalizedString(zh="H股中英文报告", en="H-share Chinese-English report"),
        summary=LocalizedString(zh=headline, en=f"English {fact_type} differs from Chinese original"),
        a_value=a_value,  # 纯数字（用于排序），提取失败为 None
        h_value=h_value,
        evidence=[zh_ev, en_ev],
        rule_id="bilingual_llm_fact_mismatch",
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue_text,
            location=f"中文第{pair['zh_page']}页（基准）；英文第{pair['en_page']}页（翻译）",
            items=[
                DiffExplanationItem(
                    label=label,
                    role=fact_type,
                    a_value=zh_value,
                    h_value=en_value,
                    a_page=pair["zh_page"],
                    h_page=pair["en_page"],
                    a_snippet=pair["zh_text"][:240],
                    h_snippet=pair["en_text"][:240],
                )
            ],
            review_hint=llm_review_hint,
        ),
    )


def _extract_numbers_from_text(text: str) -> list[str]:
    """从文本中提取所有规范化的数字串（去逗号，保留小数）。

    用途：
    - Layer 1.5 自洽性：检查 LLM 在 issue 字符串中提到的数字是否真的出现在 zh_value/en_value
    - Step 1 算术校验：定位段落内的所有数字，看英文 value 是否复用了另一字段数字
    """
    if not text:
        return []
    matches = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    return [m.replace(",", "") for m in matches if m]


def _detect_misused_number_pattern(
    zh_value: str,
    en_value: str,
    zh_text: str,
    en_text: str,
) -> dict:
    """检测"英文用错字段数值"的强证据模式（Section 63 类型）。

    典型场景：
    - 中文 zh_value = 2,503,994（股利总额）
    - 英文 en_value = 25,039,945（误用了同段股本数）
    - 25,039,945 同时出现在中文段落（股本基数）和英文段落（股本基数）中
    - 数值比例 ≈ 10（"少做了 ×rate/10 运算"的典型特征）

    返回:
        {
            "detected": bool,
            "ratio": float | None,
            "borrowed_from": str,   # "zh_text" | "en_text" | "zh_text+en_text"
        }
    """
    result = {"detected": False, "ratio": None, "borrowed_from": ""}
    if not (zh_value and en_value):
        return result

    zh_parsed = _parse_numeric_value(zh_value)
    en_parsed = _parse_numeric_value(en_value)
    if not (zh_parsed and en_parsed):
        return result

    zh_num = zh_parsed[0]
    en_num = en_parsed[0]
    if zh_num <= 0 or en_num <= 0:
        return result

    # 实质相等不属于"用错"（Layer 1 已处理）
    if abs(zh_num - en_num) / max(abs(zh_num), abs(en_num), 1.0) < 0.01:
        return result

    # 数值接近（< 1.5x）→ 可能是小幅误差，不归为"用错字段"
    ratio = en_num / zh_num
    if 0.67 < ratio < 1.5:
        return result

    # 取 en_value 的"原始数字串"（如 "25,039,945" 去逗号后 "25039945"）
    en_main_raw = _extract_numbers_from_text(en_value)
    if not en_main_raw:
        return result
    en_raw_num = en_main_raw[0]
    # 太短的数字会有大量误命中（如 "2", "10"）；至少 5 位才视为强证据
    if len(en_raw_num) < 5:
        return result

    zh_nums_in_text = _extract_numbers_from_text(zh_text)
    en_nums_in_text = _extract_numbers_from_text(en_text)

    borrowed: list[str] = []
    # 1) en_value 数字在中文段落出现 → 中文存在该字段作为另一角色
    if en_raw_num in zh_nums_in_text:
        borrowed.append("zh_text")
    # 2) en_value 数字在英文段落出现 ≥ 2 次 → 英文段落里这个数字承担了多个角色
    #    （en_value 自身计 1 次，再出现一次说明被复用）
    if en_text and en_text.count(en_raw_num) >= 2:
        borrowed.append("en_text")

    if borrowed:
        result["detected"] = True
        result["ratio"] = round(ratio, 3)
        result["borrowed_from"] = "+".join(borrowed)
    return result


def _value_appears_in_text(value_str: str, text: str) -> bool:
    """检查 LLM 声称的值（如 '2,503,994千元' 或 'RMB25,039,945 thousand'）
    是否在对应的段落文本中可找到。

    使用 fuzzy matching 容忍 PDF 提取导致的格式差异：
    - 逗号/空格位置的轻微偏差
    - 换行符被替换为空格后的数字分割
    """
    if not value_str or not text:
        return False
    # 提取关键数字
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", value_str.replace(",", ""))
    if not nums:
        return False
    main_num = nums[0]
    text_clean = text.replace(",", "")
    if main_num in text_clean:
        return True
    # ── Fuzzy matching: 去除所有空白和逗号后比较数字串 ──
    # PDF 提取可能将 "2,503,994" 变成 "2,503, 994" 或 "2503994"
    value_digits = re.sub(r"[,\s]", "", value_str)
    text_digits = re.sub(r"[,\s]", "", text)
    if len(value_digits) >= 4 and value_digits in text_digits:
        return True
    # ── 首尾模糊匹配: 匹配数字的前 4 位和后 3 位 ──
    if len(main_num) >= 7:
        prefix = main_num[:4]
        suffix = main_num[-3:]
        if prefix in text_clean and suffix in text_clean:
            return True
    # 尝试标准化的数字格式
    try:
        num_val = float(main_num.replace(",", ""))
        # 中文数字表示（万/亿）
        if "亿" in value_str and num_val > 0:
            yi_val = num_val / 100_000_000
            yi_str = f"{yi_val:.0f}亿" if yi_val == int(yi_val) else f"{yi_val:.1f}亿"
            return str(int(num_val)) in text_digits or yi_str in text
        if "万" in value_str and num_val > 0:
            wan_val = num_val / 10_000
            wan_str = f"{wan_val:.0f}万" if wan_val == int(wan_val) else f"{wan_val:.1f}万"
            return str(int(num_val)) in text_digits or wan_str in text
        return str(int(num_val)) in text_digits
    except ValueError:
        return main_num in text_clean


# ── 事实角色对齐校验用术语表 ──
# 每个元组 (zh_terms, en_terms, role_key) 定义一个"角色"。
# 当一个数字的上下文中出现这些术语时，该数字被认为具有该角色。
# 同角色数字才能互相比较；不同角色的数字不应比较。
_FACT_ROLE_TERMS: list[tuple[tuple[str, ...], tuple[str, ...], str]] = [
    # 股本/股份
    (("股本", "总股本", "股本总额", "股本基数", "股份总数", "注册资本"), ("share capital", "total share capital", "registered capital", "number of shares"), "share_capital"),
    # 分红/派息
    (("分红", "股利", "派息", "现金股利", "现金分红", "派发股利", "共计股利"), ("dividend", "cash dividend", "total dividend", "dividend per", "distribution"), "dividend"),
    # 可分配利润
    (("可分配利润", "未分配利润", "利润分配"), ("distributable profit", "profit available", "undistributed profit", "retained earnings"), "distributable_profit"),
    # 营业收入
    (("营业收入", "经营收入", "主营收入"), ("revenue", "operating revenue", "turnover", "operating income"), "revenue"),
    # 净利润
    (("净利润", "纯利", "年度利润"), ("net profit", "net income", "profit for the year", "annual profit"), "net_profit"),
    # 税率
    (("税率", "企业所得税率", "所得税率"), ("tax rate", "income tax rate", "corporate tax rate"), "tax_rate"),
    # 债券/票据
    (("债券", "票据", "融资券", "公司债"), ("bond", "note", "debenture", "commercial paper"), "bond"),
    # 利息/票面利率
    (("利息", "票面利率", "年利率", "利率"), ("interest", "coupon rate", "annual rate", "interest rate"), "interest_rate"),
    # 董事会决议
    (("董事会", "董事决议"), ("board", "resolution of the board", "board meeting"), "board_resolution"),
]


def _infer_fact_role(value_str: str, text: str) -> str | None:
    """根据数字在文本中的上下文推断其语义角色。

    在 value_str 附近（前后 80 字符）搜索角色术语，返回匹配的角色 key。
    若无匹配返回 None。
    """
    # 在文本中定位 value_str 中的核心数字
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", value_str.replace(",", ""))
    if not nums:
        return None
    main_num = nums[0].replace(",", "")

    # 在文本中找到该数字的位置
    pos = text.find(main_num)
    if pos < 0:
        text_clean = text.replace(",", "")
        pos = text_clean.find(main_num)
        if pos < 0:
            return None
        text = text_clean

    # 取数字前后 80 字符的上下文窗口
    window_start = max(0, pos - 80)
    window_end = min(len(text), pos + len(main_num) + 80)
    context = text[window_start:window_end]
    context_lower = context.lower()
    context_norm = _normalize(context)

    # 搜索角色术语
    matched_roles: list[str] = []
    for zh_terms, en_terms, role_key in _FACT_ROLE_TERMS:
        if any(t in context_norm for t in zh_terms):
            matched_roles.append(role_key)
        elif any(t in context_lower for t in en_terms):
            matched_roles.append(role_key)

    if len(matched_roles) == 1:
        return matched_roles[0]
    if len(matched_roles) > 1:
        # 多角色匹配：返回第一个（优先级由术语表顺序决定）
        return matched_roles[0]
    return None


def _verify_fact_role_alignment(zh_value: str, en_value: str, pair: dict[str, Any]) -> str:
    """验证 LLM 报告的中文值和英文值是否具有相同的语义角色。

    返回:
    - "same_role": 角色一致，可以比较
    - "different_role": 角色明确不同，应丢弃
    - "likely_different_role": 角色可能不同，应降级
    - "unknown": 无法判断角色，保留
    """
    zh_text = pair.get("zh_text", "")
    en_text = pair.get("en_text", "")

    zh_role = _infer_fact_role(zh_value, zh_text)
    en_role = _infer_fact_role(en_value, en_text)

    # 两端都推断出角色 → 直接比较
    if zh_role and en_role:
        if zh_role == en_role:
            return "same_role"
        else:
            return "different_role"

    # 只有中文推断出角色 → 尝试通过角色术语表反向验证英文
    if zh_role and not en_role:
        # 查找该角色对应的英文术语
        for zh_terms, en_terms, role_key in _FACT_ROLE_TERMS:
            if role_key == zh_role:
                en_text_lower = en_text.lower()
                if any(t in en_text_lower for t in en_terms):
                    return "same_role"
                # 中文有角色术语但英文没找到对应术语 → 可能是角色不同
                return "likely_different_role"

    # 只有英文推断出角色 → 类似处理
    if en_role and not zh_role:
        for zh_terms, en_terms, role_key in _FACT_ROLE_TERMS:
            if role_key == en_role:
                zh_text_norm = _normalize(zh_text)
                if any(t in zh_text_norm for t in zh_terms):
                    return "same_role"
                return "likely_different_role"

    # 两端都无法推断 → 保留
    return "unknown"


def _merge_regex_and_llm_fact_diffs(
    regex_diffs: list[Diff],
    llm_diffs: list[Diff],
    start_index: int,
) -> list[Diff]:
    """合并正则兜底差异与 LLM 差异，避免重复报告同一事实。

    去重签名使用（中文页码，英文页码，两侧原始文本中的数字集合）。
    该签名对金额/股数/日期都有效，且能容忍 LLM 输出中的单位简写。
    """

    def _numeric_signature(raw: str) -> tuple[str, ...]:
        nums = re.findall(r"\d[\d,]*(?:\.\d+)?", str(raw).replace(",", ""))
        return tuple(sorted(nums))

    def _signature(diff: Diff) -> tuple | None:
        if not diff.evidence or len(diff.evidence) < 2:
            return None
        zh_ev, en_ev = diff.evidence[0], diff.evidence[1]
        zh_raw, en_raw = "", ""
        if diff.diff_explanation and diff.diff_explanation.items:
            item = diff.diff_explanation.items[0]
            zh_raw = str(item.a_value or "")
            en_raw = str(item.h_value or "")
        return (
            zh_ev.page,
            en_ev.page,
            _numeric_signature(zh_raw),
            _numeric_signature(en_raw),
        )

    seen: set[tuple] = set()
    merged: list[Diff] = []
    for diff in llm_diffs:
        sig = _signature(diff)
        if sig:
            seen.add(sig)
        merged.append(diff)

    for diff in regex_diffs:
        # 仅把高置信度的数值/事实差异作为兜底，避免 INFO 噪声淹没报告
        # 使用可配置的最低严重度阈值（默认 high）
        min_sev_str = settings.bilingual_regex_backfill_min_severity
        _SEVERITY_ORDER = {
            DiffSeverity.INFO: 0,
            DiffSeverity.LOW: 1,
            DiffSeverity.MEDIUM: 2,
            DiffSeverity.HIGH: 3,
            DiffSeverity.CRITICAL: 4,
        }
        min_sev_map = {
            "high": DiffSeverity.HIGH,
            "medium": DiffSeverity.MEDIUM,
            "low": DiffSeverity.LOW,
        }
        min_sev = min_sev_map.get(min_sev_str, DiffSeverity.HIGH)
        if _SEVERITY_ORDER.get(diff.severity, 0) < _SEVERITY_ORDER.get(min_sev, 3):
            continue
        sig = _signature(diff)
        if sig and sig in seen:
            continue
        diff.diff_id = f"BILINGUAL_FACT_{start_index + len(merged):04d}"
        merged.append(diff)
        if sig:
            seen.add(sig)

    return merged


def _flag_translation_doubt(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """翻译验证降级标记：标记缺乏特异性共享实体的可疑配对，但不移除。

    核心思路：如果两个段落确实是翻译关系，它们应当共享"特异性实体"——
    不仅仅是通用的章节术语（如"债券/bond"），而是能唯一标识该段落的具体数字、
    编号或命名实体。缺乏特异性共享实体的配对可能是同章节内串配。

    注意：不移除任何段落对。审计场景下宁可误报也不能漏报，
    LLM prompt 中已有 STEP 0 翻译验证逻辑，能正确处理可疑对。
    """
    for pair in pairs:
        if not _is_likely_translation_pair(pair):
            pair["_translation_doubtful"] = True
            logger.debug(
                f"翻译验证标记可疑: zh_index={pair.get('zh_index')}, en_index={pair.get('en_index')}, "
                f"score={pair.get('score')} — 缺乏特异性共享实体"
            )
    return pairs


def _is_likely_translation_pair(pair: dict[str, Any]) -> bool:
    """判断一对段落是否可能是真正的翻译配对。

    只要有任何一类"特异性"共享实体，就认为是可能的翻译对。
    """
    zh_text = pair.get("zh_text", "")
    en_text = pair.get("en_text", "")
    zh_norm = _normalize(zh_text)
    en_norm = _normalize(en_text)

    # 规则 1: 共享具体金额数字（前3位有效数字匹配，至少4位数）
    zh_amounts = set()
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)", zh_norm):
        val = m.group(1).replace(",", "")
        if len(val) >= 4:
            zh_amounts.add(val[:3])
    en_amounts = set()
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)", en_norm):
        val = m.group(1).replace(",", "")
        if len(val) >= 4:
            en_amounts.add(val[:3])
    if zh_amounts and en_amounts and zh_amounts & en_amounts:
        return True

    # 规则 2: 共享编号模式
    zh_numbering = set(re.findall(r"(?:[(（]\s*(\d+)\s*[)）]|(?:case|案件|事项)\s*(\d+)|第\s*(\d+)\s*(?:项|條|条|案))", zh_norm))
    en_numbering = set(re.findall(r"(?:[(（]\s*(\d+)\s*[)）]|(?:case|item)\s*(\d+))", en_norm, re.I))
    zh_nums = {n for t in zh_numbering for n in t if n}
    en_nums = {n for t in en_numbering for n in t if n}
    if zh_nums and en_nums and zh_nums & en_nums:
        return True

    # 规则 3: 共享具体日期（年-月-日完全一致）
    zh_full_dates = set(re.findall(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", zh_norm))
    en_full_dates = set(re.findall(r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}", en_norm, re.I))
    # 也匹配 "30 March 2021" 格式
    en_full_dates |= set(re.findall(r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+20\d{2}", en_norm, re.I))
    if zh_full_dates and en_full_dates:
        return True

    # 规则 4: 共享2个以上 glossary 术语（单一术语如"债券"不足以区分同章节不同段落）
    zh_keys = _glossary_keys_from_text(zh_text)
    en_keys = _glossary_keys_from_text(en_text)
    if len(zh_keys & en_keys) >= 2:
        return True

    # 规则 5: 已通过实体交叉校验（_has_shared_entities=True 表示配对时已有共享实体）
    if pair.get("_has_shared_entities"):
        return True

    # 无任何特异性共享实体 → 可能是同章节内串配
    return False


def _llm_fact_diffs(
    alignments: list[_UnitAlignment],
    *,
    start_index: int = 1,
    fallback: bool = True,
) -> tuple[list[Diff], dict[str, int]]:
    """LLM 优先事实对比：选高置信段落对 → 批量送 LLM → 只报实质差异。

    在 LLM 之外保留一层轻量正则兜底：对金额/股数/日期等可被精确核对的事实，
    若 LLM 漏报，则把正则检出的差异补充进结果，确保高置信度数字错误不被遗漏。
    LLM 不可用时自动 fallback 到旧正则逻辑 _fact_diffs()。
    """
    if not settings.bilingual_use_llm_fact_compare:
        # 配置禁用：使用旧逻辑
        fact_pairs = _legacy_pairs_from_alignments(alignments)
        return _fact_diffs(fact_pairs, start_index=start_index)

    # 从 alignments 构建段落对
    all_pairs = _legacy_pairs_from_alignments(
        alignments,
        min_confidence=settings.bilingual_fact_min_confidence,
    )
    if not all_pairs:
        return [], {"llm_fact_pairs_reviewed": 0, "llm_fact_issues_found": 0, "llm_fact_fallback_used": False}

    # 按优先级选择 top-N
    selected = _select_fact_compare_pairs(all_pairs, settings.bilingual_fact_max_pairs)

    # ── 翻译验证降级标记 ──
    # 不再移除段落对（移除会导致正则兜底也漏检），改为标记可疑对。
    # LLM 自身有翻译验证（prompt 中 STEP 0），能正确处理可疑对。
    selected = _flag_translation_doubt(selected)

    batch_size = max(1, settings.bilingual_fact_batch_size)

    all_issues: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (issue, pair)
    any_succeeded = False
    batches_sent = 0
    unchecked_pairs_count = 0

    for start in range(0, len(selected), batch_size):
        batch = selected[start:start + batch_size]
        batches_sent += 1
        try:
            batch_result = _llm_compare_batch(batch)
        except Exception as exc:
            logger.warning(f"LLM 事实对比批次 {start}-{start + len(batch)} 失败: {exc}")
            batch_result = None
        if batch_result is None:
            unchecked_pairs_count += len(batch)
            # 诊断 trace：记录被跳过的段落对信息（页面范围），帮助定位漏报
            if getattr(settings, "bilingual_trace_diagnostics", True):
                affected_pages = sorted(set(
                    f"zh_p{p.get('zh_page', '?')}/en_p{p.get('en_page', '?')}"
                    for p in batch
                ))
                page_summary = ", ".join(affected_pages[:10])
                if len(affected_pages) > 10:
                    page_summary += f" …(+{len(affected_pages) - 10})"
                logger.warning(
                    f"[BILINGUAL-TRACE] LLM batch#{batches_sent - 1} 失败，"
                    f"{len(batch)} 对段落未被 LLM 核查（仅依赖正则兜底，无法检测翻译内容遗漏）: "
                    f"{page_summary}"
                )
            continue
        any_succeeded = True
        batch_issues = batch_result
        for issue in batch_issues:
            pair_idx = int(issue.get("pair_index", 0))
            if 0 <= pair_idx < len(batch):
                all_issues.append((issue, batch[pair_idx]))

    if not any_succeeded:
        if fallback:
            logger.warning(
                f"LLM 事实对比全部 {batches_sent} 批均失败（{unchecked_pairs_count} 对未核查），"
                f"回退到正则提取逻辑"
            )
            fact_pairs = _legacy_pairs_from_alignments(alignments)
            diffs, stats = _fact_diffs(fact_pairs, start_index=start_index)
            stats["llm_fact_fallback_used"] = True
            stats["llm_unchecked_pairs"] = unchecked_pairs_count
            return diffs, stats
        return [], {
            "llm_fact_pairs_reviewed": 0, "llm_fact_issues_found": 0,
            "llm_fact_fallback_used": True, "llm_unchecked_pairs": unchecked_pairs_count,
        }

    # 正则兜底：使用完整 all_pairs 做事实对比
    regex_diffs, regex_stats = _fact_diffs(
        all_pairs, start_index=start_index,
    )

    # 转换 LLM issues 为 Diff 对象
    llm_diffs: list[Diff] = []
    for issue, pair in all_issues:
        diff = _make_llm_fact_diff(issue, pair, start_index + len(llm_diffs))
        if diff is not None:
            llm_diffs.append(diff)

    # 合并 LLM 结果与正则兜底，去重后返回
    diffs = _merge_regex_and_llm_fact_diffs(regex_diffs, llm_diffs, start_index)

    stats = {
        "llm_fact_batches": batches_sent,
        "llm_fact_pairs_reviewed": len(selected),
        "llm_fact_issues_found": len(llm_diffs),
        "llm_fact_regex_backfill": len(diffs) - len(llm_diffs),
        "llm_fact_fallback_used": False,
        "llm_unchecked_pairs": unchecked_pairs_count,
    }
    return diffs, stats


def _blocks_from_doc(doc: ReportDocument) -> list[_Block]:
    """从 ReportDocument 提取文本 Block，仅含 doc.texts 段落。

    表格行不再混入段落配对空间——表格行匹配由 _table_row_diffs 独立处理。
    混入会导致：(1) 251 张表→~5000 行 block 淹没 455 个文本 block；
    (2) 表格行与段落错配→级联 fact_mismatch 误报。
    """
    blocks: list[_Block] = []
    for segment in doc.texts:
        text = _clean_text(segment.text)
        if len(text) < 8:
            continue
        # 传递 TextSegment.raw_text（若存在）供 LLM 比对使用
        raw_text = getattr(segment, "raw_text", None) or None
        blocks.append(_Block(
            index=len(blocks), page=segment.page, text=text,
            section=segment.section, raw_text=raw_text,
        ))
    return blocks


def _pair_blocks(zh_blocks: list[_Block], en_blocks: list[_Block], zh_total: int = 0, en_total: int = 0) -> list[dict[str, Any]]:
    min_high = getattr(settings, "bilingual_pair_min_score_high", 6)
    min_low = getattr(settings, "bilingual_pair_min_score_low", 3)

    # 阶段 1：高置信度配对（score >= min_high），按 score 降序全局最优分配
    pairs_1 = _global_best_pairs(zh_blocks, en_blocks, zh_total, en_total, min_score=min_high)
    used_zh = {p["zh_index"] for p in pairs_1}
    used_en = {p["en_index"] for p in pairs_1}

    # 阶段 2：中置信度配对（score >= min_high），对剩余块贪心匹配
    remaining_zh = [b for b in zh_blocks if b.index not in used_zh]
    pairs_2 = _greedy_pairs(remaining_zh, en_blocks, used_en.copy(), zh_total, en_total, min_score=min_high)

    # 阶段 3：低置信度配对（score >= min_low），兜底捕获边缘配对，标记为低置信
    # 这些配对仍会送入 LLM，由 LLM 的 STEP 0 翻译验证做最终裁决，不会直接产生误报
    used_zh_high = used_zh | {p["zh_index"] for p in pairs_2}
    used_en_high = used_en | {p["en_index"] for p in pairs_2}
    remaining_zh_low = [b for b in zh_blocks if b.index not in used_zh_high]
    pairs_3 = _greedy_pairs(remaining_zh_low, en_blocks, used_en_high.copy(), zh_total, en_total, min_score=min_low)
    for p in pairs_3:
        p["_low_confidence_pair"] = True
        p["score"] = min(p["score"], min_high - 1)  # 标记为低于高阈值
    if pairs_3:
        logger.info(
            f"[BILINGUAL-TRACE] 低置信配对: {len(pairs_3)} 对 score∈[{min_low},{min_high}) "
            f"(zh_blocks={len(zh_blocks)}, en_blocks={len(en_blocks)})"
        )

    pairs = pairs_1 + pairs_2 + pairs_3
    pairs = _merge_many_to_one_pairs(pairs)

    # 阶段 4：同章节内交叉配对修正 — 暂时禁用
    # 实体重叠度指标太粗糙，可能把正确配对交换成错误的。
    # 段落配对可靠性交给评分和 LLM 翻译验证（prompt STEP 0）。
    # pairs = _fix_crossing_pairs(pairs)

    # 诊断：记录未配对段落
    if getattr(settings, "bilingual_trace_diagnostics", True):
        paired_zh = {p["zh_index"] for p in pairs}
        paired_en = {p["en_index"] for p in pairs}
        unpaired_zh = len(zh_blocks) - len(paired_zh)
        unpaired_en = len(en_blocks) - len(paired_en)
        if unpaired_zh > 0 or unpaired_en > 0:
            logger.info(
                f"[BILINGUAL-TRACE] 未配对段落: zh={unpaired_zh}/{len(zh_blocks)}, "
                f"en={unpaired_en}/{len(en_blocks)} — 这些段落将不会被 LLM 核对翻译"
            )
            # 输出前 3 个未配对中文段落的摘要供人工检查
            if unpaired_zh > 0:
                unpaired_zh_samples = [
                    b for b in zh_blocks if b.index not in paired_zh
                ][:3]
                for b in unpaired_zh_samples:
                    logger.info(
                        f"[BILINGUAL-TRACE] 未配对 zh p{b.page}: {b.text[:150]}"
                    )

    return pairs


def _merge_many_to_one_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检测并合并一个中文段落对应多个连续英文段落的情况。"""
    if not pairs:
        return pairs
    zh_to_ens: dict[int, list[dict[str, Any]]] = {}
    for pair in pairs:
        zh_to_ens.setdefault(pair["zh_index"], []).append(pair)

    merged: list[dict[str, Any]] = []
    for zh_idx, en_pairs in zh_to_ens.items():
        if len(en_pairs) <= 1:
            merged.extend(en_pairs)
            continue
        en_pairs.sort(key=lambda p: p["en_index"])
        en_indices = [p["en_index"] for p in en_pairs]
        # 检查英文段落是否连续
        if max(en_indices) - min(en_indices) == len(en_indices) - 1:
            best = max(en_pairs, key=lambda p: p["score"])
            merged_text = " ".join(p["en_text"] for p in en_pairs)
            best["en_text"] = merged_text
            best["score"] = max(p["score"] for p in en_pairs)
            best["many_to_one"] = en_indices
            merged.append(best)
        else:
            # 不连续：保留最高分的一个，其余释放（不标记为配对）
            best = max(en_pairs, key=lambda p: p["score"])
            merged.append(best)
    return merged


def _fix_crossing_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """修正同章节内 zh_index 和 en_index 顺序不一致的交叉配对。

    在 H 股中英文年报中，同一章节内的段落顺序通常一致（中文第3段对应英文第3段）。
    若出现"交叉"（zh_i < zh_j 但 en_i > en_j），说明两对段落可能互相串配了。

    修正策略：对于交叉的配对组，尝试交换 en_index（即让 zh_i 配 en_j，zh_j 配 en_i），
    仅当交换后两侧的共享实体更多时才执行交换，否则保留原配对。
    """
    if not pairs or len(pairs) < 2:
        return pairs

    # 按 zh_section 分组
    by_section: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        section = pair.get("zh_section") or ""
        by_section.setdefault(section, []).append(pair)

    fixed: list[dict[str, Any]] = []
    for section, section_pairs in by_section.items():
        if len(section_pairs) < 2:
            fixed.extend(section_pairs)
            continue

        # 按 zh_index 排序
        section_pairs.sort(key=lambda p: p["zh_index"])

        # 检测交叉：对每对相邻配对，如果 en_index 顺序反了就尝试修正
        changed = True
        max_rounds = 3  # 最多修正3轮，防止无限循环
        while changed and max_rounds > 0:
            changed = False
            max_rounds -= 1
            for i in range(len(section_pairs) - 1):
                p1 = section_pairs[i]
                p2 = section_pairs[i + 1]
                # 只处理同章节且 zh_index 严格递增的相邻对
                if p1["zh_index"] >= p2["zh_index"]:
                    continue
                # 检测交叉：zh_i < zh_j 但 en_i > en_j
                if p1["en_index"] <= p2["en_index"]:
                    continue

                # 交叉检测到，尝试交换 en 配对
                # 交换前：zh_i-en_i, zh_j-en_j（交叉）
                # 交换后：zh_i-en_j, zh_j-en_i（顺序一致）
                score_before = _pair_entity_overlap(p1["zh_text"], p1["en_text"]) + _pair_entity_overlap(p2["zh_text"], p2["en_text"])
                score_after = _pair_entity_overlap(p1["zh_text"], p2["en_text"]) + _pair_entity_overlap(p2["zh_text"], p1["en_text"])

                if score_after > score_before:
                    # 交换后实体重叠更高，执行交换
                    p1["en_index"], p2["en_index"] = p2["en_index"], p1["en_index"]
                    p1["en_text"], p2["en_text"] = p2["en_text"], p1["en_text"]
                    p1["en_page"], p2["en_page"] = p2["en_page"], p1["en_page"]
                    p1["en_section"], p2["en_section"] = p2["en_section"], p1["en_section"]
                    # 重新提取英文侧事实
                    p1["en_facts"] = list(_extract_facts(_Block(index=p1["en_index"], page=p1["en_page"], text=p1["en_text"], section=p1.get("en_section"))))
                    p2["en_facts"] = list(_extract_facts(_Block(index=p2["en_index"], page=p2["en_page"], text=p2["en_text"], section=p2.get("en_section"))))
                    changed = True

        fixed.extend(section_pairs)

    return fixed


def _pair_entity_overlap(zh_text: str, en_text: str) -> int:
    """计算中英文段落之间的实体重叠度（用于交叉配对修正的辅助判断）。

    返回一个粗略的重叠分数：共享数字前缀数量 + 共享日期数量 + 共享术语数量。
    """
    score = 0
    zh_norm = _normalize(zh_text)
    en_norm = _normalize(en_text)

    # 数字前缀重叠
    zh_amounts = set()
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)", zh_norm):
        val = m.group(1).replace(",", "")
        if len(val) >= 4:
            zh_amounts.add(val[:3])
    en_amounts = set()
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)", en_norm):
        val = m.group(1).replace(",", "")
        if len(val) >= 4:
            en_amounts.add(val[:3])
    score += len(zh_amounts & en_amounts)

    # 年份重叠
    zh_years = set(re.findall(r"20\d{2}", zh_norm))
    en_years = set(re.findall(r"20\d{2}", en_norm))
    score += len(zh_years & en_years)

    # 术语重叠
    zh_keys = _glossary_keys_from_text(zh_text)
    en_keys = _glossary_keys_from_text(en_text)
    score += len(zh_keys & en_keys)

    return score


def _build_pair_dict(zh: _Block, en: _Block, score: int) -> dict[str, Any]:
    return {
        "zh_index": zh.index,
        "en_index": en.index,
        "zh_page": zh.page,
        "en_page": en.page,
        "zh_text": zh.text,
        "en_text": en.text,
        "zh_section": zh.section,
        "en_section": en.section,
        "score": score,
        "zh_facts": _extract_facts(zh),
        "en_facts": _extract_facts(en),
    }


def _global_best_pairs(zh_blocks: list[_Block], en_blocks: list[_Block], zh_total: int, en_total: int, *, min_score: int) -> list[dict[str, Any]]:
    """对所有候选对按 score 降序排列，依次配对（已配对的不再参与）。

    性能优化：预计算所有块的 profile，用 _fast_pair_score 替代 _pair_score，
    避免在 O(n×m) 循环中重复调用 _extract_facts / _glossary_keys_from_text 等昂贵函数。
    """
    indexes = _build_pair_indexes(en_blocks)
    zh_ranks = _compute_section_ranks(zh_blocks)
    zh_profiles = {
        b.index: _build_block_profile(
            b,
            section_rank=zh_ranks.get(b.index, (0, 1))[0],
            section_size=zh_ranks.get(b.index, (0, 1))[1],
        )
        for b in zh_blocks
    }
    en_profiles = indexes["en_profiles"]  # 已在 _build_pair_indexes 中预计算

    candidates: list[tuple[int, _Block, _Block]] = []
    for zh in zh_blocks:
        zh_prof = zh_profiles[zh.index]
        for en in _candidate_en_blocks(zh, indexes, set(), zh_total, en_total, zh_profile=zh_prof):
            score = _fast_pair_score(zh_prof, en_profiles[en.index], zh.page, en.page)
            if score >= min_score:
                candidates.append((score, zh, en))
    candidates.sort(key=lambda x: -x[0])
    used_zh: set[int] = set()
    used_en: set[int] = set()
    pairs: list[dict[str, Any]] = []
    for score, zh, en in candidates:
        if zh.index in used_zh or en.index in used_en:
            continue
        used_zh.add(zh.index)
        used_en.add(en.index)
        pairs.append(_build_pair_dict(zh, en, score))
    return pairs


def _greedy_pairs(zh_blocks: list[_Block], en_blocks: list[_Block], used_en: set[int], zh_total: int, en_total: int, *, min_score: int) -> list[dict[str, Any]]:
    """对剩余块贪心匹配，按顺序处理。同样使用预计算 profile 加速。"""
    pairs: list[dict[str, Any]] = []
    indexes = _build_pair_indexes(en_blocks)
    zh_ranks = _compute_section_ranks(zh_blocks)
    zh_profiles = {
        b.index: _build_block_profile(
            b,
            section_rank=zh_ranks.get(b.index, (0, 1))[0],
            section_size=zh_ranks.get(b.index, (0, 1))[1],
        )
        for b in zh_blocks
    }
    en_profiles = indexes["en_profiles"]
    for zh in zh_blocks:
        zh_prof = zh_profiles[zh.index]
        candidates = _candidate_en_blocks(zh, indexes, used_en, zh_total, en_total, zh_profile=zh_prof)
        scored = [(_fast_pair_score(zh_prof, en_profiles[en.index], zh.page, en.page), en) for en in candidates]
        scored = [(score, en) for score, en in scored if score >= min_score]
        if not scored:
            continue
        score, en = max(scored, key=lambda item: item[0])
        used_en.add(en.index)
        pairs.append(_build_pair_dict(zh, en, score))
    return pairs


def _build_pair_indexes(en_blocks: list[_Block]) -> dict[str, Any]:
    by_page: dict[int, list[_Block]] = {}
    by_section: dict[str, list[_Block]] = {}
    by_signal: dict[str, list[_Block]] = {}
    all_blocks = list(en_blocks)
    # 预计算 EN 块的 profile 和 section_key，避免在配对循环中重复计算
    en_ranks = _compute_section_ranks(en_blocks)
    en_profiles: dict[int, _BlockProfile] = {}
    en_section_keys: dict[int, str] = {}
    for block in en_blocks:
        by_page.setdefault(block.page, []).append(block)
        profile = _build_block_profile(
            block,
            section_rank=en_ranks.get(block.index, (0, 1))[0],
            section_size=en_ranks.get(block.index, (0, 1))[1],
        )
        en_profiles[block.index] = profile
        en_section_keys[block.index] = profile.section_key
        if profile.section_key:
            by_section.setdefault(profile.section_key, []).append(block)
        for signal in profile.signals:
            by_signal.setdefault(signal, []).append(block)
    return {
        "all": all_blocks,
        "by_page": by_page,
        "by_section": by_section,
        "by_signal": by_signal,
        "en_profiles": en_profiles,
        "en_section_keys": en_section_keys,
    }


# ── 预计算缓存：避免配对循环中重复提取 signals / section_key / glossary_keys ──

@dataclass(frozen=True)
class _BlockProfile:
    """为配对预计算的块特征，避免在 O(n×m) 循环中重复调用昂贵函数。"""
    signals: frozenset[str]
    section_key: str
    glossary_keys: frozenset[str]
    normalized_text: str
    topic_hits: frozenset[int]       # 命中的 _TOPIC_TERMS 索引
    action_hits: frozenset[int]      # 命中的 _ACTION_TERMS 索引
    section_rank: int = 0            # 章节内相对位置（0-based）
    section_size: int = 1            # 章节内块总数


def _compute_section_ranks(blocks: list[_Block]) -> dict[int, tuple[int, int]]:
    """计算每个块在其章节内的相对位置。

    返回 {block_index: (rank, size)} 其中 rank 是章节内 0-based 位置，
    size 是该章节的块总数。同章节内段落按文档顺序（index 递增）排列。

    用途：在配对评分中增加章节内相对位置一致性奖励，防止同章节内
    多个相似主题段落（如多个债券发行案件）互相串配。
    """
    by_section: dict[str, list[_Block]] = {}
    for block in blocks:
        key = _section_key(block.section, block.text) or block.section or ""
        by_section.setdefault(key, []).append(block)
    result: dict[int, tuple[int, int]] = {}
    for key, section_blocks in by_section.items():
        # blocks 已按 index 排序（文档顺序），同章节内保持文档顺序
        for rank, block in enumerate(section_blocks):
            result[block.index] = (rank, len(section_blocks))
    return result


def _build_block_profile(block: _Block, *, section_rank: int = 0, section_size: int = 1) -> _BlockProfile:
    """一次性提取块的全部配对特征，后续评分只做集合运算。"""
    text = _normalize(block.text)
    topic_hits: set[int] = set()
    for idx, (zh_terms, en_terms) in enumerate(_TOPIC_TERMS):
        if any(term in text for term in zh_terms) or any(term in text for term in en_terms):
            topic_hits.add(idx)
    action_hits: set[int] = set()
    for idx, (zh_terms, en_terms) in enumerate(_ACTION_TERMS):
        if any(term in text for term in zh_terms) or any(term in text for term in en_terms):
            action_hits.add(idx)
    gkeys = _glossary_keys_from_text(block.text)
    return _BlockProfile(
        signals=frozenset(_block_signals(block)),
        section_key=_section_key(block.section, block.text),
        glossary_keys=frozenset(gkeys),
        normalized_text=text,
        topic_hits=frozenset(topic_hits),
        action_hits=frozenset(action_hits),
        section_rank=section_rank,
        section_size=section_size,
    )


def _fast_pair_score(zh_profile: _BlockProfile, en_profile: _BlockProfile, zh_page: int, en_page: int) -> int:
    """基于预计算 profile 的快速配对评分，避免重复提取特征。

    比 _pair_score 快 10-50 倍，因为不调用 _extract_facts / _glossary_keys_from_text /
    _section_key / _block_signals 等昂贵函数。
    """
    score = 0
    # Section match (+2)
    if zh_profile.section_key and en_profile.section_key and zh_profile.section_key == en_profile.section_key:
        score += 2
    # Topic term overlap (+3 per shared topic)
    shared_topics = zh_profile.topic_hits & en_profile.topic_hits
    score += 3 * len(shared_topics)
    # Action term overlap (+5 per shared action)
    shared_actions = zh_profile.action_hits & en_profile.action_hits
    score += 5 * len(shared_actions)
    # Glossary key overlap (+2 per shared key)
    shared_glossary = zh_profile.glossary_keys & en_profile.glossary_keys
    score += 2 * len(shared_glossary)
    # Page proximity bonus
    page_delta = abs(zh_page - en_page)
    if page_delta <= 2:
        score += 2
    elif page_delta <= 5:
        score += 1
    # ── 章节内相对位置一致性奖励 ──
    # 在同章节中，中文第3段应匹配英文第3段，防止同章节内多个相似主题段落串配。
    # 只在两侧章节都匹配时生效（否则位置不可比）。
    if (zh_profile.section_key and en_profile.section_key
            and zh_profile.section_key == en_profile.section_key
            and zh_profile.section_size > 1 and en_profile.section_size > 1):
        zh_rel = zh_profile.section_rank / zh_profile.section_size
        en_rel = en_profile.section_rank / en_profile.section_size
        pos_delta = abs(zh_rel - en_rel)
        if pos_delta <= 0.15:
            score += 4   # 位置高度一致：强奖励
        elif pos_delta <= 0.30:
            score += 2   # 位置大致一致：中等奖励
        elif pos_delta >= 0.70:
            score -= 2   # 位置差异大：轻微惩罚（如第1段vs最后一段）
    # Semantic similarity bonus (only when score is low)
    if score < 5 and shared_glossary:
        ratio = len(shared_glossary) / max(len(zh_profile.glossary_keys | en_profile.glossary_keys), 1)
        if ratio > 0.75:
            score += 3
        elif ratio > 0.60:
            score += 1
    return score


def _candidate_en_blocks(zh: _Block, indexes: dict[str, Any], used_en: set[int], zh_total: int = 0, en_total: int = 0, *, zh_profile: _BlockProfile | None = None) -> list[_Block]:
    """获取 ZH 块的候选 EN 块，支持传入预计算 profile 避免重复计算。"""
    candidates: dict[int, _Block] = {}
    by_page: dict[int, list[_Block]] = indexes["by_page"]
    by_section: dict[str, list[_Block]] = indexes["by_section"]
    by_signal: dict[str, list[_Block]] = indexes["by_signal"]
    # 使用预计算的 section_key 和 signals，避免每次调用重复计算
    en_profiles = indexes.get("en_profiles") or {}

    window = _page_window(zh.page, zh_total, en_total)
    for page in range(max(1, zh.page - window), zh.page + window + 1):
        for block in by_page.get(page, []):
            if block.index not in used_en:
                candidates[block.index] = block

    section_key = zh_profile.section_key if zh_profile else _section_key(zh.section, zh.text)
    if section_key:
        for block in by_section.get(section_key, []):
            if block.index not in used_en:
                candidates[block.index] = block

    zh_signals = zh_profile.signals if zh_profile else frozenset(_block_signals(zh))
    for signal in zh_signals:
        for block in by_signal.get(signal, []):
            if block.index not in used_en:
                candidates[block.index] = block

    # 排序时使用预计算的 section_key 避免重复调用 _section_key
    en_section_keys = indexes.get("en_section_keys") or {}
    zh_skey = zh_profile.section_key if zh_profile else _section_key(zh.section, zh.text)
    ordered = sorted(
        candidates.values(),
        key=lambda block: (
            0 if zh_skey and en_section_keys.get(block.index) == zh_skey else 1,
            abs(block.page - zh.page),
            abs(block.index - zh.index),
            block.index,
        ),
    )
    return ordered[:_MAX_PAIR_CANDIDATES]


def _block_signals(block: _Block) -> set[str]:
    text = _normalize(block.text)
    signals: set[str] = set()
    for idx, (zh_terms, en_terms) in enumerate(_TOPIC_TERMS):
        if any(term in text for term in zh_terms) or any(term in text for term in en_terms):
            signals.add(f"topic:{idx}")
    for idx, (zh_terms, en_terms) in enumerate(_ACTION_TERMS):
        if any(term in text for term in zh_terms) or any(term in text for term in en_terms):
            signals.add(f"action:{idx}")
    for fact in _extract_facts(block):
        signals.add(f"fact:{fact.kind}:{fact.role}")
    # Glossary canonical keys 作为跨语言信号
    for key in _glossary_keys_from_text(block.text):
        signals.add(f"glossary:{key}")
    return signals


def _pair_score(zh: _Block, en: _Block) -> int:
    content_score = _content_match_score(zh, en)
    page_bonus = 2 if abs(zh.page - en.page) <= 2 else (1 if abs(zh.page - en.page) <= 5 else 0)
    semantic_bonus = 0
    if content_score < 5:
        sim = _semantic_similarity(zh.text, en.text)
        if sim > 0.75:
            semantic_bonus = 3
        elif sim > 0.60:
            semantic_bonus = 1
    return content_score + page_bonus + semantic_bonus


def _content_match_score(zh: _Block, en: _Block) -> int:
    zh_text = _normalize(zh.text)
    en_text = _normalize(en.text)
    score = 0
    if zh.section and en.section and _section_key(zh.section, zh.text) == _section_key(en.section, en.text):
        score += 2
    for zh_terms, en_terms in _TOPIC_TERMS:
        if any(term in zh_text for term in zh_terms) and any(term in en_text for term in en_terms):
            score += 3
    for zh_terms, en_terms in _ACTION_TERMS:
        if any(term in zh_text for term in zh_terms) and any(term in en_text for term in en_terms):
            score += 5
    # Glossary 语义匹配：中英文块包含同一 canonical_key 的术语
    zh_keys = _glossary_keys_from_text(zh.text)
    en_keys = _glossary_keys_from_text(en.text)
    overlap = zh_keys & en_keys
    score += 2 * len(overlap)
    zh_facts = _extract_facts(zh)
    en_facts = _extract_facts(en)
    zh_fact_types = {(fact.kind, fact.role) for fact in zh_facts}
    en_fact_types = {(fact.kind, fact.role) for fact in en_facts}
    score += 2 * len(zh_fact_types & en_fact_types)
    for zh_fact in zh_facts:
        if any(zh_fact.kind == en_fact.kind and zh_fact.role == en_fact.role and _single_value_match(zh_fact.value, en_fact.value, zh_fact.raw, en_fact.raw, zh_fact.currency, en_fact.currency) for en_fact in en_facts):
            score += 4
    return score


def _layout_diffs(
    zh_doc: ReportDocument,
    en_doc: ReportDocument,
    pairs: list[dict[str, Any]],
    zh_blocks: list[_Block] | None = None,
) -> tuple[list[Diff], dict[str, Any]]:
    diffs: list[Diff] = []
    zh_sections = _sections_from_doc(zh_doc)
    en_sections = _sections_from_doc(en_doc)
    zh_by_key = {section.key: section for section in zh_sections}
    en_by_key = {section.key: section for section in en_sections}
    common_keys = [section.key for section in zh_sections if section.key in en_by_key]

    # 构建已配对中文块的 section 映射，用于容忍度检查
    _zh_blocks = zh_blocks or _blocks_from_doc(zh_doc)
    paired_zh_indexes = {int(pair["zh_index"]) for pair in pairs}

    for section in zh_sections:
        if section.key not in en_by_key:
            if _is_low_confidence_section(section):
                continue
            # 容忍：如果该章节的大部分文本块已配对到英文段落，说明内容存在、仅 section 检测失败
            section_blocks = [b for b in _zh_blocks if _section_key(b.section, b.text) == section.key]
            if section_blocks:
                paired_count = sum(1 for b in section_blocks if b.index in paired_zh_indexes)
                if paired_count / len(section_blocks) >= 0.5:
                    continue  # 超过 50% 已配对，不报缺失
            diffs.append(_make_section_missing_diff(section, len(diffs) + 1))

    # 注：章节顺序差异在双语报告中属正常翻译排版差异，不再报出
    # en_common_order = [section.key for section in en_sections if section.key in zh_by_key]
    # if common_keys and common_keys != en_common_order: ...

    table_row_diffs = _table_row_diffs(zh_doc, en_doc, start_index=len(diffs) + 1)
    diffs.extend(table_row_diffs)

    # 所有已配对的 zh_index（阶段1>=8 或 阶段2>=3，取最低阈值）
    paired_zh_indexes = {int(pair["zh_index"]) for pair in pairs}
    paragraph_diffs = _unpaired_paragraph_diffs(
        _blocks_from_doc(zh_doc),
        paired_zh_indexes,
        start_index=len(diffs) + 1,
    )
    diffs.extend(paragraph_diffs)

    return diffs, {
        "section_pair_count": len(common_keys),
        "section_diff_count": sum(1 for diff in diffs if diff.rule_id.startswith("bilingual_section_")),
        "table_row_diff_count": len(table_row_diffs),
        "paragraph_unpaired_count": len(paragraph_diffs),
    }


def _sections_from_doc(doc: ReportDocument) -> list[_SectionInfo]:
    sections: dict[str, _SectionInfo] = {}
    for order, segment in enumerate(doc.texts):
        key = _section_key(segment.section, segment.text)
        if not key:
            continue
        text = _clean_text(segment.text)
        existing = sections.get(key)
        if existing is None or segment.page < existing.page:
            sections[key] = _SectionInfo(key=key, page=segment.page, order=order, text=text)
    return sorted(sections.values(), key=lambda item: (item.page, item.order, item.key))


_LOW_CONFIDENCE_SECTION_KEYS = {
    "contents",
    "definitions",
    "glossary",
    "abbreviations",
    "company",
    "overview",
}
_LOW_CONFIDENCE_TABLE_TEXT = re.compile(
    r"definition|definitions|glossary|contents|abbreviation|abbreviations|meansthe|means |释义|目录|简称|定义",
    re.I,
)


def _is_low_confidence_section(section: _SectionInfo) -> bool:
    key = (section.key or "").strip().lower()
    text = _normalize(section.text)
    if key in _LOW_CONFIDENCE_SECTION_KEYS:
        return True
    if key and any(token in key for token in ("definition", "contents", "glossary", "abbreviation")):
        return True
    return any(
        token in text
        for token in (
            "definitions",
            "definition",
            "contents",
            "glossary",
            "abbreviations",
            "meansthe",
            "means ",
            "释义",
            "目录",
        )
    )


def _section_key(section: str | None, text: str) -> str:
    raw = (section or "").strip().lower()
    if raw:
        # 0. 检查 parser code 直接映射（下划线 code → canonical key）
        mapped = _PARSER_CODE_TO_KEY.get(raw)
        if mapped:
            return mapped
        # 1. 尝试 glossary 精确匹配
        canonical = glossary.lookup(raw)
        if canonical:
            return canonical
        # 2. Fallback: _SECTION_KEYWORDS 子串匹配（中英对照更可靠）
        normalized = _normalize(raw)
        for key, zh_terms, en_terms in _SECTION_KEYWORDS:
            if any(term in normalized for term in zh_terms) or any(term in normalized for term in en_terms):
                return key
        # 3. 尝试 glossary 子串匹配（统一返回 canonical，不加前缀）
        for form, canonical in glossary._to_canonical.items():
            if len(form) >= 3 and form in normalized:
                return canonical
        return raw
    normalized = _normalize(text)
    # 先尝试 _SECTION_KEYWORDS（中英对照更可靠）
    for key, zh_terms, en_terms in _SECTION_KEYWORDS:
        if any(term in normalized for term in zh_terms) or any(term in normalized for term in en_terms):
            return key
    # 再尝试 glossary 子串匹配（统一返回 canonical，不加前缀）
    for form, canonical in glossary._to_canonical.items():
        if len(form) >= 3 and form in normalized:
            return canonical
    return ""


def _make_section_missing_diff(section: _SectionInfo, index: int) -> Diff:
    zh_ev = Evidence(side=ReportSide.A_SHARE, page=section.page, snippet=section.text[:300], section=section.key)
    headline = "英文报告缺失章节"
    issue = f"中文报告存在章节 {section.key}，英文报告对应章节缺失。"
    return Diff(
        diff_id=f"BILINGUAL_LAYOUT_{index:04d}",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key=f"section_missing:{section.key}",
        topic=LocalizedString(zh="章节排版", en="Section layout"),
        summary=LocalizedString(zh=headline, en="Section missing in English report"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{section.page}页（基准）；英文未定位",
            items=[
                DiffExplanationItem(
                    label="章节",
                    role="section",
                    a_value=section.key,
                    h_value="对应章节缺失",
                    a_page=section.page,
                    a_snippet=section.text[:240],
                )
            ],
            review_hint="按章节标题和顺序核对英文报告是否遗漏对应章节；页码偏移本身不判定为差异。",
        ),
        evidence=[zh_ev],
        rule_id="bilingual_section_missing",
    )


def _make_section_order_diff(zh_sections: list[_SectionInfo], en_sections: list[_SectionInfo], index: int) -> Diff:
    zh_first = zh_sections[0]
    en_first = en_sections[0]
    zh_order = " > ".join(section.key for section in zh_sections)
    en_order = " > ".join(section.key for section in en_sections)
    zh_ev = Evidence(side=ReportSide.A_SHARE, page=zh_first.page, snippet=zh_order, section=zh_first.key)
    en_ev = Evidence(side=ReportSide.H_SHARE, page=en_first.page, snippet=en_order, section=en_first.key)
    headline = "英文报告章节顺序与中文不一致"
    issue = f"中文报告章节顺序为 {zh_order}；英文报告章节顺序为 {en_order}，与中文不一致。"
    return Diff(
        diff_id=f"BILINGUAL_LAYOUT_{index:04d}",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="section_order",
        topic=LocalizedString(zh="章节排版", en="Section layout"),
        summary=LocalizedString(zh=headline, en="English section order differs from Chinese"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{zh_first.page}页（基准）；英文第{en_first.page}页（翻译）",
            items=[
                DiffExplanationItem(
                    label="章节顺序",
                    role="section_order",
                    a_value=zh_order,
                    h_value=en_order,
                    a_page=zh_first.page,
                    h_page=en_first.page,
                    a_snippet=zh_order,
                    h_snippet=en_order,
                )
            ],
            review_hint="页码可偏移，但章节相对顺序应与中文报告一致。",
        ),
        evidence=[zh_ev, en_ev],
        rule_id="bilingual_section_order_mismatch",
    )


def _table_row_diffs(zh_doc: ReportDocument, en_doc: ReportDocument, *, start_index: int) -> list[Diff]:
    zh_rows = _table_rows_from_doc(zh_doc)
    en_rows = _table_rows_from_doc(en_doc)
    zh_total = zh_doc.total_pages
    en_total = en_doc.total_pages

    # ---- 表格级配对：先按 table_id 分组，再配对 ZH/EN 表格 ----
    # ZH 可能有 251 表、EN 只有 82 表，未配对的 ZH 表整表报差异而非逐行报
    zh_tables_by_id: dict[str, list[int]] = {}  # table_id -> [row indices in zh_rows]
    for i, row in enumerate(zh_rows):
        zh_tables_by_id.setdefault(row.table_id, []).append(i)

    en_tables_by_id: dict[str, list[int]] = {}
    for i, row in enumerate(en_rows):
        en_tables_by_id.setdefault(row.table_id, []).append(i)

    # 按表格标题和页码配对 ZH/EN 表格
    paired_table_ids = _pair_table_ids(zh_rows, en_rows, zh_tables_by_id, en_tables_by_id, zh_total, en_total)

    # 仅对已配对的表格做行级匹配
    paired_zh_row_indices: set[int] = set()
    paired_en_row_indices: set[int] = set()
    for zh_tid, en_tid in paired_table_ids:
        zh_indices = zh_tables_by_id.get(zh_tid, [])
        en_indices = en_tables_by_id.get(en_tid, [])
        # 在这两个表的行之间做局部配对
        _pair_rows_within_tables(zh_rows, zh_indices, en_rows, en_indices, zh_total, en_total,
                                  paired_zh_row_indices, paired_en_row_indices)

    diffs: list[Diff] = []

    # 未配对的 ZH 表格：整表报一条差异（而非逐行报 N 条）
    paired_zh_tids = {zh_tid for zh_tid, _ in paired_table_ids}
    for zh_tid, zh_indices in zh_tables_by_id.items():
        if zh_tid in paired_zh_tids:
            # 已配对表：检查行覆盖率，仅报覆盖率低于 80% 的缺失行
            matched_count = sum(1 for i in zh_indices if i in paired_zh_row_indices)
            total_count = len(zh_indices)
            if total_count > 0 and matched_count / total_count < 0.8:
                for i in zh_indices:
                    if i not in paired_zh_row_indices:
                        diffs.append(_make_table_row_missing_diff(zh_rows[i], start_index + len(diffs)))
        else:
            # 未配对表：整表报一条，降级为 INFO 减少噪声
            first_row = zh_rows[zh_indices[0]]
            diffs.append(_make_table_missing_diff(first_row, zh_indices, start_index + len(diffs), severity=DiffSeverity.INFO))

    return diffs


def _pair_table_ids(
    zh_rows: list[_TableRow], en_rows: list[_TableRow],
    zh_tables_by_id: dict[str, list[int]], en_tables_by_id: dict[str, list[int]],
    zh_total: int, en_total: int,
) -> list[tuple[str, str]]:
    """按标题相似度和页码接近度配对 ZH/EN 表格，返回 (zh_table_id, en_table_id) 列表。"""
    # 构建每个表格的代表信息（取第一行的标题和页码）
    zh_table_info: dict[str, tuple[str, int]] = {}
    for tid, indices in zh_tables_by_id.items():
        first = zh_rows[indices[0]]
        zh_table_info[tid] = (first.title, first.page)

    en_table_info: dict[str, tuple[str, int]] = {}
    for tid, indices in en_tables_by_id.items():
        first = en_rows[indices[0]]
        en_table_info[tid] = (first.title, first.page)

    candidates: list[tuple[int, str, str]] = []  # (score, zh_tid, en_tid)
    for zh_tid, (zh_title, zh_page) in zh_table_info.items():
        zh_block = _Block(index=0, page=zh_page, text=zh_title, section=zh_title)
        for en_tid, (en_title, en_page) in en_table_info.items():
            window = _page_window(zh_page, zh_total, en_total)
            if abs(zh_page - en_page) > window:
                continue
            en_block = _Block(index=0, page=en_page, text=en_title, section=en_title)
            score = _content_match_score(zh_block, en_block)
            if score >= 3:
                candidates.append((score, zh_tid, en_tid))

    candidates.sort(key=lambda x: -x[0])
    used_zh: set[str] = set()
    used_en: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for score, zh_tid, en_tid in candidates:
        if zh_tid in used_zh or en_tid in used_en:
            continue
        used_zh.add(zh_tid)
        used_en.add(en_tid)
        pairs.append((zh_tid, en_tid))
    return pairs


def _pair_rows_within_tables(
    zh_rows: list[_TableRow], zh_indices: list[int],
    en_rows: list[_TableRow], en_indices: list[int],
    zh_total: int, en_total: int,
    out_paired_zh: set[int], out_paired_en: set[int],
) -> list[tuple[int, int]]:
    """在已配对的两张表内做行级匹配，结果写入 out_paired_zh/out_paired_en，并返回配对列表 [(zh_i, en_i)]。"""
    pairs: list[tuple[int, int]] = []
    if not zh_indices or not en_indices:
        return pairs
    # 局部配对：仅考虑这两张表的行
    candidates: list[tuple[int, int, int]] = []
    for zh_i in zh_indices:
        zh_row = zh_rows[zh_i]
        for en_i in en_indices:
            en_row = en_rows[en_i]
            score = _row_match_score(zh_row, en_row)
            if score >= 4:
                candidates.append((score, zh_i, en_i))

    candidates.sort(key=lambda x: -x[0])
    used_zh_local: set[int] = set()
    used_en_local: set[int] = set()
    for score, zh_i, en_i in candidates:
        if zh_i in used_zh_local or en_i in used_en_local:
            continue
        used_zh_local.add(zh_i)
        used_en_local.add(en_i)
        out_paired_zh.add(zh_i)
        out_paired_en.add(en_i)
        pairs.append((zh_i, en_i))
    return pairs


def _compare_row_facts(zh_row: _TableRow, en_row: _TableRow, start_index: int) -> list[Diff]:
    """对已配对的表格行提取数字事实并比对，发现表格内数字/金额/日期不一致。

    复用 _extract_facts + _fact_diffs 的配对与比对逻辑（含跨币种换算）。
    不匹配产出 bilingual_fact_mismatch，evidence 来自行文本与页码。
    """
    # 将 _TableRow 继承的单位声明注入到 block 文本中，使 _detect_report_unit() 能识别
    zh_text = zh_row.text
    en_text = en_row.text
    if zh_row.unit and not _detect_report_unit(zh_text)[0]:
        zh_text = f"单位：{zh_row.unit} {zh_text}"
    if en_row.unit and not _detect_report_unit(en_text)[0]:
        en_text = f"Unit: {en_row.unit} {en_text}"

    zh_block = _Block(index=0, page=zh_row.page, text=zh_text, section=zh_row.section)
    en_block = _Block(index=0, page=en_row.page, text=en_text, section=en_row.section)
    zh_facts = _extract_facts(zh_block)
    en_facts = _extract_facts(en_block)
    if not zh_facts or not en_facts:
        return []
    pair = {
        "zh_facts": list(zh_facts),
        "en_facts": list(en_facts),
        "zh_page": zh_row.page,
        "en_page": en_row.page,
        "zh_section": zh_row.section,
        "en_section": en_row.section,
    }
    diffs, _ = _fact_diffs([pair], start_index=start_index)
    return diffs


def _make_table_missing_diff(first_row: _TableRow, row_indices: list[int], index: int, *, severity: DiffSeverity = DiffSeverity.LOW) -> Diff:
    """构造整张表格缺失的 Diff（替代逐行报 bilingual_table_row_missing）。"""
    zh_ev = Evidence(side=ReportSide.A_SHARE, page=first_row.page, snippet=first_row.title[:300], section=first_row.section)
    headline = "英文报告缺失表格"
    row_count = len(row_indices)
    issue = f"中文报告表格 {first_row.title[:80]}（共 {row_count} 行）在英文报告中无对应表格。"
    return Diff(
        diff_id=f"BILINGUAL_LAYOUT_{index:04d}",
        diff_type=DiffType.DISCLOSURE,
        severity=severity,
        triage="unresolved",
        canonical_key=f"table_missing:{first_row.table_id}",
        topic=LocalizedString(zh="内容对应", en="Content alignment"),
        summary=LocalizedString(zh=headline, en="Table missing in English report"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{first_row.page}页（基准）；英文未定位",
            items=[
                DiffExplanationItem(
                    label="表格",
                    role="table",
                    a_value=f"{first_row.title[:80]}（{row_count} 行）",
                    h_value="对应表格缺失",
                    a_page=first_row.page,
                    a_snippet=first_row.title[:240],
                )
            ],
            review_hint="检查英文报告是否以不同结构披露了相同数据，或仅因排版差异未提取到。",
        ),
        evidence=[zh_ev],
        rule_id="bilingual_table_missing",
    )


def _global_best_table_row_pairs(
    zh_rows: list[_TableRow], en_rows: list[_TableRow],
    zh_total: int, en_total: int, *, min_score: int,
) -> list[tuple[int, int]]:
    """按 score 降序全局最优分配表格行配对。"""
    en_row_index = _build_table_row_index(en_rows)
    candidates: list[tuple[int, int, int]] = []  # (score, zh_i, en_i)
    for zh_i, zh_row in enumerate(zh_rows):
        for en_i, en_row in _candidate_table_rows(zh_row, en_row_index, set(), zh_total, en_total):
            score = _row_match_score(zh_row, en_row)
            if score >= min_score:
                candidates.append((score, zh_i, en_i))
    candidates.sort(key=lambda x: -x[0])
    used_zh: set[int] = set()
    used_en: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for score, zh_i, en_i in candidates:
        if zh_i in used_zh or en_i in used_en:
            continue
        used_zh.add(zh_i)
        used_en.add(en_i)
        pairs.append((zh_i, en_i))
    return pairs


def _greedy_table_row_pairs(
    zh_rows: list[_TableRow], used_zh: set[int],
    en_rows: list[_TableRow], used_en: set[int],
    zh_total: int, en_total: int, *, min_score: int,
) -> list[tuple[int, int]]:
    """对剩余表格行贪心匹配。"""
    en_row_index = _build_table_row_index(en_rows)
    pairs: list[tuple[int, int]] = []
    for zh_i, zh_row in enumerate(zh_rows):
        if zh_i in used_zh:
            continue
        best: tuple[int, int] | None = None
        for en_i, en_row in _candidate_table_rows(zh_row, en_row_index, used_en, zh_total, en_total):
            if en_i in used_en:
                continue
            score = _row_match_score(zh_row, en_row)
            if score >= min_score and (best is None or score > best[0]):
                best = (score, en_i)
        if best is not None:
            used_en.add(best[1])
            pairs.append((zh_i, best[1]))
    return pairs


def _build_table_row_index(en_rows: list[_TableRow]) -> dict[str, Any]:
    by_page: dict[int, list[tuple[int, _TableRow]]] = {}
    for index, row in enumerate(en_rows):
        by_page.setdefault(row.page, []).append((index, row))
    return {"rows": en_rows, "by_page": by_page}


def _table_rows_from_doc(doc: ReportDocument) -> list[_TableRow]:
    rows: list[_TableRow] = []
    for table in doc.tables:
        if _skip_bilingual_table_rows(table):
            continue
        cells_by_row: dict[int, list[Any]] = {}
        for cell in table.cells:
            cells_by_row.setdefault(cell.row, []).append(cell)
        title = _clean_text(table.title.zh or table.title.en or "")
        # 继承表格级单位声明（如"人民币千元"）到每一行
        inherited_unit = _clean_text(table.unit or "")
        for row, cells in sorted(cells_by_row.items()):
            if row == 0:
                continue
            text = _clean_text(" ".join(cell.text or "" for cell in sorted(cells, key=lambda c: c.col)))
            if len(text) < 4:
                continue
            rows.append(_TableRow(
                table_id=table.table_id, page=table.page, title=title, row=row,
                text=f"{title} {text}".strip(), section=title,
                unit=inherited_unit or None,
            ))
    return rows


def _skip_bilingual_table_rows(table: Any) -> bool:
    table_id = str(getattr(table, "table_id", "") or "")
    if re.search(r"_text_t\d+$", table_id):
        return True
    return False


def _best_table_row_match(zh_row: _TableRow, en_row_index: dict[str, Any], used_en: set[int], zh_total: int = 0, en_total: int = 0) -> int | None:
    best: tuple[int, int] | None = None
    for index, en_row in _candidate_table_rows(zh_row, en_row_index, used_en, zh_total, en_total):
        if index in used_en:
            continue
        score = _row_match_score(zh_row, en_row)
        if best is None or score > best[0]:
            best = (score, index)
    if best and best[0] >= 4:
        return best[1]
    return None


def _candidate_table_rows(zh_row: _TableRow, en_row_index: dict[str, Any], used_en: set[int], zh_total: int = 0, en_total: int = 0) -> list[tuple[int, _TableRow]]:
    by_page: dict[int, list[tuple[int, _TableRow]]] = en_row_index["by_page"]
    candidates: dict[int, _TableRow] = {}
    window = _page_window(zh_row.page, zh_total, en_total)
    for page in range(max(1, zh_row.page - window), zh_row.page + window + 1):
        for index, row in by_page.get(page, []):
            if index not in used_en:
                candidates[index] = row
    return sorted(candidates.items(), key=lambda item: (abs(item[1].page - zh_row.page), item[0]))[:_MAX_PAIR_CANDIDATES]


def _row_match_score(zh_row: _TableRow, en_row: _TableRow) -> int:
    zh_block = _Block(index=0, page=zh_row.page, text=zh_row.text, section=zh_row.section)
    en_block = _Block(index=0, page=en_row.page, text=en_row.text, section=en_row.section)
    score = _content_match_score(zh_block, en_block)
    if _table_title_score(zh_row.title, en_row.title):
        score += 2
    # 单位维度：中英文表格单位归一化后不一致则扣分，降低千元表 vs million 表错配概率
    # （单位不一致不直接判不配对，因千元/million 可能本就是翻译单位选择差异）
    if zh_row.unit and en_row.unit and _unit_multiplier(zh_row.unit) != _unit_multiplier(en_row.unit):
        score -= 2
    return score


def _table_title_score(zh_title: str, en_title: str) -> bool:
    zh_norm = _normalize(zh_title)
    en_norm = _normalize(en_title)
    # 原有 topic terms 匹配
    if any(any(zh_term in zh_norm for zh_term in zh_terms) and any(en_term in en_norm for en_term in en_terms) for zh_terms, en_terms in _TOPIC_TERMS):
        return True
    # Glossary 匹配：标题包含同一 canonical_key 的术语
    zh_keys = _glossary_keys_from_text(zh_title)
    en_keys = _glossary_keys_from_text(en_title)
    return bool(zh_keys & en_keys)


def _make_table_row_missing_diff(row: _TableRow, index: int) -> Diff:
    zh_ev = Evidence(side=ReportSide.A_SHARE, page=row.page, snippet=row.text[:300], section=row.title or row.section)
    headline = "英文报告表格行缺失"
    issue = f"中文报告表格行 {row.text[:120]} 在英文报告中缺失。"
    return Diff(
        diff_id=f"BILINGUAL_LAYOUT_{index:04d}",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key=f"table_row_missing:{row.table_id}:{row.row}",
        topic=LocalizedString(zh="章节排版", en="Section layout"),
        summary=LocalizedString(zh=headline, en="Table row missing in English report"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{row.page}页（基准）；英文未定位",
            items=[
                DiffExplanationItem(
                    label="表格行",
                    role="table_row",
                    a_value=row.text,
                    h_value="对应表格行缺失",
                    a_page=row.page,
                    a_snippet=row.text[:240],
                )
            ],
            review_hint="核对英文报告同一章节、同一表格是否遗漏该行或翻译为不同项目。",
        ),
        evidence=[zh_ev],
        rule_id="bilingual_table_row_missing",
    )


def _unpaired_paragraph_diffs(zh_blocks: list[_Block], paired_zh_indexes: set[int], *, start_index: int) -> list[Diff]:
    diffs: list[Diff] = []
    for block in zh_blocks:
        if block.index in paired_zh_indexes:
            continue
        if not _is_material_bilingual_block(block):
            continue
        diffs.append(_make_unpaired_paragraph_diff(block, start_index + len(diffs)))
    return diffs


_BOILERPLATE_PATTERN = re.compile(
    r"备查文件|本报告所述|本公司及|除非另有|forward.looking|safe.harbor|查阅|可供查阅|"
    r"本公司董事会.*保证.*不存在.*虚假记载|"
    r"以上财务数据未经审计|除特别注明外.*金额单位|"
    r"本集团(?:及子公司)?(?:及其附属公司)?(?:及子公司)?|^本公司|^公司|^本行",
    re.I,
)

_LOW_VALUE_PATTERN = re.compile(
    r"截至.*20\d{2}年.*(?:12月31日|年度|止年度)|"
    r"本公司.*(?:及其子公司|及其附属公司|及子公司)|"
    r"除.*另有.*(?:规定|说明|注明|约定)|"
    r"以下简称|"
    r"详见.*附注|"
    r"(?:如|若)?无特殊说明.*(?:人民币|RMB)",
    re.I,
)


def _is_material_bilingual_block(block: _Block) -> bool:
    text = _normalize(block.text)
    if len(text) < 50:
        return False
    if _BOILERPLATE_PATTERN.search(text):
        return False
    if _LOW_VALUE_PATTERN.search(text):
        return False
    facts = _extract_facts(block)
    if facts:
        # 金额类事实需 >= 100,000（排除小数字噪声）
        if any(f.kind == "amount" and isinstance(f.value, (int, float)) and abs(f.value) >= 100_000 for f in facts):
            return True
        # 非金额事实：仅限关键角色（每10股派息），排除普通日期和百分比
        if any(f.role == "dividend_rate_per_10_shares" for f in facts):
            return True
    # TOPIC_TERMS 段落必须同时包含金额事实才报警
    has_topic = any(any(term in text for term in zh_terms) for zh_terms, _ in _TOPIC_TERMS)
    if has_topic:
        return any(f.kind == "amount" and isinstance(f.value, (int, float)) and abs(f.value) >= 100_000 for f in facts)
    return False


def _make_unpaired_paragraph_diff(block: _Block, index: int) -> Diff:
    zh_ev = Evidence(side=ReportSide.A_SHARE, page=block.page, snippet=block.text[:300], section=block.section)
    headline = "英文报告段落缺失"
    issue = f"中文报告段落 {block.text[:120]} 在英文报告中缺失。"
    facts = _extract_facts(block)
    # 长文本且多事实保持 LOW，否则降级为 INFO 减少噪音
    severity = DiffSeverity.LOW if (len(block.text) >= 80 and len(facts) >= 2) else DiffSeverity.INFO
    return Diff(
        diff_id=f"BILINGUAL_LAYOUT_{index:04d}",
        diff_type=DiffType.DISCLOSURE,
        severity=severity,
        triage="unresolved",
        canonical_key=f"paragraph_unpaired:{block.index}",
        topic=LocalizedString(zh="内容对应", en="Content alignment"),
        summary=LocalizedString(zh=headline, en="Paragraph missing in English report"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{block.page}页（基准）；英文未定位",
            items=[
                DiffExplanationItem(
                    label="段落",
                    role="paragraph",
                    a_value=block.text[:160],
                    h_value="对应段落缺失",
                    a_page=block.page,
                    a_snippet=block.text[:240],
                )
            ],
            review_hint="优先确认英文报告是否在相邻章节或页码偏移位置披露了同一内容。",
        ),
        evidence=[zh_ev],
        rule_id="bilingual_paragraph_unpaired",
    )


def _fact_diffs(
    pairs: list[dict[str, Any]], *, start_index: int = 1,
) -> tuple[list[Diff], dict[str, int]]:
    diffs: list[Diff] = []
    fact_stats = {
        "cross_currency_matched": 0,
        "cross_currency_mismatch": 0,
        "currency_ambiguous": 0,
    }
    for pair in pairs:
        # 质量守卫：一侧有≥3条事实、另一侧为 0，说明配对可能错误，跳过事实比较
        zh_facts = pair.get("zh_facts", [])
        en_facts = pair.get("en_facts", [])
        if (len(zh_facts) >= 3 and len(en_facts) == 0) or (len(en_facts) >= 3 and len(zh_facts) == 0):
            continue

        # ── 低置信度段落对（<0.85）：正则路径极易跨子事项错配，直接跳过 ──
        # LLM 路径不受影响（LLM 有 STEP 0 翻译验证），真实差异不会漏报
        pair_confidence = pair.get("alignment_confidence", 0.5)
        # Keep the low-confidence guard for likely cross-event pairings, but
        # allow regex checks when the pair has shared entities.
        has_shared = pair.get("_has_shared_entities", False)
        if pair_confidence < 0.85 and not has_shared:
            fact_stats["low_confidence_skipped"] = fact_stats.get("low_confidence_skipped", 0) + 1
            continue

        # ── 段落级多事实风险预检测 ──
        # 当段落两侧各有≥2条同类(amount)事实时，该段落为"多事实段落"：
        # 同一段中包含多个不同指标的金额（如股本基数、分红金额、可分配利润），
        # 正则配对极易跨指标错配。整段所有差异强制降级为 INFO/unresolved。
        zh_amount_count = sum(1 for f in zh_facts if getattr(f, "kind", "") == "amount")
        en_amount_count = sum(1 for f in en_facts if getattr(f, "kind", "") == "amount")
        zh_total_facts = len(zh_facts)
        en_total_facts = len(en_facts)
        # 多事实段落：任一侧有≥2条amount，或总事实数≥3
        multi_fact_paragraph = (
            (zh_amount_count >= 2 and en_amount_count >= 2)
            or (zh_total_facts >= 3 and en_total_facts >= 2)
            or (en_total_facts >= 3 and zh_total_facts >= 2)
        )
        zh_by_role = _facts_by_role(zh_facts)
        en_by_role = _facts_by_role(en_facts)
        for key in sorted(set(zh_by_role) & set(en_by_role)):
            zh_values = zh_by_role[key]
            en_values = en_by_role[key]
            # 使用最优一对一配对，避免 [0] 索引错配
            matched = _optimal_fact_pairs(zh_values, en_values)
            # 多事实组(同段同类≥2事实)：降级为 INFO/unresolved，不再完全跳过
            # 审计场景下宁可多报几个需人工确认的差异，也不能漏掉真实翻译错误
            multi_fact_group = key[0] != "date" and (len(zh_values) >= 2 and len(en_values) >= 2)
            for zh_fact, en_fact in matched:
                cross_currency = bool(
                    zh_fact.currency and en_fact.currency and zh_fact.currency != en_fact.currency
                )
                if _single_value_match(
                    zh_fact.value, en_fact.value, zh_fact.raw, en_fact.raw,
                    zh_fact.currency, en_fact.currency,
                ):
                    if cross_currency:
                        fact_stats["cross_currency_matched"] += 1
                    continue
                # ── 量级比差异检测：1000x/10000x 等精确倍数差可能是单位换算差异而非真实错误 ──
                unit_scale_suppressed = False
                if zh_fact.kind == "amount" and isinstance(zh_fact.value, (int, float)) and isinstance(en_fact.value, (int, float)):
                    a_val, h_val = float(zh_fact.value), float(en_fact.value)
                    if a_val != 0 and h_val != 0:
                        ratio = max(a_val, h_val) / min(a_val, h_val)
                        for factor in (1_000, 10_000, 1_000_000, 100_000_000, 1_000_000_000):
                            if abs(ratio - factor) / factor <= 0.05:
                                if _is_unit_scale_compatible(zh_fact, en_fact, ratio):
                                    # 两侧 unit 字段解释了比值 → 完全抑制（非真实差异）
                                    fact_stats["unit_scale_suppressed"] = fact_stats.get("unit_scale_suppressed", 0) + 1
                                    unit_scale_suppressed = True
                                else:
                                    # 无法确定是单位差异 → 降级为 INFO / unresolved，避免淹没真实错误
                                    severity_override = DiffSeverity.INFO
                                break
                if unit_scale_suppressed:
                    continue
                # 日期类型：如果上下文不相似（成立日期 vs 报告日期），不报差异
                if zh_fact.kind == "date" and not _date_context_similar(zh_fact, en_fact):
                    continue
                # 跨币种不匹配 / 一侧币种缺失，分别计入统计
                if cross_currency:
                    fact_stats["cross_currency_mismatch"] += 1
                elif (zh_fact.currency is None) != (en_fact.currency is None) and (zh_fact.currency or en_fact.currency):
                    fact_stats["currency_ambiguous"] += 1
                severity_override: DiffSeverity | None = None
                # ── triage 决策 ──
                # 单角色一对一（同段同类各1条事实）且段落有共享实体 → 可报 real
                # 其他情况 → unresolved（同段多事实的噪音差异已在上面被 continue 跳过）
                single_fact_each_side = (
                    key[0] != "date" and len(zh_values) == 1 and len(en_values) == 1
                )
                is_reliable_match = (
                    single_fact_each_side
                    and has_shared
                    and pair_confidence >= 0.80
                )

                if multi_fact_paragraph:
                    severity_override = DiffSeverity.INFO
                # 同段同类≥2事实的角色组：降级为 INFO（可能跨指标错配，但不完全跳过）
                if multi_fact_group:
                    severity_override = DiffSeverity.INFO
                # 日期低置信仍降 INFO(待判断)
                if zh_fact.kind == "date" and _date_pair_low_confidence(zh_fact, en_fact, zh_values, en_values):
                    severity_override = DiffSeverity.INFO
                # 跨币种差异因汇率不确定性，在上述降级基础上再降一级
                if cross_currency and severity_override != DiffSeverity.INFO:
                    base_severity = DiffSeverity.HIGH if zh_fact.kind == "amount" else DiffSeverity.MEDIUM
                    severity_override = _demote_severity(base_severity)
                diff = _make_fact_diff(
                    zh_fact, en_fact, start_index + len(diffs),
                    severity_override=severity_override, cross_currency=cross_currency,
                )
                # triage: 单角色一对一 + 共享实体 → real，其他 → unresolved
                if is_reliable_match and severity_override is None:
                    diff.triage = "real"
                else:
                    diff.triage = "unresolved"
                diffs.append(diff)
            # 注：单侧多余的事实（数量不一致）暂不报差异。
            # 中英文翻译中金额/日期的合并/拆分表述很常见，
            # 报单侧缺失会产生大量误报。真正的不匹配已通过上面的最优配对检出。
    return diffs, fact_stats


def _semantic_diffs(pairs: list[dict[str, Any]], issues: list[dict[str, Any]] | None) -> list[Diff]:
    if not issues:
        return []
    pair_map = {(pair["zh_index"], pair["en_index"]): pair for pair in pairs}
    diffs: list[Diff] = []
    for idx, issue in enumerate(issues, start=1):
        pair = pair_map.get((int(issue.get("zh_index", -1)), int(issue.get("en_index", -1))))
        if not pair:
            continue
        zh_ev = Evidence(side=ReportSide.A_SHARE, page=pair["zh_page"], snippet=pair["zh_text"][:300], section=pair["zh_section"])
        en_ev = Evidence(side=ReportSide.H_SHARE, page=pair["en_page"], snippet=pair["en_text"][:300], section=pair["en_section"])
        headline = str(issue.get("headline") or "英文翻译与中文原文语义不一致")
        issue_text = str(issue.get("issue") or "英文报告翻译与中文报告存在实质差异。")
        # 翻译语义问题需人工确认（LLM 可能因段落配对错配而误报），一律进"待判断"，
        # 不进"真实差异"——让"真实差异"只放确定性高的数字/金额错误，避免翻译问题淹没真实错误
        try:
            confidence = float(issue.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        sev = DiffSeverity.MEDIUM if confidence >= 0.7 else DiffSeverity.INFO
        triage = "unresolved"
        diffs.append(
            Diff(
                diff_id=f"BILINGUAL_SEM_{idx:04d}",
                diff_type=DiffType.DISCLOSURE,
                severity=sev,
                triage=triage,
                topic=LocalizedString(zh="英文翻译核对", en="English translation review"),
                summary=LocalizedString(zh=headline, en="English translation differs from Chinese original"),
                diff_explanation=DiffExplanation(
                    headline=headline,
                    issue=issue_text,
                    location=f"中文第{zh_ev.page}页（基准）；英文第{en_ev.page}页（翻译）",
                    items=[
                        DiffExplanationItem(
                            label="语义翻译",
                            role="semantic_translation",
                            a_value=pair["zh_text"][:160],
                            h_value=pair["en_text"][:160],
                            a_page=zh_ev.page,
                            h_page=en_ev.page,
                            a_snippet=zh_ev.snippet,
                            h_snippet=en_ev.snippet,
                        )
                    ],
                    review_hint=str(issue.get("review_hint") or "以中文原文为准，核对英文翻译是否准确。"),
                ),
                evidence=[zh_ev, en_ev],
                rule_id="bilingual_semantic_mismatch",
            )
        )
    return diffs


def _make_one_sided_fact_diff(zh_fact: _Fact | None, en_fact: _Fact | None, index: int) -> Diff:
    """构造单侧事实缺失的 Diff（一侧有、另一侧无）。"""
    assert zh_fact is not None or en_fact is not None
    present = zh_fact or en_fact
    assert present is not None
    label = _FACT_LABELS.get((present.kind, present.role), _FACT_LABELS.get((present.kind, ""), "关键事实"))

    if zh_fact is not None:
        zh_ev = Evidence(side=ReportSide.A_SHARE, page=zh_fact.page, snippet=zh_fact.text[:300], section=zh_fact.section)
        en_ev = Evidence(side=ReportSide.H_SHARE, page=zh_fact.page, snippet="对应事实缺失", section=zh_fact.section)
        headline = f"英文报告缺失{label}"
        issue = f"中文原文为 {zh_fact.raw}；英文报告未披露对应{label}。"
        a_value = zh_fact.raw
        h_value = "对应事实缺失"
        a_page = zh_fact.page
        h_page = zh_fact.page
        a_snippet = zh_fact.text[:240]
        h_snippet = ""
        numeric_a = zh_fact.value if isinstance(zh_fact.value, (int, float)) else None
        numeric_h = None
        delta_val = None
    else:
        assert en_fact is not None
        zh_ev = Evidence(side=ReportSide.A_SHARE, page=en_fact.page, snippet="对应事实缺失", section=en_fact.section)
        en_ev = Evidence(side=ReportSide.H_SHARE, page=en_fact.page, snippet=en_fact.text[:300], section=en_fact.section)
        headline = f"中文报告缺失{label}"
        issue = f"英文报告为 {en_fact.raw}；中文报告未披露对应{label}。"
        a_value = "对应事实缺失"
        h_value = en_fact.raw
        a_page = en_fact.page
        h_page = en_fact.page
        a_snippet = ""
        h_snippet = en_fact.text[:240]
        numeric_a = None
        numeric_h = en_fact.value if isinstance(en_fact.value, (int, float)) else None
        delta_val = None

    return Diff(
        diff_id=f"BILINGUAL_FACT_{index:04d}",
        diff_type=DiffType.NUMERIC if present.kind in {"amount", "percentage", "date"} else DiffType.DISCLOSURE,
        severity=DiffSeverity.HIGH if present.kind == "amount" else DiffSeverity.MEDIUM,
        triage="unresolved",  # 单侧事实缺失 = 可能译略，需人工确认
        topic=LocalizedString(zh="H股中英文报告", en="H-share Chinese-English report"),
        summary=LocalizedString(zh=headline, en="Fact missing in counterpart report"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{a_page}页（基准）；英文第{h_page}页（翻译）",
            items=[
                DiffExplanationItem(
                    label=label,
                    role=present.role if present.kind == "amount" else present.kind if present.role == "general" else present.role,
                    a_value=a_value,
                    h_value=h_value,
                    delta=delta_val,
                    a_page=a_page,
                    h_page=h_page,
                    a_snippet=a_snippet,
                    h_snippet=h_snippet,
                )
            ],
            review_hint="以H股中文报告为准，核对对应位置是否遗漏事实。",
        ),
        a_value=numeric_a,
        h_value=numeric_h,
        delta=delta_val,
        evidence=[zh_ev, en_ev],
        rule_id="bilingual_fact_mismatch",
    )


def _make_fact_diff(
    zh_fact: _Fact,
    en_fact: _Fact,
    index: int,
    *,
    severity_override: DiffSeverity | None = None,
    cross_currency: bool = False,
) -> Diff:
    label = _FACT_LABELS.get((zh_fact.kind, zh_fact.role), _FACT_LABELS.get((zh_fact.kind, ""), "关键事实"))
    zh_ev = Evidence(side=ReportSide.A_SHARE, page=zh_fact.page, snippet=zh_fact.text[:300], section=zh_fact.section)
    en_ev = Evidence(side=ReportSide.H_SHARE, page=en_fact.page, snippet=en_fact.text[:300], section=en_fact.section)
    headline = f"英文报告{label}与中文原文不一致"
    issue = f"中文原文为 {zh_fact.raw}；英文报告翻译为 {en_fact.raw}"
    if cross_currency:
        zh_fx = _fx_to_hkd(zh_fact.currency)
        en_fx = _fx_to_hkd(en_fact.currency)
        rate_note = f"已按汇率换算核对（{zh_fact.currency}→HKD={zh_fx}, {en_fact.currency}→HKD={en_fx}）后仍不一致"
        issue = f"{issue}；{rate_note}，差异可能源于汇率波动或披露口径，请人工复核。"
    base_severity = DiffSeverity.HIGH if zh_fact.kind == "amount" else DiffSeverity.MEDIUM
    severity = severity_override if severity_override is not None else base_severity
    triage = "unresolved"  # 正则路径不产生 real，由上层根据可靠性标记
    review_hint = "以H股中文报告为准，核对英文报告对应位置的翻译、数字和单位倍率。"
    if cross_currency:
        review_hint = f"{review_hint} 本项涉及跨币种（{zh_fact.currency}/{en_fact.currency}），请确认汇率口径。"
    return Diff(
        diff_id=f"BILINGUAL_FACT_{index:04d}",
        diff_type=DiffType.NUMERIC if zh_fact.kind in {"amount", "percentage", "date"} else DiffType.DISCLOSURE,
        severity=severity,
        triage=triage,
        topic=LocalizedString(zh="H股中英文报告", en="H-share Chinese-English report"),
        summary=LocalizedString(zh=headline, en="English report fact differs from Chinese original"),
        diff_explanation=DiffExplanation(
            headline=headline,
            issue=issue,
            location=f"中文第{zh_fact.page}页（基准）；英文第{en_fact.page}页（翻译）",
            items=[
                DiffExplanationItem(
                    label=label,
                    role=zh_fact.role if zh_fact.kind == "amount" else zh_fact.kind if zh_fact.role == "general" else zh_fact.role,
                    a_value=zh_fact.raw,
                    h_value=en_fact.raw,
                    delta=_delta(zh_fact.value, en_fact.value),
                    a_page=zh_fact.page,
                    h_page=en_fact.page,
                    a_snippet=zh_fact.text[:240],
                    h_snippet=en_fact.text[:240],
                )
            ],
            review_hint=review_hint,
        ),
        a_value=zh_fact.value if isinstance(zh_fact.value, (int, float)) else None,
        h_value=en_fact.value if isinstance(en_fact.value, (int, float)) else None,
        delta=_delta(zh_fact.value, en_fact.value),
        evidence=[zh_ev, en_ev],
        rule_id="bilingual_fact_mismatch",
    )


@lru_cache(maxsize=20000)
def _extract_facts(block: _Block) -> tuple[_Fact, ...]:
    text = block.text
    facts: list[_Fact] = []
    facts.extend(_extract_per_10_share_facts(block))
    facts.extend(_extract_share_count_facts(block))
    facts.extend(_extract_amount_facts(block))
    facts.extend(_extract_percentage_facts(block))
    facts.extend(_extract_date_facts(block))
    return tuple(facts)


# 序数词模式（第XXX万元等，应排除）
_ORDINAL_PATTERN = re.compile(r"第\s*\d{1,3}(?:,\d{3})+\s*万?元?")


def _extract_share_count_facts(block: _Block) -> list[_Fact]:
    """提取股份数量（股/千股/万股/shares），作为独立的 kind='share_count'，不与金额混配。"""
    text = block.text
    facts: list[_Fact] = []
    # 中文：25,039,944,560股、1000万股
    for match in re.finditer(
        r"(?P<value>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>千股|万股|股)",
        text,
    ):
        value = _to_number(match.group("value"))
        unit = match.group("unit")
        multiplier = {"千股": 1_000, "万股": 10_000, "股": 1}.get(unit, 1)
        facts.append(
            _Fact(
                kind="share_count",
                role="share_count",
                value=round(value * multiplier, 4),
                raw=match.group(0).strip(),
                page=block.page,
                text=text,
                section=block.section,
                unit=unit if unit != "股" else None,
            )
        )
    # 英文：25,039,944,560 shares、10 million shares
    for match in re.finditer(
        r"(?P<value>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>thousand|million|billion)?\s*shares?\b",
        text,
        re.I,
    ):
        value = _to_number(match.group("value"))
        unit = (match.group("unit") or "").lower()
        multiplier = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}.get(unit, 1)
        facts.append(
            _Fact(
                kind="share_count",
                role="share_count",
                value=round(value * multiplier, 4),
                raw=match.group(0).strip(),
                page=block.page,
                text=text,
                section=block.section,
                unit=unit or None,
            )
        )
    return facts


# 金额角色粗分类：中英文同一指标应落入同一粗类，避免具体 glossary key 跨语言不一致导致错配
_ROLE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "assets": ("total_assets", "current_assets", "non_current_assets", "fixed_assets", "ppe", "intangible_assets", "long_term_investments", "investment_property", "construction_in_progress", "inventories"),
    "liabilities": ("total_liabilities", "current_liabilities", "non_current_liabilities", "bonds", "provisions", "leases", "employee_benefits", "income_tax"),
    "equity": ("total_equity", "share_capital", "retained_earnings", "capital_reserve", "minority_interest", "equity_statement"),
    "revenue": ("revenue", "operating_income", "gross_profit", "net_profit", "eps", "dividend", "profit_distribution"),
    "expenses": ("cogs", "selling_expenses", "admin_expenses", "finance_expenses"),
    "share": ("share_capital", "shares", "share_changes", "shareholders"),
    "other": (),
}


def _infer_amount_role(block_text: str, match_start: int, match_end: int) -> str:
    """根据金额附近的术语推断粗粒度角色。

    使用中英文共享的粗类别（assets/liabilities/equity/revenue/expenses/share/dividend/eps），
    避免具体 glossary key（如 total_assets vs 資產總額）跨语言不一致导致同一指标被分到不同 role。

    特殊检测：
    - EPS/每股收益 → amount:eps（不与 revenue 混淆）
    - 分红/股利 → amount:dividend（不与 equity/share_capital 混淆）
    - 诉讼金额 → amount:litigation（不与 revenue 混淆）
    """
    before = block_text[max(0, match_start - 80):match_start]
    after = block_text[match_end:min(len(block_text), match_end + 40)]
    context = before + after

    # ── 专项检测（优先级高于粗分类）──
    # EPS/每股收益
    if re.search(r"每股收益|每股盈利|每股基本盈利|每股摊薄盈利|basic\s+earnings\s+per\s+share|diluted\s+earnings\s+per\s+share|eps\b", context, re.I):
        return "amount:eps"
    # 分红/股利金额
    if re.search(r"现金股利|现金分红|分红总额|派息总额|股利总额|共计股利|利润分配|股利|dividend|payout|cash\s+dividend|total\s+dividend|profit\s+distribution", context, re.I):
        return "amount:dividend"
    # 股本/注册资本
    if re.search(r"股本总额|股本基数|注册资本|总股本|share\s+capital|registered\s+capital|total\s+shares\s+capital", context, re.I):
        return "amount:equity"
    # 诉讼金额（避免与营业收入混淆）
    if re.search(r"诉讼.*金额|索赔.*金额|涉诉.*金额|诉.*(?:本金|金额|标的)|litigation|claim\s+amount|dispute\s+amount", context, re.I):
        return "amount:litigation"

    keys = _glossary_keys_from_text(before) | _glossary_keys_from_text(after)
    if keys:
        for category, members in _ROLE_CATEGORIES.items():
            if any(k in members for k in keys):
                return f"amount:{category}"
        # 未命中任何类别时，退化为通用 amount:other（不再用具体 key）
        return "amount:other"
    return "amount"


# ── 报告级单位声明检测 ──────────────────────────────────────────────
# H 股中英文报告中，金额单位常在页面/章节开头声明（如"以人民币千元为单位"），
# 后续数字不再逐个标注单位。以下正则从文本中识别此类声明，供金额提取时归一化。

_REPORT_UNIT_PATTERNS_ZH: list[tuple[re.Pattern, str]] = [
    # 优先级 1：完整声明句式
    (re.compile(r"除(?:特别注明外|另有说明外|另有说明)[，,]?\s*(?:金额单位|本报告.*单位|所有金额).*?人民币(千元|万元|百万元|亿元)"), "CNY"),
    (re.compile(r"以人民币(千元|万元|百万元|亿元)(?:为单位|列示)"), "CNY"),
    (re.compile(r"单位[：:]\s*人民币(千元|万元|百万元|亿元)"), "CNY"),
    # 繁体中文变体
    (re.compile(r"除(?:另有說明外|特別註明外)[，,]?\s*(?:金額單位|所有金額).*?人民幣(千元|萬元|百萬元|億元)"), "CNY"),
    (re.compile(r"以人民幣(千元|萬元|百萬元|億元)(?:為單位|列示)"), "CNY"),
    (re.compile(r"單位[：:]\s*人民幣(千元|萬元|百萬元|億元)"), "CNY"),
    # 港币/美元
    (re.compile(r"以(?:港币|港元)(千元|万元|百万元|亿元)(?:为单位|列示)"), "HKD"),
    (re.compile(r"以(?:美元)(千元|万元|百万元|亿元)(?:为单位|列示)"), "USD"),
]

_REPORT_UNIT_PATTERNS_EN: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:all\s+amounts|expressed|stated|presented|denominated)\s+in\s+(?:RMB\s+)?(thousands|millions|billions)\s+of\s+(?:Renminbi|RMB)", re.I), "CNY"),
    (re.compile(r"(?:all\s+amounts|expressed|stated|presented)\s+in\s+(?:RMB|HK\$|US\$)\s*(thousands|millions|billions)", re.I), "CNY"),
    (re.compile(r"(?:all\s+amounts|expressed|stated|presented)\s+in\s+RMB\s*(thousands|millions|billions)", re.I), "CNY"),
    (re.compile(r"unit[：:]\s*(?:RMB|HK\$)?\s*(thousands|millions|billions)", re.I), "CNY"),
    # HKD/USD 变体
    (re.compile(r"(?:all\s+amounts|expressed|stated|presented)\s+in\s+HK\$\s*(thousands|millions|billions)", re.I), "HKD"),
    (re.compile(r"(?:all\s+amounts|expressed|stated|presented)\s+in\s+US\$\s*(thousands|millions|billions)", re.I), "USD"),
]

# 裸数字模式：当检测到报告级单位时，提取无行内单位前缀的格式化数字
_BARE_NUMBER_PATTERN = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?![\d.]|\s*(?:千|万|亿|元|thousand|million|billion|股|shares))"
)

# 报告级单位归一化仅在此章节集合中生效（金融报表主表及附注）
_FINANCIAL_SECTIONS = {"bs", "pl", "cf", "equity", "notes", "financial_statements"}


def _detect_report_unit(text: str) -> tuple[str | None, float, str | None]:
    """从文本中检测报告级单位声明。

    Returns:
        (unit_str, multiplier, currency) — unit_str 如 "千元"/"thousand"，
        multiplier 为对应数值乘数，currency 为推断的币种（CNY/HKD/USD）。
        未检测到返回 (None, 1.0, None)。
    """
    for patterns in (_REPORT_UNIT_PATTERNS_ZH, _REPORT_UNIT_PATTERNS_EN):
        for pattern, currency in patterns:
            m = pattern.search(text)
            if m:
                unit_str = m.group(1)
                return unit_str, _unit_multiplier(unit_str), currency
    return None, 1.0, None


def _extract_amount_facts(block: _Block) -> list[_Fact]:
    text = block.text
    # 先屏蔽序数词上下文，避免误提
    text = _ORDINAL_PATTERN.sub(lambda m: "█" * len(m.group(0)), text)
    facts: list[_Fact] = []
    covered_spans = _per_10_share_spans(text)

    # 检测报告级单位声明（如"以人民币千元为单位"）
    report_unit, report_multiplier, report_currency = _detect_report_unit(text)

    patterns = [
        re.compile(r"(?P<currency>RMB|HKD|USD|CNY|CNH)\s*(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>thousand|million|billion|mn|bn|k|m|mm)?\b", re.I),
        re.compile(r"(?P<currency>HK\$|US\$|¥|￥)\s*(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>thousand|million|billion|mn|bn|k|m|mm)?\b", re.I),
        re.compile(r"(?P<currency>人民币|港币|港元|美元)?\s*(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>千元|百万元|百万|万元|亿元|万亿元|元)"),
        re.compile(r"(?P<currency>RMB|HKD|USD|CNY)\s*(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>千元|万元|亿元|元)", re.I),
    ]
    # 收集所有候选（含span和value），先做span去重再构造Fact
    candidates: list[tuple[tuple[int, int], str, str, str, float]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            span = match.span()
            if _span_overlaps(span, covered_spans):
                continue
            unit = match.group("unit") or ""
            currency = match.group("currency") or ""
            if not unit and not currency:
                # 无行内单位/币种，但有报告级单位 → 仅在金融章节使用报告级单位
                if report_unit and block.section in _FINANCIAL_SECTIONS:
                    unit = report_unit
                else:
                    continue
            raw = match.group(0).strip()
            value = _to_number(match.group("value")) * _unit_multiplier(unit)
            candidates.append((span, unit, currency, raw, value))

    # 注意：裸数字（无行内单位前缀）的提取仅用于表格行（由 _compare_row_facts 的单位传播处理），
    # 文本段落中的裸数字不额外扫描——避免附注段落中大量非对齐数字产生错误配对。
    # 原始 patterns 中无行内 unit 但有 currency 的金额仍通过 report_unit 归一化。

    # 按span长度降序，过滤被长match完全覆盖的短match
    candidates.sort(key=lambda x: (x[0][1] - x[0][0]), reverse=True)
    used_spans: list[tuple[int, int]] = []
    for span, unit, currency, raw, value in candidates:
        if any(_span_contains(outer, span) for outer in used_spans):
            continue
        used_spans.append(span)
        # 推断金额角色：同一 role="amount" 的金额会互相错配，用上下文术语区分
        role = _infer_amount_role(text, span[0], span[1])
        facts.append(
            _Fact(
                kind="amount",
                role=role,
                value=round(value, 4),
                raw=raw,
                page=block.page,
                text=text,
                section=block.section,
                currency=_normalize_currency(currency) if currency else (report_currency if report_unit else None),
                unit=unit,
            )
        )
    return facts


def _extract_per_10_share_facts(block: _Block) -> list[_Fact]:
    """提取每股派息/每10股派息事实。

    覆盖中英文常见表达，包括中间夹有修饰语（如 "(tax inclusive)"）的情况。
    """
    text = block.text
    facts: list[_Fact] = []
    patterns = [
        # 中文：每 10 股 ... 人民币 1.00 元
        re.compile(r"每\s*10\s*股.{0,28}?(?:人民币|港币|港元|美元)?\s*(\d+(?:\.\d+)?)\s*元"),
        # 英文：RMB1.00 (tax inclusive) per 10 shares — 允许数字与 per 10 shares 之间有括号修饰
        re.compile(r"(?:RMB|HKD|USD)\s*(\d+(?:\.\d+)?)\s*(?:\([^)]+\))?\s*per\s*10\s*shares", re.I),
        # 英文：RMB 1.00 per 10 shares（无括号）
        re.compile(r"(?:RMB|HKD|USD)\s*(\d+(?:\.\d+)?)\s*per\s*10\s*shares", re.I),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            facts.append(
                _Fact(
                    kind="amount",
                    role="dividend_rate_per_10_shares",
                    value=round(_to_number(match.group(1)), 4),
                    raw=match.group(0).strip(),
                    page=block.page,
                    text=text,
                    section=block.section,
                    unit=None,
                )
            )
    return facts


# 非财务百分比上下文（置信区间、完成率等应排除）
# 非财务百分比上下文（置信区间、增长率等应排除）
# 双语报告中大量百分比属于"口径/详略差异"而非翻译错误，排除可大幅降噪。
_PERCENTAGE_EXCLUDE_CONTEXT = re.compile(
    r"置信区间|完成率|进度|满意度|通过率|合格率|同比增长率?|环比增长率?|折旧率|税率|"
    r"年化|年化收益率|年化利率|平均利率|加权平均|"
    r"持股比例|占比|比例|占.*比|比重|份额|"
    r"覆盖率|拨备覆盖率|充足率|资本充足率|"
    r"margin|ratio|rate|yield|return|"
    r"ownership|proportion|share\s+of|percentage\s+of|"
    r"year.on.year|compound\s+growth|cagr",
    re.I,
)

# 百分比上下文角色：区分"票面利率 5%"和"持股比例 51.2%"等不同含义的百分比，
# 使 _facts_by_role 按 (kind, role) 分组后避免跨角色错配。
# 注意：长模式必须排在短模式前面，避免"利率"误匹配"毛利率"中的子串。
_PERCENTAGE_ROLE_PATTERNS = [
    # 长模式优先（避免子串误匹配）
    (re.compile(r"票面利率|coupon\s*rate", re.I), "coupon_rate"),
    (re.compile(r"资产负债率|debt.to.asset|gearing\s*ratio|leverage\s*ratio|資產負債率", re.I), "debt_asset_ratio"),
    (re.compile(r"净资产收益率|return.on.equity|ROE|淨資產收益率", re.I), "roe"),
    (re.compile(r"总资产收益率|return.on.asset|ROA|總資產收益率", re.I), "roa"),
    (re.compile(r"毛利率|gross\s*(?:profit\s*)?margin|gross\s*margin", re.I), "gross_margin"),
    (re.compile(r"净利率|net\s*(?:profit\s*)?margin|net\s*margin", re.I), "net_margin"),
    (re.compile(r"流动比率|current\s*ratio|流動比率", re.I), "current_ratio"),
    (re.compile(r"速动比率|quick\s*ratio|速動比率", re.I), "quick_ratio"),
    (re.compile(r"拨备覆盖率|覆盖率|资本充足率|充足率", re.I), "provision_rate"),
    (re.compile(r"股息率|dividend\s*yield|派息率|payout\s*ratio", re.I), "dividend_rate"),
    (re.compile(r"占比|比重|proportion|percentage\s+of|share\s+of", re.I), "proportion"),
    (re.compile(r"税率|tax\s*rate|稅率", re.I), "tax_rate"),
    # 短模式放最后，用词边界/前缀排除防止子串误匹配
    (re.compile(r"(?<![票净毛])利率|interest\s*rate", re.I), "interest_rate"),
    # 注意：增长率/占比等非翻译一致性维度不列入角色模式，由排除模式过滤
]


def _extract_percentage_facts(block: _Block) -> list[_Fact]:
    facts: list[_Fact] = []
    for match in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%", block.text):
        # 检查前后文是否为非财务百分比（宽窗口用于排除判断）
        start_wide = max(0, match.start() - 40)
        end_wide = min(len(block.text), match.end() + 40)
        context_wide = block.text[start_wide:end_wide]

        # 角色匹配：对每个模式找其最后一次出现位置，然后选最近（最靠后）的匹配；
        # 当多个模式在同一位置附近匹配时，长模式（如"票面利率"）优先于短模式（如"利率"），
        # 通过列表顺序保证——长模式排在前面，短模式排在后面。
        context_before = block.text[max(0, match.start() - 60):match.start()]

        role = "percentage"
        best_end = -1  # 匹配的结束位置（越靠后越近）
        best_role_len = 0  # 同位置时，长模式优先
        for pattern, specific_role in _PERCENTAGE_ROLE_PATTERNS:
            # 找该模式在 context_before 中的最后一次匹配
            last_m = None
            for m in pattern.finditer(context_before):
                last_m = m
            if last_m is not None:
                end_pos = last_m.end()
                role_len = last_m.end() - last_m.start()
                # 选择结束位置最靠后的（最接近百分比），同位置时选更长的匹配
                if end_pos > best_end or (end_pos == best_end and role_len > best_role_len):
                    best_end = end_pos
                    best_role_len = role_len
                    role = specific_role

        # 排除检查：仅当角色仍为 generic "percentage" 时生效
        # 匹配了特定角色（coupon_rate、gross_margin 等）的百分比是可比较事实，不应排除
        if role == "percentage" and _PERCENTAGE_EXCLUDE_CONTEXT.search(context_wide):
            continue

        # 未匹配已知角色的百分比 → 按位置索引赋唯一角色，防止同段不同位置百分比错配
        if role == "percentage":
            role = f"percentage:{len(facts)}"

        facts.append(
            _Fact(
                kind="percentage",
                role=role,
                value=round(_to_number(match.group(1)), 4),
                raw=match.group(0).strip(),
                page=block.page,
                text=block.text,
                section=block.section,
                unit=None,
            )
        )
    return facts


# 中文期间描述模式（截至X年X月X日止年度等，应排除）
# 注意：需检查日期周围更广上下文（而非仅 raw），因为 raw 仅含日期本身
_PERIOD_DESC_PATTERN_ZH = re.compile(
    r"截至.*20\d{2}年.*(?:12月31日|止年度|年度)|"
    r"(?:截至|截至.*止).*20\d{2}年\d{1,2}月\d{1,2}日"
)
# 英文期间描述模式
_PERIOD_DESC_PATTERN_EN = re.compile(
    r"(?:for\s+the\s+(?:year|six\s+months|period|half[\s-]year)\s+ended|"
    r"as\s+at\s+(?:the\s+end\s+of\s+)?)",
    re.I,
)
# 关键日期上下文：成立/上市/并购/董事会/股东大会等中英文关键词
_SIGNIFICANT_DATE_CONTEXT = re.compile(
    r"成立|上市|并购|合并|重组|改制|设立|创办|"
    r"董事会|股东大会|会议|决议|批准|通过|审议|"
    r"签署|发布|披露|公告|"
    r"established|founded|listed|ipo|merger|acquisition|restructuring|"
    r"board|shareholders|meeting|approved|passed|adopted|"
    r"signed|published|disclosed|announced",
    re.I,
)


def _extract_date_facts(block: _Block) -> list[_Fact]:
    facts: list[_Fact] = []
    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    for match in re.finditer(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", block.text):
        raw = match.group(0)
        # 排除期间描述：检查日期前方的更广上下文（raw 仅含日期本身，不含"截至"前缀）
        ctx_before = max(0, match.start() - 40)
        period_window = block.text[ctx_before:match.end()]
        if _PERIOD_DESC_PATTERN_ZH.search(period_window):
            continue
        # 仅保留有重要上下文的日期（前后均检查：中文关键词常在日期之后，如"于[date]成立"）
        ctx_after = min(len(block.text), match.end() + 30)
        context_window = block.text[ctx_before:ctx_after]
        if not _SIGNIFICANT_DATE_CONTEXT.search(context_window):
            continue
        facts.append(_date_fact(block, raw, f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"))
    for match in re.finditer(
        r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})",
        block.text,
        flags=re.I,
    ):
        raw = match.group(0)
        ctx_before = max(0, match.start() - 80)
        period_window = block.text[ctx_before:match.end()]
        if _PERIOD_DESC_PATTERN_EN.search(period_window):
            continue
        ctx_after = min(len(block.text), match.end() + 20)
        context_window = block.text[ctx_before:ctx_after]
        if not _SIGNIFICANT_DATE_CONTEXT.search(context_window):
            continue
        facts.append(_date_fact(block, raw, f"{int(match.group(3)):04d}-{month_names[match.group(2).lower()]:02d}-{int(match.group(1)):02d}"))
    for match in re.finditer(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(20\d{2})",
        block.text,
        flags=re.I,
    ):
        raw = match.group(0)
        ctx_before = max(0, match.start() - 80)
        period_window = block.text[ctx_before:match.end()]
        if _PERIOD_DESC_PATTERN_EN.search(period_window):
            continue
        ctx_after = min(len(block.text), match.end() + 20)
        context_window = block.text[ctx_before:ctx_after]
        if not _SIGNIFICANT_DATE_CONTEXT.search(context_window):
            continue
        facts.append(_date_fact(block, raw, f"{int(match.group(3)):04d}-{month_names[match.group(1).lower()]:02d}-{int(match.group(2)):02d}"))
    return facts


def _date_fact(block: _Block, raw: str, value: str) -> _Fact:
    return _Fact(kind="date", role="date", value=value, raw=raw, page=block.page, text=block.text, section=block.section, unit=None)


# 日期上下文关键词按语义类别分组，用于判断两个日期是否来自相似上下文
_DATE_CONTEXT_CATEGORIES = {
    "corporate_events": frozenset({
        "成立", "上市", "并购", "合并", "重组", "改制", "设立", "创办",
        "established", "founded", "listed", "ipo", "merger", "acquisition", "restructuring",
    }),
    "governance": frozenset({
        "董事会", "股东大会", "会议", "决议", "批准", "通过", "审议",
        "board", "shareholders", "meeting", "approved", "passed", "adopted",
    }),
    "disclosure": frozenset({
        "签署", "发布", "披露", "公告",
        "signed", "published", "disclosed", "announced",
    }),
}



def _date_context_similar(zh_fact: _Fact, en_fact: _Fact) -> bool:
    """检查两个日期的事实是否来自相似的上下文（同一语义类别）。

    语义类别：
    - corporate_events: 成立/上市/并购/重组等企业事件
    - governance: 董事会/股东大会/决议/批准等治理事项
    - disclosure: 签署/发布/披露/公告等披露事项

    如果上下文属于不同类别（如成立日期 vs 批准日期），认为是不同事实，不报差异。
    此外增加日期周边关键词精确匹配：同一段落中不同日期往往伴随不同关键词
    （如"董事会…2021年3月30日" vs "自2021年1月1日起"），必须关键词也对应才允许配对。
    """
    zh_text = _normalize(zh_fact.text)
    en_text = _normalize(en_fact.text)
    # 提取日期周围的上下文
    zh_pos = zh_text.find(_normalize(zh_fact.raw))
    en_pos = en_text.find(_normalize(en_fact.raw))
    if zh_pos < 0:
        zh_pos = zh_text.find(zh_fact.raw.split()[0].lower()) if zh_fact.raw.split() else 0
    if en_pos < 0:
        en_pos = en_text.find(en_fact.raw.split()[0].lower()) if en_fact.raw.split() else 0
    zh_start = max(0, zh_pos - 80)
    zh_end = min(len(zh_text), zh_pos + len(zh_fact.raw) + 30)
    en_start = max(0, en_pos - 80)
    en_end = min(len(en_text), en_pos + len(en_fact.raw) + 30)
    zh_ctx = zh_text[zh_start:zh_end]
    en_ctx = en_text[en_start:en_end]

    # 第一层：按事件类别匹配
    category_matched = False
    for category_keywords in _DATE_CONTEXT_CATEGORIES.values():
        zh_has = any(kw in zh_ctx for kw in category_keywords)
        en_has = any(kw in en_ctx for kw in category_keywords)
        if zh_has and en_has:
            category_matched = True
            break
    if not category_matched:
        return False

    # 第二层：精确关键词配对（防止同段不同事项的日期交叉匹配）
    # 例如：中文"董事会…3月30日" vs 英文"Board…30 March"应匹配，
    # 但中文"自2021年1月1日起" vs 英文"30 March 2021"不应匹配
    zh_date_keywords = _extract_date_surrounding_keywords(zh_ctx, is_zh=True)
    en_date_keywords = _extract_date_surrounding_keywords(en_ctx, is_zh=False)
    if zh_date_keywords and en_date_keywords:
        if not (zh_date_keywords & en_date_keywords):
            return False

    return True


# 日期周边关键词映射：中英文对应的日期修饰词
_DATE_SURROUNDING_KEYWORD_MAP: list[tuple[frozenset[str], frozenset[str]]] = [
    # (中文关键词集合, 英文关键词集合)
    (frozenset({"董事会", "董事"}), frozenset({"board", "directors", "director"})),
    (frozenset({"股东", "股东大会", "股东周年"}), frozenset({"shareholders", "shareholder", "agm", "general meeting"})),
    (frozenset({"发行", "债券", "起"}), frozenset({"issue", "issuance", "bond", "from", "commencing"})),
    (frozenset({"上市", "挂牌"}), frozenset({"listing", "listed", "ipo"})),
    (frozenset({"批准", "通过", "决议"}), frozenset({"approved", "approval", "resolution", "resolved", "passed"})),
    (frozenset({"签署", "签订", "订立"}), frozenset({"signed", "executed", "entered into"})),
    (frozenset({"报告", "年度报告", "年报"}), frozenset({"report", "annual report"})),
    (frozenset({"成立", "注册"}), frozenset({"established", "incorporated", "registered", "founded"})),
    (frozenset({"截至", "止年度", "止"}), frozenset({"ended", "for the year", "as at", "as of"})),
    (frozenset({"派息", "分红", "股利", "利润分配"}), frozenset({"dividend", "distribution", "profit distribution"})),
]


def _extract_date_surrounding_keywords(ctx: str, *, is_zh: bool) -> frozenset[str]:
    """从日期周围上下文中提取规范化的关键词集合（统一使用英文关键词作为 canonical key）。"""
    matched: set[str] = set()
    for zh_kws, en_kws in _DATE_SURROUNDING_KEYWORD_MAP:
        source_kws = zh_kws if is_zh else en_kws
        # 始终使用英文关键词作为规范化集合，确保中英文可交集比对
        if any(kw in ctx for kw in source_kws):
            matched.update(en_kws)
    return frozenset(matched)


def _facts_by_role(facts: list[_Fact]) -> dict[tuple[str, str], list[_Fact]]:
    result: dict[tuple[str, str], list[_Fact]] = {}
    for fact in facts:
        result.setdefault((fact.kind, fact.role), []).append(fact)
    return result


def _fact_values_match(a_values: list[Any], h_values: list[Any]) -> bool:
    return all(any(_single_value_match(a, h) for h in h_values) for a in a_values) and all(
        any(_single_value_match(h, a) for a in a_values) for h in h_values
    )


def _is_unit_scale_compatible(zh_fact: _Fact, en_fact: _Fact, ratio: float) -> bool:
    """检查两侧 unit 字段是否能解释值比值。

    当中文用"千元"而英文用"thousand"（或一侧无单位声明），若 unit 乘数差
    与实际比值一致（5% 容差），则判定为单位换算差异而非真实错误。
    """
    zh_mult = _unit_multiplier(zh_fact.unit or "")
    en_mult = _unit_multiplier(en_fact.unit or "")
    # 两侧都有 unit 且不同 → 用乘数差解释比值
    if zh_mult != en_mult and zh_mult > 1.0 and en_mult > 1.0:
        expected_ratio = max(zh_mult, en_mult) / min(zh_mult, en_mult)
        if abs(ratio / expected_ratio - 1.0) <= 0.05:
            return True
    # 一侧有 unit，另一侧无 → 假设无 unit 侧乘数为 1，检查比值是否匹配有 unit 侧的乘数
    if zh_fact.unit and not en_fact.unit:
        expected_ratio = zh_mult
        if expected_ratio > 1.0 and abs(ratio / expected_ratio - 1.0) <= 0.05:
            return True
    if en_fact.unit and not zh_fact.unit:
        expected_ratio = en_mult
        if expected_ratio > 1.0 and abs(ratio / expected_ratio - 1.0) <= 0.05:
            return True
    return False


def _normalize_raw_for_comparison(raw: str) -> str:
    """归一化原始文本用于比较，去除RMB前缀、空格、逗号、大小写差异。"""
    return (raw or "").replace("RMB", "").replace(" ", "").replace(",", "").replace("元", "").lower().strip()


# 用于 LLM 输出校验的数值解析：从原始文本提取（归一化值、币种、单位）
_NUMERIC_VALUE_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def _parse_numeric_value(raw: str) -> tuple[float, str | None, str | None] | None:
    """从原始文本提取单个数值，返回（已按单位换算的值、币种、单位）。"""
    if not raw:
        return None
    m = _NUMERIC_VALUE_RE.search(raw.replace(",", ""))
    if not m:
        return None
    value = float(m.group().replace(",", ""))

    lower = raw.lower()
    unit: str | None = None
    for u, mult in (
        ("thousand", 1_000), ("thousands", 1_000),
        ("million", 1_000_000), ("millions", 1_000_000), ("mn", 1_000_000), ("mm", 1_000_000),
        ("billion", 1_000_000_000), ("billions", 1_000_000_000), ("bn", 1_000_000_000),
        ("千元", 1_000), ("万元", 10_000), ("百万元", 1_000_000), ("百万", 1_000_000),
        ("亿元", 100_000_000), ("万亿元", 1_000_000_000_000),
    ):
        if u in lower:
            unit = u
            value *= mult
            break

    currency = None
    for c, canon in (
        ("人民币", "CNY"), ("rmb", "CNY"), ("cny", "CNY"), ("cnh", "CNY"), ("¥", "CNY"), ("￥", "CNY"),
        ("港币", "HKD"), ("港元", "HKD"), ("hkd", "HKD"), ("hk$", "HKD"),
        ("美元", "USD"), ("usd", "USD"), ("us$", "USD"),
    ):
        if c in lower:
            currency = canon
            break

    return value, currency, unit


def _numeric_values_match(
    zh_parsed: tuple[float, str | None, str | None],
    en_parsed: tuple[float, str | None, str | None],
) -> bool:
    """判断两个解析后的数值是否实质相同，支持跨币种换算。"""
    zh_val, zh_cur, _ = zh_parsed
    en_val, en_cur, _ = en_parsed

    if zh_cur and en_cur and zh_cur != en_cur:
        zh_fx = _fx_to_hkd(zh_cur)
        en_fx = _fx_to_hkd(en_cur)
        if zh_fx is not None and en_fx is not None:
            zh_val *= zh_fx
            en_val *= en_fx

    base = max(abs(zh_val), abs(en_val), 1.0)
    return abs(zh_val - en_val) / base <= 0.0001


def _single_value_match(
    a: Any,
    h: Any,
    zh_raw: str = "",
    en_raw: str = "",
    zh_currency: str | None = None,
    en_currency: str | None = None,
) -> bool:
    if isinstance(a, (int, float)) and isinstance(h, (int, float)):
        # 跨币种分支：H 股中文版常以人民币披露、英文版常以港币披露，
        # 需换算到同一币种（HKD）后比较，容差放宽至配置值（汇率波动 + 披露日不同）
        if zh_currency and en_currency and zh_currency != en_currency:
            zh_fx = _fx_to_hkd(zh_currency)
            en_fx = _fx_to_hkd(en_currency)
            if zh_fx is not None and en_fx is not None:
                a_hkd = float(a) * zh_fx
                h_hkd = float(h) * en_fx
                base = max(abs(a_hkd), abs(h_hkd), 1.0)
                if abs(a_hkd - h_hkd) / base <= settings.bilingual_cross_currency_tolerance:
                    return True
                return False
            # 任一币种无汇率配置：回退到下方精确比较（不做币种换算）
        base = max(abs(float(a)), abs(float(h)), 1.0)
        if abs(float(a) - float(h)) / base <= 0.0001:
            return True
        # 后备：归一化原始文本比较（处理 RMB 前缀、空格差异）
        # 注：不再使用量级因子容忍（1K/1万/1M/1亿）——金额提取时已按 _unit_multiplier
        # 归一化，归一化后的值应直接精确比较；因子容忍会把"X千元"与"RMB X"误判匹配，导致真实差异漏检
        if zh_raw and en_raw:
            if _normalize_raw_for_comparison(zh_raw) == _normalize_raw_for_comparison(en_raw):
                return True
        return False
    # 字符串类型：归一化后比较
    if zh_raw and en_raw:
        if _normalize_raw_for_comparison(zh_raw) == _normalize_raw_for_comparison(en_raw):
            return True
    return a == h


def _fact_context_compatible(
    zh_fact: _Fact,
    en_fact: _Fact,
    *,
    zh_total_same_role: int = 1,
    en_total_same_role: int = 1,
) -> bool:
    """检查两个事实的局部上下文是否有术语交集。

    同段不同事项的金额/日期往往围绕不同术语（如 "诉讼本金" vs "涉诉总额"），
    若局部上下文无 glossary 术语交集，说明不是对应事实，不应配对。
    当两个事实来自同一段落（text 相同）时，使用 raw 周围的局部上下文。

    当段落中存在多个同角色事实时（zh_total_same_role/en_total_same_role≥2），
    要求术语交集中必须包含区分性术语，仅靠通用术语不足以确认配对。
    """
    # 百分比专项：位置角色必须完全一致才允许配对，不同角色（roe vs gross_margin）不配对
    if zh_fact.kind == "percentage" and en_fact.kind == "percentage":
        # 位置角色（percentage:N）→ 只有相同位置才配对
        if zh_fact.role.startswith("percentage:") and en_fact.role.startswith("percentage:"):
            if zh_fact.role != en_fact.role:
                return False
            # 即使位置角色相同，也必须检查周边文本的角色关键词是否一致
            # 防止中文第1个百分比（如债券利率）被配对到英文第1个百分比（如增长率）
            if not _percentage_role_keywords_compatible(zh_fact, en_fact):
                return False
        # 特定角色（roe, gross_margin 等）→ 只有相同角色才配对
        elif zh_fact.role != en_fact.role:
            return False
        # 无位置角色时，检查周边文本的角色关键词是否一致
        elif not _percentage_role_keywords_compatible(zh_fact, en_fact):
            return False

    zh_text = _normalize(zh_fact.text)
    en_text = _normalize(en_fact.text)
    zh_raw_norm = _normalize(zh_fact.raw)
    en_raw_norm = _normalize(en_fact.raw)
    zh_pos = zh_text.find(zh_raw_norm)
    en_pos = en_text.find(en_raw_norm)
    if zh_pos < 0:
        zh_pos = 0
    if en_pos < 0:
        en_pos = 0

    # 数值位置偏移检查：同段中两个 amount 类事实的相对位置应大致对应
    # 若一侧排在前面、另一侧排在后面 → 不是同一事项，不应配对
    if zh_fact.kind == "amount" and zh_total_same_role >= 2 and en_total_same_role >= 2:
        zh_text_len = max(len(zh_text), 1)
        en_text_len = max(len(en_text), 1)
        zh_rel_pos = zh_pos / zh_text_len
        en_rel_pos = en_pos / en_text_len
        if abs(zh_rel_pos - en_rel_pos) > 0.40:
            return False

    # 取事实前后各 60 字符的局部上下文
    zh_start = max(0, zh_pos - 60)
    zh_end = min(len(zh_text), zh_pos + len(zh_raw_norm) + 60)
    en_start = max(0, en_pos - 60)
    en_end = min(len(en_text), en_pos + len(en_raw_norm) + 60)
    zh_ctx = zh_text[zh_start:zh_end]
    en_ctx = en_text[en_start:en_end]
    zh_keys = _glossary_keys_from_text(zh_ctx)
    en_keys = _glossary_keys_from_text(en_ctx)
    common_keys = zh_keys & en_keys
    if zh_keys and en_keys:
        # 多事实段落（两侧均有≥2条同角色事实）：对 amount 要求区分性术语交集
        # 日期不在此限——同段不同日期靠 _date_context_similar 的语义类别+关键词区分
        if (zh_total_same_role >= 2 and en_total_same_role >= 2
                and zh_fact.kind == "amount"):
            if not common_keys:
                return False
            if not _has_distinguishing_terms(common_keys):
                return False
            return True
        return bool(common_keys)
    # 都无法提取术语时退回允许配对（避免过度过滤）
    return True


# 区分性术语集合：能用来区分同一段落中不同数字的具体业务术语
_DISTINGUISHING_TERMS: set[str] = {
    "share_capital", "shares", "share_changes", "shareholders",  # 股本/股份相关
    "dividend", "profit_distribution", "eps",                    # 分红/每股收益相关
    "retained_earnings", "capital_reserve",                       # 留存收益/资本公积
    "revenue", "operating_income", "gross_profit", "net_profit", # 收入/利润相关
    "total_assets", "total_liabilities", "total_equity",         # 主要资产负债表科目
    "current_assets", "current_liabilities", "non_current_assets",
    "ppe", "intangible_assets", "investment_property",
    "cogs", "selling_expenses", "admin_expenses", "finance_expenses",
    "bonds", "provisions", "leases", "income_tax",
    "borrowings", "loans", "deposits",                           # 借贷相关
    "interest_income", "interest_expense", "fee_income",         # 利息/手续费
    "cash_dividend", "bonus_share",                               # 现金分红/送股
    "share_capital_count", "dividend_amount",                     # 股数/分红金额区分
}


def _has_distinguishing_terms(common_keys: set[str]) -> bool:
    """检查术语交集中是否包含能区分不同事项的具体业务术语。"""
    return len(common_keys & _DISTINGUISHING_TERMS) >= 1


# 百分比角色关键词：用于区分同一段落中不同用途的百分比
_PERCENTAGE_ROLE_KEYWORDS_ZH = {
    "proportion": frozenset({"比例", "占比", "比率", "占", "比重", "百分比"}),
    "rate": frozenset({"利率", "票面利率", "收益率", "费率", "利息率", "年利率", "月利率"}),
    "tax_rate": frozenset({"税率", "所得税率", "增值税率", "实际税率"}),
    "growth": frozenset({"增长", "同比增长", "环比增长", "变动", "上升", "下降", "减少", "增加", "增幅", "降幅"}),
    "ratio": frozenset({"每股", "每10股", "市盈率", "市净率", "roe", "roa"}),
    "margin": frozenset({"毛利率", "净利率", "利润率", "营业利润率"}),
    "coverage": frozenset({"覆盖率", "拨备覆盖率", "资本充足率"}),
    "return_rate": frozenset({"回报率", "净资产收益率", "总资产收益率"}),
}

_PERCENTAGE_ROLE_KEYWORDS_EN = {
    "proportion": frozenset({"proportion", "percentage", "ratio of", "as a percentage", "as % of", "percent of"}),
    "rate": frozenset({"interest rate", "coupon rate", "yield", "fee rate", "rate of", "per annum"}),
    "tax_rate": frozenset({"tax rate", "income tax rate", "effective tax rate"}),
    "growth": frozenset({"growth", "increase", "decrease", "change", "rose", "fell", "decline", "up", "down"}),
    "ratio": frozenset({"per share", "per 10 shares", "pe ratio", "pb ratio", "eps", "roe", "roa"}),
    "margin": frozenset({"gross margin", "net margin", "profit margin", "operating margin"}),
    "coverage": frozenset({"coverage ratio", "capital adequacy", "provision coverage"}),
    "return_rate": frozenset({"return on", "yield on", "rate of return"}),
}


def _percentage_role_keywords_compatible(zh_fact: _Fact, en_fact: _Fact) -> bool:
    """检查两个百分比事实的周边文本是否暗示相同的百分比用途。

    对于位置角色（percentage:N），如果任一侧无法分类上下文，拒绝配对
    （位置角色基于提取顺序而非语义，跨语言极易错配）。
    """
    zh_text = _normalize(zh_fact.text)
    en_text = _normalize(en_fact.text)
    zh_raw_norm = _normalize(zh_fact.raw)
    en_raw_norm = _normalize(en_fact.raw)
    zh_pos = zh_text.find(zh_raw_norm)
    en_pos = en_text.find(en_raw_norm)
    if zh_pos < 0:
        zh_pos = 0
    if en_pos < 0:
        en_pos = 0
    zh_ctx = zh_text[max(0, zh_pos - 40):min(len(zh_text), zh_pos + len(zh_raw_norm) + 40)]
    en_ctx = en_text[max(0, en_pos - 40):min(len(en_text), en_pos + len(en_raw_norm) + 40)]
    zh_category = _classify_percentage_context(zh_ctx, is_zh=True)
    en_category = _classify_percentage_context(en_ctx, is_zh=False)

    # 若两侧都能分类到不同类别 → 不兼容
    if zh_category and en_category and zh_category != en_category:
        return False

    # 位置角色（percentage:N）：必须两侧都能分类且类别一致才允许配对
    # 位置角色基于提取顺序，跨语言极易错配不同的百分比指标
    zh_is_positional = zh_fact.role.startswith("percentage:")
    en_is_positional = en_fact.role.startswith("percentage:")
    if zh_is_positional or en_is_positional:
        if not zh_category or not en_category:
            return False
        return zh_category == en_category

    return True


def _classify_percentage_context(ctx: str, *, is_zh: bool) -> str | None:
    """根据周边文本将百分比归类到语义类别。"""
    keywords_map = _PERCENTAGE_ROLE_KEYWORDS_ZH if is_zh else _PERCENTAGE_ROLE_KEYWORDS_EN
    for category, keywords in keywords_map.items():
        if any(kw in ctx for kw in keywords):
            return category
    return None


def _fact_match_score(
    zh_fact: _Fact,
    en_fact: _Fact,
    *,
    zh_total_same_role: int = 1,
    en_total_same_role: int = 1,
) -> float:
    """返回两个事实的匹配分数（越小越好）。值相同=0，值接近=小正数，不匹配=inf。"""
    if zh_fact.kind != en_fact.kind or zh_fact.role != en_fact.role:
        return float("inf")
    # 上下文兼容性：同段不同事项的事实（不同日期/金额/比例）不应配对
    if zh_fact.kind in ("date", "amount", "percentage"):
        if not _fact_context_compatible(
            zh_fact, en_fact,
            zh_total_same_role=zh_total_same_role,
            en_total_same_role=en_total_same_role,
        ):
            return float("inf")
    if _single_value_match(
        zh_fact.value, en_fact.value, zh_fact.raw, en_fact.raw, zh_fact.currency, en_fact.currency
    ):
        return 0.0
    if isinstance(zh_fact.value, (int, float)) and isinstance(en_fact.value, (int, float)):
        a, h = float(zh_fact.value), float(en_fact.value)
        if a == 0 or h == 0:
            return abs(a - h)
        ratio = max(a, h) / min(a, h)
        for factor in (1_000, 10_000, 1_000_000, 100_000_000, 1_000_000_000):
            if abs(ratio - factor) / factor <= 0.05:
                return factor / 1_000_000  # 小惩罚
        return ratio  # 大惩罚
    # 字符串类型（日期等）：给一个有限的大惩罚，确保能被配对后检查值是否匹配
    return 1_000_000.0


def _optimal_fact_pairs(zh_facts: list[_Fact], en_facts: list[_Fact]) -> list[tuple[_Fact, _Fact]]:
    """对同一 role 的中英文事实做全局最优配对：先计算所有候选对的 score，按 score 升序全局分配，
    确保 score=0（完全匹配）的对优先被选中，避免贪心顺序导致的错配。"""
    zh_n = len(zh_facts)
    en_n = len(en_facts)
    candidates: list[tuple[float, int, int]] = []
    for i, zh_fact in enumerate(zh_facts):
        for j, en_fact in enumerate(en_facts):
            score = _fact_match_score(
                zh_fact, en_fact,
                zh_total_same_role=zh_n,
                en_total_same_role=en_n,
            )
            if score < float("inf"):
                candidates.append((score, i, j))
    candidates.sort(key=lambda x: x[0])

    used_zh: set[int] = set()
    used_en: set[int] = set()
    pairs: list[tuple[_Fact, _Fact]] = []
    for score, i, j in candidates:
        if i in used_zh or j in used_en:
            continue
        used_zh.add(i)
        used_en.add(j)
        pairs.append((zh_facts[i], en_facts[j]))
    return pairs


def _date_pair_low_confidence(
    zh_fact: _Fact,
    en_fact: _Fact,
    zh_values: list[_Fact],
    en_values: list[_Fact],
) -> bool:
    if _single_value_match(zh_fact.value, en_fact.value, zh_fact.raw, en_fact.raw):
        return False
    if len(zh_values) > 1 or len(en_values) > 1:
        return True
    zh_date_mentions = _all_date_mentions(zh_fact.text)
    en_date_mentions = _all_date_mentions(en_fact.text)
    if len(zh_date_mentions) > len(zh_values) or len(en_date_mentions) > len(en_values):
        return True
    return False


def _all_date_mentions(text: str) -> list[str]:
    mentions: list[str] = []
    for pattern in _DATE_MENTION_PATTERNS:
        mentions.extend(match.group(0) for match in pattern.finditer(text or ""))
    return mentions


def _is_significant_unmatched_fact(fact: _Fact) -> bool:
    """未匹配的事实只有在足够大/重要时才报差异。"""
    if fact.kind == "amount" and isinstance(fact.value, (int, float)):
        return abs(fact.value) >= 100_000
    if fact.kind == "percentage":
        return abs(fact.value) >= 1.0  # 1%以上才报
    if fact.kind == "date":
        return True
    return False


def _delta(a: Any, h: Any) -> float | None:
    if isinstance(a, (int, float)) and isinstance(h, (int, float)):
        return round(float(h) - float(a), 4)
    return None


def _unit_multiplier(unit: str) -> float:
    normalized = (unit or "").strip().lower()
    # 去掉"人民币"前缀以归一化组合单位
    normalized = re.sub(r"^人民币", "", normalized)
    return {
        "": 1.0,
        "元": 1.0,
        "千元": 1_000.0,
        "万元": 10_000.0,
        "百万元": 1_000_000.0,
        "百万": 1_000_000.0,
        "亿元": 100_000_000.0,
        "万亿元": 1_000_000_000_000.0,
        # 组合单位（带"人民币"前缀已去除，但保留原始形式以防遗漏）
        "人民币元": 1.0,
        "人民币千元": 1_000.0,
        "人民币万元": 10_000.0,
        "人民币百万元": 1_000_000.0,
        "人民币亿元": 100_000_000.0,
        # 英文组合单位
        "thousand": 1_000.0,
        "thousands": 1_000.0,
        "million": 1_000_000.0,
        "millions": 1_000_000.0,
        "billion": 1_000_000_000.0,
        "billions": 1_000_000_000.0,
        "rmb thousand": 1_000.0,
        "rmb thousands": 1_000.0,
        "rmb million": 1_000_000.0,
        "rmb millions": 1_000_000.0,
        "rmb billion": 1_000_000_000.0,
        "rmb billions": 1_000_000_000.0,
        # 缩写
        "k": 1_000.0,
        "m": 1_000_000.0,
        "mm": 1_000_000.0,
        "mn": 1_000_000.0,
        "b": 1_000_000_000.0,
        "bn": 1_000_000_000.0,
    }.get(normalized, 1.0)


# 币种归一化：H 股中文版常用人民币、英文版常用港币，金额比对前需识别币种
_CURRENCY_ALIASES = {
    "rmb": "CNY", "cny": "CNY", "cnh": "CNY", "人民币": "CNY", "¥": "CNY", "￥": "CNY",
    "hkd": "HKD", "港币": "HKD", "港元": "HKD", "hk$": "HKD",
    "usd": "USD", "美元": "USD", "us$": "USD",
}


def _normalize_currency(raw: str) -> str | None:
    """将原始币种文本归一化为 CNY/HKD/USD，无法识别返回 None。"""
    if not raw:
        return None
    stripped = raw.strip()
    return _CURRENCY_ALIASES.get(stripped.lower()) or _CURRENCY_ALIASES.get(stripped)


def _fx_to_hkd(currency: str | None) -> float | None:
    """1 单位该币种换算到 HKD 的因子；未知币种返回 None。"""
    if currency == "CNY":
        return settings.fx_cny_to_hkd
    if currency == "USD":
        return settings.fx_usd_to_hkd
    if currency == "HKD":
        return 1.0
    return None


def _demote_severity(severity: DiffSeverity) -> DiffSeverity:
    """跨币种差异因汇率不确定性，严重度降一级。"""
    order = [
        DiffSeverity.CRITICAL,
        DiffSeverity.HIGH,
        DiffSeverity.MEDIUM,
        DiffSeverity.LOW,
        DiffSeverity.INFO,
    ]
    try:
        idx = order.index(severity)
    except ValueError:
        return severity
    return order[min(idx + 1, len(order) - 1)]


def _to_number(value: str) -> float:
    return float((value or "0").replace(",", ""))


def _per_10_share_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (
        re.compile(r"每\s*10\s*股.{0,28}?(?:人民币|港币|港元|美元)?\s*(\d+(?:\.\d+)?)\s*元"),
        re.compile(r"(?:RMB|HKD|USD)\s*(\d+(?:\.\d+)?)\s*(?:\([^)]+\))?\s*per\s*10\s*shares", re.I),
        re.compile(r"(?:RMB|HKD|USD)\s*(\d+(?:\.\d+)?)\s*per\s*10\s*shares", re.I),
    ):
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def _span_overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)


def _span_contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    """检查 inner span 是否完全被 outer span 包含（允许边界相等）。"""
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@lru_cache(maxsize=50000)
def _normalize(text: str) -> str:
    return to_simplified(_clean_text(text)).lower()


@lru_cache(maxsize=10000)
def _glossary_keys_from_text(text: str) -> set[str]:
    """从文本中提取所有匹配的 glossary canonical_key，用于跨语言语义匹配。"""
    normalized = _normalize(text)
    keys: set[str] = set()
    for form, canonical in glossary._to_canonical.items():
        if len(form) >= 3 and form in normalized:
            keys.add(canonical)
    return keys


@lru_cache(maxsize=5000)
def _semantic_similarity(zh_text: str, en_text: str) -> float:
    """基于 glossary 覆盖率的轻量跨语言语义相似度（0.0~1.0）。"""
    zh_keys = _glossary_keys_from_text(zh_text)
    en_keys = _glossary_keys_from_text(en_text)
    if not zh_keys and not en_keys:
        return 0.0
    intersection = zh_keys & en_keys
    union = zh_keys | en_keys
    return len(intersection) / len(union) if union else 0.0


_TOPIC_TERMS = (
    (("利润分配", "股利", "分红", "现金红利"), ("profit distribution", "dividend", "dividends")),
    (("债券", "票面利率", "赎回", "发行"), ("bond", "bonds", "coupon", "redemption", "issued")),
    (("董事会", "批准", "审议"), ("board", "approved", "approval")),
    (("股本", "股份", "每10股"), ("share capital", "shares", "per 10 shares")),
)

_ACTION_TERMS = (
    (("赎回", "偿还", "兑付"), ("redemption", "redeem", "redeemed", "repayment", "repaid")),
    (("发行", "发售"), ("issued", "issuance", "offering")),
    (("批准", "审议", "通过"), ("approved", "approval", "considered", "passed")),
    (("完成", "已经"), ("completed", "has completed", "have completed")),
    (("计划", "建议", "拟"), ("plans to", "proposed", "proposes", "intends to")),
)

_SECTION_KEYWORDS = (
    ("business", ("业务概览", "业务回顾", "经营情况", "经营概况"), ("business overview", "business review", "operations review")),
    ("governance", ("公司治理", "董事会", "监事会", "企业管治"), ("corporate governance", "board", "supervisory committee")),
    ("notes", ("财务报表附注", "附注", "期后事项"), ("notes to the financial statements", "notes", "subsequent events")),
    ("mda", ("管理层讨论", "管理层分析", "管理层讨论与分析"), ("management discussion", "mda", "management discussion and analysis")),
    ("risk", ("风险管理", "风险因素"), ("risk management", "risk factors")),
    ("audit", ("审计报告", "独立审计师报告", "核数师报告"), ("auditor's report", "independent auditor's report", "audit report")),
    ("directors", ("董事", "董事及高级管理人员", "董事监事"), ("directors", "senior management", "directors and senior management")),
    ("financial_statements", ("财务报表", "合并财务报表", "综合财务报表"), ("financial statements", "consolidated financial statements", "consolidated statement")),
    ("balance_sheet", ("资产负债表", "合并资产负债表", "综合资产负债表"), ("balance sheet", "statement of financial position", "consolidated balance sheet")),
    ("income_statement", ("利润表", "合并利润表", "综合收益表"), ("income statement", "profit or loss", "statement of profit or loss", "consolidated income statement")),
    ("cash_flow", ("现金流量表", "合并现金流量表"), ("cash flow statement", "statement of cash flows", "consolidated cash flow statement")),
    ("equity_statement", ("所有者权益变动表", "股东权益变动表", "权益变动表"), ("statement of changes in equity", "statement of changes in shareholders' equity")),
    ("related_party", ("关联交易", "关联方交易", "关连交易"), ("related party transactions", "related party", "connected transactions")),
    ("shareholders", ("股东", "股东信息", "股本结构"), ("shareholders", "share capital", "shareholder information")),
    ("dividend", ("利润分配", "股利分配", "分红"), ("dividend", "profit distribution", "dividend distribution")),
    ("commitments", ("承诺", "或有事项", "承诺及或有事项"), ("commitments", "contingencies", "commitments and contingencies")),
    ("share_capital", ("股本", "股份", "注册资本", "股本结构"), ("share capital", "registered capital", "authorized capital", "issued capital", "capital structure")),
    ("share_changes", ("股本变动", "股份变动", "股本变动情况"), ("changes in share capital", "share capital changes", "movements in share capital")),
    ("company_profile", ("公司简介", "公司概况", "公司信息"), ("company profile", "company information", "about the company")),
    ("long_term_investments", ("长期股权投资", "长期股权投资明细"), ("long-term equity investments", "long term investments")),
    ("revenue", ("营业收入", "收入", "主营业务收入"), ("revenue", "operating revenue", "income")),
    ("margin_loans", ("融资融券", "融出资金"), ("margin trading", "securities lending", "margin loans")),
    ("leases", ("租赁", "使用权资产"), ("leases", "right-of-use assets", "lease liabilities")),
    ("bonds", ("债券", "应付债券"), ("bonds", "debentures", "notes payable")),
    ("asset_management", ("资产管理", "受托资产管理"), ("asset management", "entrusted asset management")),
    # 新增：覆盖 parser 常见 section code
    ("significant_events", ("重要事项", "重大事项", "重要事件"), ("significant events", "significant matters", "material events")),
    ("accounting_policy", ("会计政策", "重要会计政策"), ("accounting policies", "significant accounting policies")),
    ("accounting_estimate", ("会计估计", "重要会计估计"), ("accounting estimates", "significant accounting estimates")),
    ("esg", ("环境与社会", "可持续发展", "ESG报告"), ("environmental, social", "sustainability", "esg report")),
    ("segment_report", ("分部报告", "经营分部"), ("segment reporting", "operating segments")),
    ("financial_instruments", ("金融工具", "金融资产"), ("financial instruments", "financial assets")),
    ("income_tax", ("所得税", "所得税费用"), ("income tax", "income tax expense")),
    ("eps", ("每股盈利", "每股收益", "每股利润"), ("earnings per share", "eps")),
    ("ppe", ("物业厂房及设备", "固定资产", "物业、厂房及设备"), ("property plant and equipment", "ppe", "fixed assets")),
    ("employee_benefits", ("雇员福利", "职工薪酬", "员工福利"), ("employee benefits", "employee compensation")),
    ("provisions", ("拨备", "预计负债"), ("provisions", "provision for liabilities")),
    ("inventories", ("存货", "库存"), ("inventories", "stocks")),
    ("intangible_assets", ("无形资产", "商誉及无形资产"), ("intangible assets", "intangibles")),
    ("construction_in_progress", ("在建工程", "在建项目"), ("construction in progress",)),
    ("investment_property", ("投资性房地产", "投资物业"), ("investment property", "investment properties")),
    ("capital_reserve", ("资本公积", "股份溢价"), ("capital reserve", "share premium")),
    ("retained_earnings", ("未分配利润", "保留溢利"), ("retained earnings", "retained profits")),
    ("minority_interest", ("少数股东权益", "非控制性权益"), ("minority interest", "non-controlling interests")),
)

# Parser section code → canonical key 直接映射
# Parser (pdf_h_html.py) 使用下划线 code (如 "corporate_governance")，
# _SECTION_KEYWORDS 使用空格 (如 "corporate governance")，子串匹配失败。
# 此映射表在 _section_key() 中优先查表，确保 parser code 正确归一化。
_PARSER_CODE_TO_KEY: dict[str, str] = {
    # 公司治理
    "corporate_governance": "governance",
    "directors_report": "directors",
    "significant_events": "significant_events",
    # 会计与附注
    "accounting_policy": "accounting_policy",
    "accounting_estimate": "accounting_estimate",
    "notes": "notes",
    "financial_statements": "financial_statements",
    # 业务与战略
    "business": "business",
    "mda": "mda",
    "company_profile": "company_profile",
    # 财务报表核心
    "bs": "balance_sheet",
    "pl": "income_statement",
    "cf": "cash_flow",
    "equity": "equity_statement",
    # 财务报表附注
    "financial_instruments": "financial_instruments",
    "income_tax": "income_tax",
    "eps": "eps",
    "segment_report": "segment_report",
    "related_party": "related_party",
    "leases": "leases",
    "revenue": "revenue",
    "ppe": "ppe",
    "employee_benefits": "employee_benefits",
    "provisions": "provisions",
    "inventories": "inventories",
    "intangible_assets": "intangible_assets",
    "construction_in_progress": "construction_in_progress",
    "investment_property": "investment_property",
    "long_term_investment": "long_term_investments",
    "goodwill": "intangible_assets",
    "rnd": "revenue",
    "government_grant": "revenue",
    "preference_shares": "share_capital",
    "perpetual_bond": "bonds",
    # 股本与权益
    "share_changes": "share_changes",
    "share_capital": "share_capital",
    "capital_reserve": "capital_reserve",
    "retained_earnings": "retained_earnings",
    "minority_interest": "minority_interest",
    # ESG
    "esg": "esg",
    # 券商特色
    "margin_loans": "margin_loans",
    "asset_management": "asset_management",
    "bonds": "bonds",
    # 审计与风险
    "audit": "audit",
    "risk": "risk",
    # 损益表子项
    "cogs": "income_statement",
    "selling_expenses": "income_statement",
    "admin_expenses": "income_statement",
    "finance_expenses": "income_statement",
    "net_profit": "income_statement",
    "net_profit_attributable": "income_statement",
    "basic_eps": "eps",
    "diluted_eps": "eps",
    # 现金流量表子项
    "cfo": "cash_flow",
    "cfi": "cash_flow",
    "cff": "cash_flow",
    "cash_equivalents": "cash_flow",
    # 资产负债表子项
    "receivables": "balance_sheet",
    "prepayments": "balance_sheet",
    "other_receivables": "balance_sheet",
    "current_assets": "balance_sheet",
    "non_current_assets": "balance_sheet",
    "total_assets": "balance_sheet",
    "short_term_borrowings": "balance_sheet",
    "payables": "balance_sheet",
    "contract_liabilities": "balance_sheet",
    "other_payables": "balance_sheet",
    "current_liabilities": "balance_sheet",
    "non_current_liabilities": "balance_sheet",
    "total_liabilities": "balance_sheet",
    "total_equity": "balance_sheet",
    "oci": "equity_statement",
}

_FACT_LABELS = {
    ("amount", "amount"): "金额",
    ("amount", "amount:revenue"): "收入/利润分配金额",
    ("amount", "amount:other"): "其他金额",
    ("amount", "dividend_rate_per_10_shares"): "每10股派息",
    ("percentage", "percentage"): "比例",
    ("date", "date"): "日期",
    ("date", ""): "日期",
    ("share_count", "share_count"): "股份数量",
}
