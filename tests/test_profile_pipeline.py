from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

from ahcc.check.branch_disclosure import compare_branch_tables
from ahcc.check.coverage import (
    _amount_mentions,
    _normalize_text,
    build_disclosure_coverage,
    run_event_checks_on_profiles,
)
from ahcc.check.disclosure import run_disclosure_checks_on_profiles
from ahcc.check.numeric import run_numeric_checks_on_profiles
from ahcc.check import key_metric_tamper
from ahcc.check.key_metric_tamper import _candidate_ocr_pages, run_key_metric_tamper_checks
from ahcc.check.standard import run_standard_checks, run_standard_checks_on_profiles
from ahcc.config import settings
from ahcc.profile import build_profile, summarize_profile
from ahcc.profile.compare import compare_metrics, compare_profiles
from ahcc.profile.extract_metrics import _extract_from_text, extract_metrics
from ahcc.profile.extract_narratives import extract_narratives
from ahcc.profile.extract_structure import extract_structure
from ahcc.profile.models import ChapterNode, InternalInconsistency, MetricItem, MetricOccurrences, NarrativeBlock, ReportProfile
from ahcc.schemas import (
    Currency,
    AlignedPair,
    DataPoint,
    Diff,
    DiffScope,
    DiffSeverity,
    DiffType,
    Evidence,
    ExtractionAudit,
    FinancialTable,
    Job,
    Language,
    LocalizedString,
    ReportDocument,
    ReportSide,
    TableCell,
    TextSegment,
)


def _evidence(side: ReportSide, page: int, snippet: str = "snippet") -> Evidence:
    return Evidence(side=side, page=page, bbox=(0.0, 0.0, 1.0, 1.0), snippet=snippet, section="bs")


def _metric(
    key: str,
    value: float,
    side: ReportSide,
    page: int = 1,
    unit: str = "人民币元",
    confidence: float = 0.9,
    section: str = "bs",
    snippet: str | None = None,
    source: str = "table",
    period: str | None = None,
) -> MetricItem:
    return MetricItem(
        canonical_key=key,
        name=LocalizedString(zh=key, en=key),
        value=value,
        value_text=str(value),
        unit=unit,
        currency=Currency.CNY,
        period=period,
        page=page,
        evidence=Evidence(
            side=side,
            page=page,
            bbox=(0.0, 0.0, 1.0, 1.0),
            snippet=snippet or f"{key}: {value}",
            section=section,
        ),
        confidence=confidence,
        source=source,
    )


def _occ(item: MetricItem) -> MetricOccurrences:
    return MetricOccurrences(
        canonical_key=item.canonical_key,
        name=item.name,
        primary=item,
        all_occurrences=[item],
    )


def _occ_many(primary: MetricItem, *items: MetricItem) -> MetricOccurrences:
    return MetricOccurrences(
        canonical_key=primary.canonical_key,
        name=primary.name,
        primary=primary,
        all_occurrences=[primary, *items],
    )


def _profile(side: ReportSide, metrics=None, narratives=None, structure=None) -> ReportProfile:
    return ReportProfile(
        doc_id=f"{side.value}-doc",
        side=side,
        total_pages=10,
        metrics=metrics or [],
        narratives=narratives or [],
        structure=structure or [],
        metadata={},
    )


def _profiles_with_event_texts(a_text: str, h_text: str, section: str = "notes") -> tuple[ReportProfile, ReportProfile]:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=100,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-structured-event",
                page=30,
                bbox=(0, 0, 1, 1),
                text=a_text,
                language=Language.ZH,
                section=section,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=100,
        primary_language=Language.EN,
        texts=[
            TextSegment(
                segment_id="h-structured-event",
                page=80,
                bbox=(0, 0, 1, 1),
                text=h_text,
                language=Language.EN,
                section=section,
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc
    return profile_a, profile_h


def test_extract_metrics_keeps_all_table_occurrences() -> None:
    table = FinancialTable(
        table_id="A_p001_t01",
        title=LocalizedString(zh="合并资产负债表"),
        page=1,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="人民币元",
        currency=Currency.CNY,
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="资产总计"),
            TableCell(row=1, col=1, text="1,000"),
            TableCell(row=2, col=0, text="负债合计"),
            TableCell(row=2, col=1, text="500"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=1,
        primary_language=Language.ZH,
        tables=[table],
        texts=[],
        metadata={"unit": "人民币元", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    keys = {m.canonical_key for m in metrics}
    assert "total_assets" in keys
    assert "total_liabilities" in keys
    assert sum(len(m.all_occurrences) for m in metrics) >= 2


def test_glossary_maps_sample_reference_error_labels() -> None:
    from ahcc.align.glossary import glossary

    expected = {
        "归属净利润": "net_profit_attributable",
        "归属于上市公司股东的净利润": "net_profit_attributable",
        "归属本行股东净利润": "net_profit_attributable",
        "不良贷款率": "non_performing_loan_ratio",
        "经营活动现金流净额": "operating_cash_flow",
        "税金及附加": "taxes_and_surcharges",
        "加权平均ROE": "weighted_average_roe",
        "货币资金合计": "cash_equivalents",
        "应收账款账面价值合计": "receivables",
        "存货账面价值合计": "inventory",
        "税金及附加合计": "taxes_and_surcharges",
        "现金及现金等价物年末余额": "cash_equivalents_end",
        "年末现金及现金等价物": "cash_equivalents_end",
        "归属净资产": "parent_equity",
        "金融投资合计": "financial_investments",
        "发放贷款和垫款账面价值": "customer_loans",
        "吸收存款合计": "customer_deposits",
        "现金及存放中央银行款项合计": "central_bank_deposits",
    }

    assert {label: glossary.lookup(label) for label in expected} == expected


def test_glossary_maps_sample_reference_long_descriptions() -> None:
    from ahcc.align.glossary import glossary

    expected = {
        "P17 营业收入2025": "revenue",
        "P17 利润总额2025": "total_profit",
        "P17 归属本行股东净利润2025": "net_profit_attributable",
        "P17 加权平均净资产收益率2025": "weighted_average_roe",
        "P17 不良贷款率2025": "non_performing_loan_ratio",
        "P174 手续费及佣金净收入2025": "commission_net",
        "P182 投资活动现金流量净额2025": "investing_cash_flow",
        "P216 附注6 发放贷款和垫款账面价值2025": "customer_loans",
        "P257 附注23 吸收存款合计2025": "customer_deposits",
        "P8 营业总收入2024": "revenue",
        "P228 附注应收账款账面价值合计2024": "receivables",
        "P272 附注现金及现金等价物年末余额2024": "cash_equivalents_end",
        "P5 总资产": "total_assets",
        "P6 Q4营业收入": "revenue",
        "P85 存货(合并)": "inventory",
        "P87 税金及附加(合并)": "taxes_and_surcharges",
    }

    assert {label: glossary.lookup(label) for label in expected} == expected


def test_key_metric_exact_checker_finds_tamper_in_secondary_statement_occurrence() -> None:
    a_front_revenue = _metric(
        "revenue",
        126_311.0,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="[Revenue · 2025] 126,311",
        period="2025",
    )
    a_statement_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=174,
        unit=None,
        section="income_statement",
        snippet="[Revenue · 2025] 126,411",
        period="2025",
    )
    h_revenue = _metric(
        "revenue",
        126_311.0,
        ReportSide.H_SHARE,
        page=174,
        unit=None,
        section="income_statement",
        snippet="[Revenue · 2025] 126,311",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(a_front_revenue, a_statement_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])

    assert any(
        diff.rule_id == "key_metric_exact_mismatch"
        and diff.canonical_key == "revenue"
        and diff.a_value == 126_411.0
        and diff.h_value == 126_311.0
        for diff in diffs
    )


def test_key_metric_exact_checker_covers_sample_note_and_ratio_keys() -> None:
    a_loans = _metric(
        "customer_loans",
        3_910_379.0,
        ReportSide.A_SHARE,
        page=216,
        unit=None,
        section="note_6",
        snippet="[发放贷款和垫款账面价值 · 2025] 3,910,379",
        period="2025",
    )
    h_loans = _metric(
        "customer_loans",
        3_911_379.0,
        ReportSide.H_SHARE,
        page=216,
        unit=None,
        section="note_6",
        snippet="[Customer loans and advances · 2025] 3,911,379",
        period="2025",
    )
    a_npl = _metric(
        "non_performing_loan_ratio",
        1.37,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="[不良贷款率 · 2025] 1.37",
        period="2025",
    )
    h_npl = _metric(
        "non_performing_loan_ratio",
        1.27,
        ReportSide.H_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="[Non-performing loan ratio · 2025] 1.27",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_loans), _occ(a_npl)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_loans), _occ(h_npl)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])

    keys = {diff.canonical_key for diff in diffs if diff.rule_id == "key_metric_exact_mismatch"}
    assert {"customer_loans", "non_performing_loan_ratio"} <= keys


def test_text_metric_extraction_maps_pymupdf_overlay_numbers_to_same_page_metrics() -> None:
    segment = TextSegment(
        segment_id="A_p008_overlay",
        page=8,
        bbox=(0, 0, 1, 1),
        text=(
            "主要会计数据 2024 年 2023 年 "
            "营业总收入 20,219,547.23 17,321,207.68 "
            "归属于上市公司股东的净利润 1,269,220.42 702,155.97 "
            "经营活动产生的现金流量净额 2,778,262.63 1,775,378.90 "
            "税金及附加 740,941.74 598,625.39 "
            "销售费用 783,034.30 728,641.23 "
            "8/330 20,269,547.23 1,239,220.42 2,718,262.63 790,941.74"
        ),
        language=Language.ZH,
        section="key_metrics",
        raw_text=(
            "主要会计数据\n2024 年\n2023 年\n"
            "营业总收入\n20,219,547.23\n17,321,207.68\n"
            "归属于上市公司股东的净利润\n1,269,220.42\n702,155.97\n"
            "经营活动产生的现金流量净额\n2,778,262.63\n1,775,378.90\n"
            "税金及附加\n740,941.74\n598,625.39\n"
            "销售费用\n783,034.30\n728,641.23\n"
            "8/330\n20,269,547.23\n1,239,220.42\n2,718,262.63\n790,941.74"
        ),
    )

    items = _extract_from_text(segment, ReportSide.A_SHARE, "人民币万元", Currency.CNY)
    revenue_values = {item.value for item in items if item.canonical_key == "revenue"}
    profit_values = {item.value for item in items if item.canonical_key == "net_profit_attributable"}
    tax_values = {item.value for item in items if item.canonical_key == "taxes_and_surcharges"}
    selling_values = {item.value for item in items if item.canonical_key == "selling_expenses"}
    overlay_snippets = [item.evidence.snippet for item in items if "visual overlay" in item.evidence.snippet.lower()]

    assert 20_269_547.23 in revenue_values
    assert 1_239_220.42 in profit_values
    assert 790_941.74 in tax_values
    assert 790_941.74 not in selling_values
    assert len(overlay_snippets) == 4


def test_text_metric_extraction_maps_trailing_standalone_overlay_numbers() -> None:
    seg = TextSegment(
        segment_id="A_p005_trailing_overlay",
        page=5,
        bbox=(0, 0, 1, 1),
        text="",
        raw_text=(
            "主要会计数据\n"
            "营业收入\n"
            "32,137,830,111\n"
            "归属于上市公司股东的净利润\n"
            "4,344,983,858\n"
            "经营活动产生的现金流量净额\n"
            "5,154,661,132\n"
            "季度数据与已披露定期报告数据差异说明\n"
            "32,137,839,111\n"
            "4,345,983,858\n"
            "5,153,661,132\n"
        ),
        language=Language.ZH,
    )

    items = _extract_from_text(seg, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    overlay_items = [
        item for item in items
        if "visual overlay" in (item.evidence.snippet or "").lower()
    ]
    assert {(item.canonical_key, item.value) for item in overlay_items} >= {
        ("revenue", 32_137_839_111.0),
        ("net_profit_attributable", 4_345_983_858.0),
        ("operating_cash_flow", 5_153_661_132.0),
    }


def test_text_metric_extraction_maps_text_only_front_table_overlay_suffix() -> None:
    seg = TextSegment(
        segment_id="A_p005_text_only_overlay",
        page=5,
        bbox=(0, 0, 1, 1),
        text=(
            "主要会计数据\n"
            "营业收入\n"
            "32,137,830,111\n"
            "归属于上市公司股东的净利润\n"
            "4,344,983,858\n"
            "经营活动产生的现金流量净额\n"
            "5,154,661,132\n"
            "归属于上市公司股东的净资产\n"
            "29,060,384,527 27,449,478,216\n"
            "总资产\n"
            "51,420,385,832 49,256,011,349\n"
            "4.39\n"
            "50,311,699,796\n"
            "32,137,839,111\n"
            "4,345,983,858\n"
            "5,153,661,132\n"
            "29,160,384,527\n"
            "51,420,385,872\n"
        ),
        raw_text="",
        language=Language.ZH,
    )

    items = _extract_from_text(seg, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    overlay_pairs = {
        (item.canonical_key, item.value)
        for item in items
        if "visual overlay" in (item.evidence.snippet or "").lower()
    }
    assert ("parent_equity", 29_160_384_527.0) in overlay_pairs
    assert ("total_assets", 51_420_385_872.0) in overlay_pairs
    assert ("total_assets", 50_311_699_796.0) not in overlay_pairs


def test_text_metric_extraction_prefers_line_rich_text_over_flat_raw_text_for_overlay() -> None:
    line_rich_text = (
        "主要会计数据\n"
        "归属于上市公司股\n"
        "东的净资产\n"
        "29,060,384,527 27,449,478,216\n"
        "总资产\n"
        "51,420,385,832 49,256,011,349\n"
        "4.39\n"
        "50,311,699,796\n"
        "29,160,384,527\n"
        "51,420,385,872\n"
    )
    seg = TextSegment(
        segment_id="A_p005_flat_raw_overlay",
        page=5,
        bbox=(0, 0, 1, 1),
        text=line_rich_text,
        raw_text=" ".join(line_rich_text.splitlines()),
        language=Language.ZH,
    )

    items = _extract_from_text(seg, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    overlay_pairs = {
        (item.canonical_key, item.value)
        for item in items
        if "visual overlay" in (item.evidence.snippet or "").lower()
    }
    assert ("parent_equity", 29_160_384_527.0) in overlay_pairs
    assert ("total_assets", 51_420_385_872.0) in overlay_pairs
    assert all(value < 10**12 for _key, value in overlay_pairs)


def test_text_metric_extraction_does_not_treat_operating_table_tail_as_overlay() -> None:
    seg = TextSegment(
        segment_id="A_p011_operating_table",
        page=11,
        bbox=(0, 0, 1, 1),
        text=(
            "产销量情况分析表\n"
            "主要产品\n"
            "单位\n"
            "生产量 销售量\n"
            "库存量\n"
            "生产量比上年增减（%）\n"
            "销售量比上年增减（%）\n"
            "库存量比上年增减（%）\n"
            "啤酒\n"
            "万千升\n"
            "723\n"
            "754\n"
            "52\n"
            "-2.39\n"
            "-5.85\n"
            "0.78\n"
        ),
        raw_text="",
        language=Language.ZH,
        section="revenue",
    )

    items = _extract_from_text(seg, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    assert not any("visual overlay" in (item.evidence.snippet or "").lower() for item in items)


def test_text_metric_extraction_does_not_treat_eps_note_table_tail_as_overlay() -> None:
    seg = TextSegment(
        segment_id="A_p225_eps_note_table",
        page=225,
        bbox=(0, 0, 1, 1),
        text=(
            "净资产收益率及每股收益\n"
            "本净资产收益率和每股收益计算表是按照规则编制。\n"
            "报告期利润\n"
            "加权平均净资产收益率(%)\n"
            "每股收益\n"
            "基本每股收益\n"
            "稀释每股收益\n"
            "归属于公司普通股股东的净利润\n"
            "15.38\n"
            "3.191\n"
            "3.187\n"
            "扣除非经常性损益后归属于公司普通股股东的净利润\n"
            "13.98\n"
            "2.902\n"
            "2.898\n"
        ),
        raw_text="",
        language=Language.ZH,
        section="notes",
    )

    items = _extract_from_text(seg, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    assert not any("visual overlay" in (item.evidence.snippet or "").lower() for item in items)


def test_text_metric_extraction_does_not_treat_receivable_allowance_tail_as_overlay() -> None:
    seg = TextSegment(
        segment_id="A_p212_receivable_allowance",
        page=212,
        bbox=(0, 0, 1, 1),
        text=(
            "应收账款\n"
            "人民币元\n"
            "种类\n"
            "2023 年12 月31 日\n"
            "账面余额\n"
            "坏账准备\n"
            "账面价值\n"
            "金额\n"
            "比例(%)\n"
            "金额\n"
            "计提比例(%)\n"
            "按单项计提坏账准备\n"
            "11,245,784\n"
            "0.7\n"
            "11,245,784\n"
            "100.0\n"
            "-\n"
            "按组合计提坏账准备\n"
            "1,535,195,425\n"
            "99.3\n"
            "45,199,134\n"
            "2.9\n"
            "1,489,996,291\n"
            "合计\n"
            "1,546,441,209\n"
            "100.0\n"
            "56,444,918\n"
            "/\n"
            "1,489,996,291\n"
        ),
        raw_text="",
        language=Language.ZH,
        section="notes",
    )

    items = _extract_from_text(seg, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    assert not any("visual overlay" in (item.evidence.snippet or "").lower() for item in items)


def test_text_metric_extraction_maps_raw_table_ratio_overlay_to_roe() -> None:
    segment = TextSegment(
        segment_id="A_p009_ratio_overlay",
        page=9,
        bbox=(0, 0, 1, 1),
        text=(
            "主要财务指标 2024 年 2023 年 基本每股收益（元／股） 1.49 0.82 "
            "加权平均净资产收益率（%） 17.20 10.61 9/330 4.49 17.70"
        ),
        language=Language.ZH,
        section="key_metrics",
        raw_text=(
            "长城汽车股份有限公司2024 年年度报告\n9 / 393\n"
            "主要财务指标\n2024 年\n2023 年\n"
            "基本每股收益（元／股）\n1.49\n0.82\n"
            "加权平均净资产收益率（%）\n17.20\n10.61\n"
            "9/330\n4.49\n17.70"
        ),
    )

    items = _extract_from_text(segment, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    roe_values = {item.value for item in items if item.canonical_key == "weighted_average_roe"}
    roe_overlay = [
        item for item in items
        if item.canonical_key == "weighted_average_roe"
        and "visual overlay" in item.evidence.snippet.lower()
    ]
    assert 17.20 in roe_values
    assert any(item.value == 17.70 for item in roe_overlay)


def test_text_metric_extraction_maps_cash_flow_statement_overlay_to_operating_cash_flow() -> None:
    segment = TextSegment(
        segment_id="A_p177_cashflow_overlay",
        page=177,
        bbox=(0, 0, 1, 1),
        text=(
            "合并现金流量表 经营活动产生的现金流量净额 27,782,626,338.16 "
            "年末现金及现金等价物余额 27,209,807,036.70 177/330 27,282,626,338.16"
        ),
        language=Language.ZH,
        section="cf",
        raw_text=(
            "合并现金流量表\n项目\n2024 年度\n2023 年度\n"
            "经营活动产生的现金流量净额\n(六)57(1)\n27,782,626,338.16\n17,753,789,028.71\n"
            "年末现金及现金等价物余额\n(六)57(3)\n27,209,807,036.70\n35,272,177,957.35\n"
            "177/330\n27,282,626,338.16"
        ),
    )

    items = _extract_from_text(segment, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    operating_overlays = [
        item for item in items
        if item.canonical_key == "operating_cash_flow"
        and "visual overlay" in item.evidence.snippet.lower()
    ]
    cash_end_overlays = [
        item for item in items
        if item.canonical_key == "cash_equivalents_end"
        and "visual overlay" in item.evidence.snippet.lower()
    ]
    assert any(item.value == 27_282_626_338.16 for item in operating_overlays)
    assert not cash_end_overlays


def test_text_metric_extraction_maps_note_total_overlay_to_note_topic_key() -> None:
    segment = TextSegment(
        segment_id="A_p228_note_overlay",
        page=228,
        bbox=(0, 0, 1, 1),
        text=(
            "财务报表附注 3、 应收账款 账面余额 信用损失准备 账面价值 "
            "合计 7,763,496,536.58 100.00 7,273,343,067.28 228/330 7,273,343,017.28"
        ),
        language=Language.ZH,
        section="notes",
        raw_text=(
            "财务报表附注\n3、 应收账款\n人民币元\n"
            "账面余额\n信用损失准备\n账面价值\n"
            "合计\n7,763,496,536.58\n100.00\n(490,153,469.30)\n6.31\n7,273,343,067.28\n"
            "228/330\n7,273,343,017.28"
        ),
    )

    items = _extract_from_text(segment, ReportSide.A_SHARE, "人民币元", Currency.CNY)

    receivable_values = {item.value for item in items if item.canonical_key == "receivables"}
    receivable_overlays = [
        item for item in items
        if item.canonical_key == "receivables"
        and "visual overlay" in item.evidence.snippet.lower()
    ]
    assert 7_273_343_067.28 in receivable_values
    assert any(item.value == 7_273_343_017.28 for item in receivable_overlays)


def test_key_metric_checker_reports_embedded_visual_overlay_as_internal_diff() -> None:
    text_revenue = _metric(
        "revenue",
        20_219_547.23,
        ReportSide.A_SHARE,
        page=8,
        unit="人民币万元",
        source="text",
        snippet="[营业总收入] 20,219,547.23",
        period="2024",
    )
    overlay_revenue = _metric(
        "revenue",
        20_269_547.23,
        ReportSide.A_SHARE,
        page=8,
        unit="人民币万元",
        confidence=0.86,
        source="generic_pattern",
        snippet="[visual overlay · 营业总收入] 20,269,547.23",
        period="2024",
    )
    h_revenue = _metric(
        "revenue",
        20_219_547.23,
        ReportSide.H_SHARE,
        page=8,
        unit="人民币万元",
        source="text",
        snippet="[Revenue] 20,219,547.23",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_revenue, overlay_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    assert any(
        diff.rule_id == "visual_text_layer_mismatch"
        and diff.diff_scope == DiffScope.A_INTERNAL
        and diff.a_value == 202_695_472_300.0
        and diff.h_value == 202_195_472_300.0
        for diff in diffs
    )


def test_key_metric_checker_reports_operating_cash_flow_visual_overlay() -> None:
    text_cash_flow = _metric(
        "operating_cash_flow",
        2_778_262.63,
        ReportSide.A_SHARE,
        page=8,
        unit="人民币万元",
        source="text",
        snippet="[经营活动现金流量净额] 2,778,262.63",
        period="2024",
    )
    overlay_cash_flow = _metric(
        "operating_cash_flow",
        2_718_262.63,
        ReportSide.A_SHARE,
        page=8,
        unit="人民币万元",
        confidence=0.86,
        source="generic_pattern",
        snippet="[visual overlay · 经营活动现金流量净额] 2,718,262.63",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_cash_flow, overlay_cash_flow)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    assert any(
        diff.rule_id == "visual_text_layer_mismatch"
        and diff.canonical_key == "operating_cash_flow"
        for diff in diffs
    )


def test_key_metric_checker_reports_parent_equity_split_label_visual_overlay() -> None:
    text_parent_equity = _metric(
        "parent_equity",
        29_060_384_527.0,
        ReportSide.A_SHARE,
        page=5,
        unit=None,
        section="key_metrics",
        snippet="[东的净资产] 29,060,384,527",
    )
    overlay_parent_equity = _metric(
        "parent_equity",
        29_160_384_527.0,
        ReportSide.A_SHARE,
        page=5,
        unit=None,
        confidence=0.86,
        section="key_metrics",
        snippet="[visual overlay · 归属于母公司所有者权益合计] 29,160,384,527",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_parent_equity, overlay_parent_equity)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.canonical_key == "parent_equity"
    assert visual.a_value == 29_160_384_527.0
    assert visual.h_value == 29_060_384_527.0


def test_key_metric_checker_skips_operating_cash_flow_visual_overlay_in_notes() -> None:
    text_cash_flow = _metric(
        "operating_cash_flow",
        27_782_626_338.16,
        ReportSide.A_SHARE,
        page=272,
        unit="人民币元",
        section="notes",
        source="text",
        snippet="[经营活动产生的现金流量净额] 27,782,626,338.16",
        period="2024",
    )
    overlay_cash_flow = _metric(
        "operating_cash_flow",
        27_209_807_096.70,
        ReportSide.A_SHARE,
        page=272,
        unit="人民币元",
        section="notes",
        confidence=0.86,
        source="generic_pattern",
        snippet="[visual overlay · 经营活动现金流量净额] 27,209,807,096.70",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_cash_flow, overlay_cash_flow)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    assert not any(diff.canonical_key == "operating_cash_flow" for diff in diffs)


def test_extract_metrics_preserves_quarter_column_context() -> None:
    table = FinancialTable(
        table_id="A_p010_t01",
        title=LocalizedString(zh="二、本年度分季度经营指标"),
        page=10,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="人民币百万元",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="一季度", is_header=True),
            TableCell(row=0, col=2, text="二季度", is_header=True),
            TableCell(row=0, col=3, text="三季度", is_header=True),
            TableCell(row=0, col=4, text="四季度", is_header=True),
            TableCell(row=1, col=0, text="营业收入"),
            TableCell(row=1, col=1, text="33,086"),
            TableCell(row=1, col=2, text="32,832"),
            TableCell(row=1, col=3, text="28,352"),
            TableCell(row=1, col=4, text="32,041"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=20,
        primary_language=Language.ZH,
        tables=[table],
        texts=[],
        metadata={"unit": "人民币百万元", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    revenue = next(occ for occ in metrics if occ.canonical_key == "revenue")
    periods = {item.period for item in revenue.all_occurrences}
    snippets = [item.evidence.snippet for item in revenue.all_occurrences]
    assert {"2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"} <= periods
    assert any("一季度" in snippet and "33,086" in snippet for snippet in snippets)
    assert any("四季度" in snippet and "32,041" in snippet for snippet in snippets)


def test_narratives_preserve_uncategorized_segments() -> None:
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=2,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="s1",
                page=1,
                bbox=(0.0, 0.0, 1.0, 1.0),
                text="这是一段没有稳定主题关键词但长度足够用于画像保留的普通说明文字。",
                language=Language.ZH,
                section=None,
            )
        ],
    )

    blocks = extract_narratives(doc)

    assert blocks
    assert blocks[0].topic_key == "uncategorized"
    assert blocks[0].segments[0].segment_id == "s1"


def test_structure_is_recursive_tree() -> None:
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=3,
        primary_language=Language.ZH,
        texts=[
            TextSegment(segment_id="s1", page=1, bbox=(0, 0, 1, 1), text="管理层讨论与分析", language=Language.ZH, section="mda"),
            TextSegment(segment_id="s2", page=2, bbox=(0, 0, 1, 1), text="合并资产负债表", language=Language.ZH, section="bs"),
            TextSegment(segment_id="s3", page=3, bbox=(0, 0, 1, 1), text="财务报表附注", language=Language.ZH, section="notes"),
        ],
    )

    structure = extract_structure(doc)

    assert structure
    assert any(node.children for node in structure)
    assert any(child.section_code == "bs" for node in structure for child in node.children)


def test_profile_summary_keeps_extraction_audit() -> None:
    audit = ExtractionAudit(
        total_pages=10,
        scanned_pages=list(range(1, 11)),
        blank_pages=[1],
        table_pages=[4, 5],
        coverage_ratio=1.0,
        warning_flags=["many_blank_pages"],
        warnings=["One page produced no text."],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[],
        extraction_audit=audit,
        metadata={"extraction_audit": audit.model_dump(mode="json")},
    )
    profile = _profile(ReportSide.A_SHARE)
    profile.source_doc = doc
    profile.metadata = doc.metadata

    summary = summarize_profile(profile)

    assert summary["extraction_audit"]["total_pages"] == 10
    assert summary["extraction_audit"]["scanned_pages"] == list(range(1, 11))
    assert summary["warning_flags"] == ["many_blank_pages"]


def test_extract_metrics_flags_cross_page_internal_inconsistency() -> None:
    front_table = FinancialTable(
        table_id="A_key_010",
        title=LocalizedString(en="Key accounting data"),
        page=10,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="100"),
        ],
    )
    note_table = FinancialTable(
        table_id="A_key_100",
        title=LocalizedString(en="Key accounting data"),
        page=100,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="2,000"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        tables=[front_table, note_table],
        texts=[],
        metadata={"currency": "CNY"},
    )

    metrics = extract_metrics(doc)
    revenue = next(occ for occ in metrics if occ.canonical_key == "revenue")

    assert revenue.is_internally_consistent is False
    assert revenue.internal_inconsistencies
    pages = {revenue.internal_inconsistencies[0].item_a.page, revenue.internal_inconsistencies[0].item_b.page}
    assert pages == {10, 100}


def test_extract_metrics_does_not_flag_comparative_year_columns_as_internal_inconsistency() -> None:
    table = FinancialTable(
        table_id="A_key_017",
        title=LocalizedString(zh="主要会计数据和财务指标"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="人民币百万元",
        currency=Currency.CNY,
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="2025年", is_header=True),
            TableCell(row=0, col=2, text="2024年", is_header=True),
            TableCell(row=0, col=3, text="2023年", is_header=True),
            TableCell(row=1, col=0, text="净利润"),
            TableCell(row=1, col=1, text="39,141"),
            TableCell(row=1, col=2, text="41,696"),
            TableCell(row=1, col=3, text="34,926"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        tables=[table],
        texts=[],
        metadata={"unit": "人民币百万元", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    net_profit = next(occ for occ in metrics if occ.canonical_key == "net_profit")
    periods = {item.period for item in net_profit.all_occurrences}
    assert {"2025", "2024", "2023"} <= periods
    assert net_profit.is_internally_consistent is True
    assert not net_profit.internal_inconsistencies


def test_extract_metrics_does_not_flag_annual_against_quarterly_internal_inconsistency() -> None:
    annual_table = FinancialTable(
        table_id="A_key_017",
        title=LocalizedString(en="Key accounting data"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="126,411"),
        ],
    )
    quarterly_table = FinancialTable(
        table_id="A_quarterly_035",
        title=LocalizedString(en="Quarterly operating indicators"),
        page=35,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="First quarter", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="33,086"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        tables=[annual_table, quarterly_table],
        texts=[],
        metadata={"unit": "RMB million", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    revenue = next(occ for occ in metrics if occ.canonical_key == "revenue")
    assert revenue.is_internally_consistent is True
    assert not revenue.internal_inconsistencies


def test_extract_metrics_does_not_flag_change_amount_column_as_internal_inconsistency() -> None:
    main_table = FinancialTable(
        table_id="A_main_revenue_017",
        title=LocalizedString(en="Key accounting data"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="126,411"),
        ],
    )
    movement_table = FinancialTable(
        table_id="A_change_amount_017",
        title=LocalizedString(en="Movement analysis"),
        page=218,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="Change amount", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="4,565"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.EN,
        tables=[main_table, movement_table],
        texts=[],
        metadata={"unit": "RMB million", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    revenue = next(occ for occ in metrics if occ.canonical_key == "revenue")
    assert revenue.is_internally_consistent is True
    assert not revenue.internal_inconsistencies


def test_extract_metrics_does_not_flag_adjustment_amount_column_as_internal_inconsistency() -> None:
    main_table = FinancialTable(
        table_id="A_main_cost_132",
        title=LocalizedString(zh="会计政策变更调整前后对照"),
        page=132,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="元",
        currency=Currency.CNY,
        period="2024",
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="2024年度 调整前", is_header=True),
            TableCell(row=0, col=2, text="调整金额", is_header=True),
            TableCell(row=1, col=0, text="营业成本"),
            TableCell(row=1, col=1, text="161,198,996,414.89"),
            TableCell(row=1, col=2, text="1,547,757,444.43"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=300,
        primary_language=Language.ZH,
        tables=[main_table],
        texts=[],
        metadata={"unit": "元", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    cost = next(occ for occ in metrics if occ.canonical_key == "cost_of_revenue")
    assert cost.is_internally_consistent is True
    assert not cost.internal_inconsistencies


def test_extract_metrics_does_not_flag_restricted_book_value_against_main_balance() -> None:
    restricted_table = FinancialTable(
        table_id="A_restricted_cash_034",
        title=LocalizedString(zh="所有权受到限制的资产"),
        page=34,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="元",
        currency=Currency.CNY,
        period="2024",
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="年末账面价值", is_header=True),
            TableCell(row=0, col=2, text="受限原因", is_header=True),
            TableCell(row=1, col=0, text="货币资金"),
            TableCell(row=1, col=1, text="3,470,977,563.95"),
            TableCell(row=1, col=2, text="保证金"),
        ],
    )
    main_table = FinancialTable(
        table_id="A_notes_cash_280",
        title=LocalizedString(zh="货币资金附注"),
        page=280,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="元",
        currency=Currency.CNY,
        period="2024",
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="本年年末数", is_header=True),
            TableCell(row=0, col=2, text="上年年末数", is_header=True),
            TableCell(row=1, col=0, text="货币资金"),
            TableCell(row=1, col=1, text="30,740,975,160.65"),
            TableCell(row=1, col=2, text="27,209,807,036.70"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=300,
        primary_language=Language.ZH,
        tables=[restricted_table, main_table],
        texts=[],
        metadata={"unit": "元", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    cash = next(occ for occ in metrics if occ.canonical_key == "cash_equivalents")
    assert cash.is_internally_consistent is True
    assert not cash.internal_inconsistencies


def test_extract_metrics_does_not_flag_foreign_currency_translation_against_main_balance() -> None:
    fx_table = FinancialTable(
        table_id="A_fx_cash_274",
        title=LocalizedString(zh="外币货币性项目"),
        page=274,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="元",
        currency=Currency.CNY,
        period="2024",
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="年末外币余额", is_header=True),
            TableCell(row=0, col=2, text="折算汇率", is_header=True),
            TableCell(row=0, col=3, text="年末折算人民币余额", is_header=True),
            TableCell(row=1, col=0, text="货币资金"),
            TableCell(row=1, col=1, text="4,649,301,718.74"),
            TableCell(row=1, col=2, text="7.1884"),
            TableCell(row=1, col=3, text="4,649,301,718.74"),
        ],
    )
    main_table = FinancialTable(
        table_id="A_notes_cash_280",
        title=LocalizedString(zh="货币资金附注"),
        page=280,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="元",
        currency=Currency.CNY,
        period="2024",
        cells=[
            TableCell(row=0, col=0, text="项目", is_header=True),
            TableCell(row=0, col=1, text="本年年末数", is_header=True),
            TableCell(row=1, col=0, text="货币资金"),
            TableCell(row=1, col=1, text="30,740,975,160.65"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=300,
        primary_language=Language.ZH,
        tables=[fx_table, main_table],
        texts=[],
        metadata={"unit": "元", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    cash = next(occ for occ in metrics if occ.canonical_key == "cash_equivalents")
    assert cash.is_internally_consistent is True
    assert not cash.internal_inconsistencies


def test_extract_metrics_does_not_flag_ratio_column_as_internal_inconsistency() -> None:
    main_table = FinancialTable(
        table_id="A_main_assets_017",
        title=LocalizedString(en="Key accounting data"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Total assets"),
            TableCell(row=1, col=1, text="6,959,021"),
        ],
    )
    ratio_table = FinancialTable(
        table_id="A_ratio_320",
        title=LocalizedString(en="Asset composition"),
        page=320,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="Percentage of total", is_header=True),
            TableCell(row=1, col=0, text="Total assets"),
            TableCell(row=1, col=1, text="38.72%"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=354,
        primary_language=Language.EN,
        tables=[main_table, ratio_table],
        texts=[],
        metadata={"unit": "RMB million", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    total_assets = next(occ for occ in metrics if occ.canonical_key == "total_assets")
    assert total_assets.is_internally_consistent is True
    assert not total_assets.internal_inconsistencies


def test_extract_metrics_does_not_flag_maturity_bucket_column_as_internal_inconsistency() -> None:
    main_table = FinancialTable(
        table_id="A_main_assets_017",
        title=LocalizedString(en="Key accounting data"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Total assets"),
            TableCell(row=1, col=1, text="6,959,021"),
        ],
    )
    maturity_table = FinancialTable(
        table_id="A_maturity_329",
        title=LocalizedString(en="Maturity analysis of total assets"),
        page=329,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="Within 3 months", is_header=True),
            TableCell(row=0, col=2, text="Past due / indefinite", is_header=True),
            TableCell(row=1, col=0, text="Total assets"),
            TableCell(row=1, col=1, text="829,121"),
            TableCell(row=1, col=2, text="352,897"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=354,
        primary_language=Language.EN,
        tables=[main_table, maturity_table],
        texts=[],
        metadata={"unit": "RMB million", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    total_assets = next(occ for occ in metrics if occ.canonical_key == "total_assets")
    assert total_assets.is_internally_consistent is True
    assert not total_assets.internal_inconsistencies


def test_internal_metric_comparator_skips_non_main_semantic_roles() -> None:
    from ahcc.profile.extract_metrics import (
        _internal_evidence_allows_consistency,
        _internal_key_allows_consistency,
        _internal_metric_pair_comparable,
    )

    assert _internal_key_allows_consistency("ear") is False
    assert _internal_key_allows_consistency("plan") is False
    assert _internal_key_allows_consistency("revenue") is True
    assert _internal_key_allows_consistency("net_profit") is True
    noisy_fragment = _metric(
        "net_profit",
        12.0,
        ReportSide.H_SHARE,
        unit="RMB thousand",
        snippet="[Net profit un · 202 202 192 162 7 7 4 9 1 3 13 14 1 12] 12",
    )
    clean_amount = _metric(
        "net_profit",
        2_000.0,
        ReportSide.H_SHARE,
        unit="RMB thousand",
        snippet="Consolidated income statement Net profit 2,000",
    )
    audit_matter_page_number = _metric(
        "financial_investments",
        49.0,
        ReportSide.H_SHARE,
        unit="RMB million",
        section="关键审计事项(续)",
        snippet="关键审计事项(续) 金融投资 49 估值模型及管理层判断",
    )
    assert _internal_evidence_allows_consistency("net_profit", noisy_fragment) is False
    assert _internal_evidence_allows_consistency("net_profit", clean_amount) is True
    assert _internal_evidence_allows_consistency("financial_investments", audit_matter_page_number) is False

    main_revenue = _metric(
        "revenue",
        202_195_472_334.88,
        ReportSide.A_SHARE,
        page=175,
        unit=None,
        section="income_statement",
        snippet="[Revenue · 2024] 202,195,472,334.88",
        period="2024",
    )
    same_revenue = _metric(
        "revenue",
        202_195_472_334.88,
        ReportSide.A_SHARE,
        page=220,
        unit=None,
        section="notes",
        snippet="[Revenue · 2024] 202,195,472,334.88",
        period="2024",
    )
    ratio_of_revenue = _metric(
        "revenue",
        5.17,
        ReportSide.A_SHARE,
        page=30,
        unit=None,
        section="key_metrics",
        snippet="[R&D expense as a percentage of revenue (%) · 5,265,994,458.13] 5.17",
        period="2024",
    )
    inventory_transfer = _metric(
        "inventory",
        807_346.06,
        ReportSide.A_SHARE,
        page=243,
        unit=None,
        section="notes",
        snippet="[3. Inventories transferred in · Buildings] 807,346.06",
        period="2024",
    )
    inventory_main = _metric(
        "inventory",
        25_407_813_490.36,
        ReportSide.A_SHARE,
        page=235,
        unit=None,
        section="notes",
        snippet="[Inventories · carrying amount total] 25,407,813,490.36",
        period="2024",
    )
    materiality_threshold = _metric(
        "construction_in_progress",
        10.0,
        ReportSide.A_SHARE,
        page=185,
        unit=None,
        section="notes",
        snippet="[Significant construction in progress · materiality threshold] 10%",
        period="2024",
    )
    construction_main = _metric(
        "construction_in_progress",
        3_960_451_831.94,
        ReportSide.A_SHARE,
        page=32,
        unit=None,
        section="notes",
        snippet="[Construction in progress · ending balance] 3,960,451,831.94",
        period="2024",
    )
    opening_derivative = _metric(
        "derivative_financial_assets",
        11_834_681.50,
        ReportSide.A_SHARE,
        page=11,
        unit=None,
        section="notes",
        snippet="[Derivative financial assets · opening balance] 11,834,681.50",
        period="2024",
    )
    closing_derivative = _metric(
        "derivative_financial_assets",
        10_163_635.81,
        ReportSide.A_SHARE,
        page=280,
        unit=None,
        section="notes",
        snippet="[Derivative financial assets · closing balance] 10,163,635.81",
        period="2024",
    )
    profit_effect = _metric(
        "short_term_borrowings",
        11_209_834.46,
        ReportSide.A_SHARE,
        page=284,
        unit=None,
        section="notes",
        snippet="[Short-term borrowings · impact on profit before tax] 11,209,834.46",
        period="2024",
    )
    borrowings_main = _metric(
        "short_term_borrowings",
        6_664_939_122.24,
        ReportSide.A_SHARE,
        page=281,
        unit=None,
        section="notes",
        snippet="[Short-term borrowings · closing balance] 6,664,939,122.24",
        period="2024",
    )
    profit_distribution_component = _metric(
        "profit_distribution",
        13_892_581.89,
        ReportSide.A_SHARE,
        page=179,
        unit=None,
        section="equity_statement",
        snippet="[(III) Profit distribution · treasury shares] 13,892,581.89",
        period="2024",
    )
    profit_distribution_main = _metric(
        "profit_distribution",
        4_512_660.50,
        ReportSide.A_SHARE,
        page=180,
        unit=None,
        section="equity_statement",
        snippet="[(III) Profit distribution · retained earnings] 4,512,660.50",
        period="2024",
    )

    assert _internal_metric_pair_comparable("revenue", main_revenue, same_revenue) is True
    assert _internal_metric_pair_comparable("revenue", main_revenue, ratio_of_revenue) is False
    assert _internal_metric_pair_comparable("inventory", inventory_main, inventory_transfer) is False
    assert _internal_metric_pair_comparable("construction_in_progress", construction_main, materiality_threshold) is False
    assert _internal_metric_pair_comparable("derivative_financial_assets", opening_derivative, closing_derivative) is False
    assert _internal_metric_pair_comparable("short_term_borrowings", borrowings_main, profit_effect) is False
    assert _internal_metric_pair_comparable(
        "profit_distribution",
        profit_distribution_component,
        profit_distribution_main,
    ) is False


def test_h_share_profile_internal_inconsistency_requires_review() -> None:
    from ahcc.profile.compare import check_internal_consistency

    first = _metric(
        "financial_investments",
        2_371_901.0,
        ReportSide.H_SHARE,
        page=168,
        unit="RMB million",
        section="合併財務狀況表",
        snippet="[合併財務狀況表 金融投资] 2,371,901",
    )
    second = _metric(
        "financial_investments",
        1_233_695.0,
        ReportSide.H_SHARE,
        page=168,
        unit="RMB million",
        section="合併財務狀況表",
        snippet="[合併財務狀況表 金融投资] 1,233,695",
    )
    profile = _profile(ReportSide.H_SHARE, metrics=[_occ_many(first, second)])
    profile.metrics[0].is_internally_consistent = False
    profile.metrics[0].internal_inconsistencies.append(
        InternalInconsistency(item_a=first, item_b=second, delta=1_138_206.0, delta_pct=48.0)
    )

    diffs = check_internal_consistency(profile)

    assert len(diffs) == 1
    assert diffs[0].triage == "unresolved"


def test_internal_metric_comparator_skips_component_and_movement_roles() -> None:
    from ahcc.profile.extract_metrics import _internal_metric_pair_comparable

    cases = [
        (
            "government_grants",
            _metric(
                "government_grants",
                560_001_090,
                ReportSide.A_SHARE,
                page=171,
                section="cash_flow_notes",
                snippet="[收到的政府补助] 560,001,090",
                period="2024",
            ),
            _metric(
                "government_grants",
                469_273_541,
                ReportSide.A_SHARE,
                page=225,
                section="notes",
                snippet="[计入当期损益的政府补助，但与公司正常经营业务密切相关] 469,273,541",
                period="2024",
            ),
        ),
        (
            "long_term_investments",
            _metric(
                "long_term_investments",
                48_272_283,
                ReportSide.A_SHARE,
                page=167,
                section="notes",
                snippet="[权益法核算的长期股权投资收益] 48,272,283",
                period="2024",
            ),
            _metric(
                "long_term_investments",
                1_805_089_447,
                ReportSide.A_SHARE,
                page=224,
                section="notes",
                snippet="[成本法核算的长期股权投资收益(注)] 1,805,089,447",
                period="2024",
            ),
        ),
        (
            "intangible_assets",
            _metric(
                "intangible_assets",
                2_975_241,
                ReportSide.A_SHARE,
                page=168,
                section="notes",
                snippet="[无形资产处置(损失)收益 · 本年发生额] 2,975,241",
                period="2024",
            ),
            _metric(
                "intangible_assets",
                161_090_719,
                ReportSide.A_SHARE,
                page=175,
                section="cash_flow_reconciliation",
                snippet="[无形资产摊销] 161,090,719",
                period="2024",
            ),
        ),
        (
            "deferred_income",
            _metric(
                "deferred_income",
                314_548_027,
                ReportSide.A_SHARE,
                page=175,
                section="cash_flow_reconciliation",
                snippet="[递延收益摊销] 314,548,027",
                period="2024",
            ),
            _metric(
                "deferred_income",
                2_858_207_387,
                ReportSide.A_SHARE,
                page=187,
                section="notes",
                snippet="[递延收益 · 2023年12月31日] 2,858,207,387",
                period="2024",
            ),
        ),
        (
            "total_comprehensive_income",
            _metric(
                "total_comprehensive_income",
                72_479_048,
                ReportSide.A_SHARE,
                page=89,
                section="equity_statement",
                snippet="[(一)综合收益总额 · 其他综合收益] 72,479,048",
                period="2024",
            ),
            _metric(
                "total_comprehensive_income",
                13_829_210,
                ReportSide.A_SHARE,
                page=185,
                section="notes",
                snippet="[综合收益总额 · 本年发生额] 13,829,210",
                period="2024",
            ),
        ),
    ]

    for key, item_a, item_b in cases:
        assert _internal_metric_pair_comparable(key, item_a, item_b) is False


def test_internal_metric_comparator_skips_equity_cashflow_and_asset_detail_roles() -> None:
    from ahcc.profile.extract_metrics import _internal_metric_pair_comparable

    cases = [
        (
            "profit_distribution",
            _metric(
                "profit_distribution",
                13_892_581.89,
                ReportSide.A_SHARE,
                page=179,
                section="equity_statement",
                snippet="[(三)利润分配 · 减：库存股] 13,892,581.89",
                period="2024",
            ),
            _metric(
                "profit_distribution",
                4_512_660.50,
                ReportSide.A_SHARE,
                page=180,
                section="equity_statement",
                snippet="[(三)利润分配 · 减：库存股] 4,512,660.50",
                period="2024",
            ),
        ),
        (
            "profit_distribution",
            _metric(
                "profit_distribution",
                3_001_232_934,
                ReportSide.A_SHARE,
                page=41,
                section="dividend_policy",
                snippet="[现金分红金额（含税） · 22] 3,001,232,934",
                period="2024",
            ),
            _metric(
                "profit_distribution",
                8_665_060,
                ReportSide.A_SHARE,
                page=89,
                section="equity_statement",
                snippet="[(三)利润分配 · 减：库存股] 8,665,060",
                period="2024",
            ),
        ),
        (
            "inventory",
            _metric(
                "inventory",
                104,
                ReportSide.A_SHARE,
                page=104,
                section="accounting_policy",
                snippet="[集团合并计提存货跌价准备。 · 存货] 104",
                period="2024",
            ),
            _metric(
                "inventory",
                22_663,
                ReportSide.A_SHARE,
                page=167,
                section="notes",
                snippet="[存货跌价损失] 22,663",
                period="2024",
            ),
        ),
        (
            "investment_property",
            _metric(
                "investment_property",
                2_410_719,
                ReportSide.A_SHARE,
                page=175,
                section="cash_flow_reconciliation",
                snippet="[投资性房地产折旧] 2,410,719",
                period="2024",
            ),
            _metric(
                "investment_property",
                5_115_813,
                ReportSide.A_SHARE,
                page=179,
                section="notes",
                snippet="[投资性房地产 · 租赁收入] 5,115,813",
                period="2024",
            ),
        ),
        (
            "deferred_tax_assets",
            _metric(
                "deferred_tax_assets",
                155_696_486,
                ReportSide.A_SHARE,
                page=169,
                section="income_tax_notes",
                snippet="[使用前期未确认递延所得税资产的可抵扣亏损] 155,696,486",
                period="2024",
            ),
            _metric(
                "deferred_tax_assets",
                86_779_920,
                ReportSide.A_SHARE,
                page=175,
                section="cash_flow_reconciliation",
                snippet="[递延所得税资产减少] 86,779,920",
                period="2024",
            ),
        ),
        (
            "deferred_income",
            _metric(
                "deferred_income",
                2_858_207_387,
                ReportSide.A_SHARE,
                page=187,
                section="notes",
                snippet="[递延收益 · 2023年12月31日] 2,858,207,387",
                period="2024",
            ),
            _metric(
                "deferred_income",
                251_130_785,
                ReportSide.A_SHARE,
                page=187,
                section="notes",
                snippet="[递延收益 · 本年新增 补助金额] 251,130,785",
                period="2024",
            ),
        ),
        (
            "net_profit",
            _metric(
                "net_profit",
                4_491_776_686,
                ReportSide.A_SHARE,
                page=175,
                section="income_statement",
                snippet="[净利润] 4,491,776,686",
                period="2024",
            ),
            _metric(
                "net_profit",
                13_829_210,
                ReportSide.A_SHARE,
                page=185,
                section="cash_flow_reconciliation",
                snippet="[净利润 · 本年发生额 312,906,292 (1,101,650) 4,986,344] 13,829,210",
                period="2024",
            ),
        ),
        (
            "retained_earnings",
            _metric(
                "retained_earnings",
                43_272_339_677.50,
                ReportSide.A_SHARE,
                page=71,
                section="parent_company_distribution",
                snippet="[最近一个会计年度母公司报表年度末未分配利润 · 12,692,204,172.58] 43,272,339,677.50",
                period="2024",
            ),
            _metric(
                "retained_earnings",
                39_983_051_398.08,
                ReportSide.A_SHARE,
                page=318,
                section="notes",
                snippet="[年初未分配利润 · 金额] 39,983,051,398.08",
                period="2024",
            ),
        ),
    ]

    for key, item_a, item_b in cases:
        assert _internal_metric_pair_comparable(key, item_a, item_b) is False


def test_extract_metrics_does_not_flag_note_reference_column_as_internal_inconsistency() -> None:
    main_table = FinancialTable(
        table_id="A_main_interest_017",
        title=LocalizedString(en="Key accounting data"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Net interest income"),
            TableCell(row=1, col=1, text="92,101"),
        ],
    )
    note_table = FinancialTable(
        table_id="A_note_ref_210",
        title=LocalizedString(en="Income statement"),
        page=210,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        period="2025",
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="Note", is_header=True),
            TableCell(row=1, col=0, text="Net interest income"),
            TableCell(row=1, col=1, text="35"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=354,
        primary_language=Language.EN,
        tables=[main_table, note_table],
        texts=[],
        metadata={"unit": "RMB million", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    interest_net = next(occ for occ in metrics if occ.canonical_key == "interest_net")
    assert interest_net.is_internally_consistent is True
    assert not interest_net.internal_inconsistencies


def test_extract_metrics_does_not_flag_subsidiary_detail_against_group_internal_inconsistency() -> None:
    group_table = FinancialTable(
        table_id="A_key_017",
        title=LocalizedString(en="Key accounting data"),
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Net profit"),
            TableCell(row=1, col=1, text="39,141"),
        ],
    )
    subsidiary_table = FinancialTable(
        table_id="A_subsidiary_178",
        title=LocalizedString(en="China Everbright Bank Company Limited"),
        page=178,
        bbox=(0.0, 0.0, 1.0, 1.0),
        unit="RMB million",
        currency=Currency.CNY,
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Net profit"),
            TableCell(row=1, col=1, text="41,696"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=220,
        primary_language=Language.ZH,
        tables=[group_table, subsidiary_table],
        texts=[],
        metadata={"unit": "RMB million", "currency": "CNY"},
    )

    metrics = extract_metrics(doc)

    net_profit = next(occ for occ in metrics if occ.canonical_key == "net_profit")
    assert net_profit.is_internally_consistent is True
    assert not net_profit.internal_inconsistencies


def test_extract_metrics_flags_non_key_cross_page_internal_inconsistency() -> None:
    front_table = FinancialTable(
        table_id="A_inventory_012",
        title=LocalizedString(en="Balance sheet"),
        page=12,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Inventories"),
            TableCell(row=1, col=1, text="800"),
        ],
    )
    note_table = FinancialTable(
        table_id="A_inventory_088",
        title=LocalizedString(en="Notes"),
        page=88,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Inventories"),
            TableCell(row=1, col=1, text="1,600"),
        ],
    )
    doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        tables=[front_table, note_table],
        texts=[],
        metadata={"currency": "CNY"},
    )

    metrics = extract_metrics(doc)
    inventory = next(occ for occ in metrics if occ.canonical_key == "inventory")

    assert inventory.is_internally_consistent is False
    assert inventory.internal_inconsistencies
    pages = {inventory.internal_inconsistencies[0].item_a.page, inventory.internal_inconsistencies[0].item_b.page}
    assert pages == {12, 88}


def test_report_document_internal_metric_inconsistency_becomes_a_internal_diff() -> None:
    a_front_table = FinancialTable(
        table_id="A_key_010",
        title=LocalizedString(en="Key accounting data"),
        page=10,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="100"),
        ],
    )
    a_note_table = FinancialTable(
        table_id="A_key_100",
        title=LocalizedString(en="Key accounting data"),
        page=100,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="2,000"),
        ],
    )
    h_table = FinancialTable(
        table_id="H_key_010",
        title=LocalizedString(en="Key accounting data"),
        page=10,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="2025", is_header=True),
            TableCell(row=1, col=0, text="Revenue"),
            TableCell(row=1, col=1, text="100"),
        ],
    )
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        tables=[a_front_table, a_note_table],
        texts=[],
        metadata={"currency": "CNY"},
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=120,
        primary_language=Language.EN,
        tables=[h_table],
        texts=[],
        metadata={"currency": "CNY"},
    )

    profile_a = asyncio.run(build_profile(a_doc))
    profile_h = asyncio.run(build_profile(h_doc))
    diffs = asyncio.run(run_disclosure_checks_on_profiles(profile_a, profile_h))

    internal = next(diff for diff in diffs if diff.diff_scope == DiffScope.A_INTERNAL)
    assert internal.diff_type == DiffType.INTERNAL
    assert internal.triage == "real"
    assert internal.diff_explanation is not None
    assert internal.diff_explanation.items[0].a_value == 100.0
    assert internal.diff_explanation.items[0].h_value == 2000.0
    assert internal.diff_explanation.items[0].a_page == 10
    assert internal.diff_explanation.items[0].h_page == 100


def test_compare_metrics_triages_real_expected_and_keeps_evidence() -> None:
    a_total = _metric("total_assets", 100.0, ReportSide.A_SHARE, page=197)
    h_total = _metric("total_assets", 120.0, ReportSide.H_SHARE, page=21)
    a_pref = _metric("preferred_stock", 1.0, ReportSide.A_SHARE, page=30)
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_total), _occ(a_pref)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_total)])

    diffs = compare_metrics(profile_a, profile_h)

    mismatch = next(d for d in diffs if d.canonical_key == "total_assets")
    expected = next(d for d in diffs if d.canonical_key == "preferred_stock")
    assert mismatch.triage == "real"
    assert {ev.side for ev in mismatch.evidence} == {ReportSide.A_SHARE, ReportSide.H_SHARE}
    assert mismatch.a_pages == [197]
    assert mismatch.h_pages == [21]
    assert expected.triage == "expected"


def test_compare_metrics_does_not_report_quarterly_revenue_as_real_diff() -> None:
    a_annual_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        section="pl",
        snippet="合并利润表 营业收入 126,411",
        period="2025",
    )
    h_quarter_revenue = _metric(
        "revenue",
        33_086.0,
        ReportSide.H_SHARE,
        page=35,
        unit="人民币百万元",
        section="key_metrics",
        snippet="二、本年度分季度经营指标 项目 一季度 营业收入 33,086",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_annual_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_quarter_revenue)])

    diffs = compare_metrics(profile_a, profile_h)

    assert not any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)
    assert any(
        d.canonical_key == "revenue"
        and d.triage == "unresolved"
        and "quarterly_vs_annual" in (d.rationale or d.summary.zh or d.summary.en)
        for d in diffs
    )


def test_single_side_profile_items_are_coverage_not_diffs() -> None:
    metric = _metric("total_assets", 100.0, ReportSide.A_SHARE, page=3)
    block = NarrativeBlock(
        topic_key="share_changes",
        topic_label="股份变动",
        word_count=200,
        page_range=(5, 5),
        evidence=[_evidence(ReportSide.A_SHARE, 5, "股份变动情况")],
    )
    node = ChapterNode(
        title=LocalizedString(zh="重大事项"),
        section_code="significant_events",
        page_start=9,
        page_end=10,
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(metric)], narratives=[block], structure=[node])
    profile_h = _profile(ReportSide.H_SHARE, narratives=[])

    numeric_diffs = run_numeric_checks_on_profiles(profile_a, profile_h)
    diffs = asyncio.run(run_disclosure_checks_on_profiles(profile_a, profile_h))
    coverage, event_diffs = build_disclosure_coverage(profile_a, profile_h)

    assert numeric_diffs == []
    assert diffs == []
    assert event_diffs == []
    assert any(item.category == "metric" and item.status == "a_only" for item in coverage)
    assert any(item.category == "narrative" and item.status == "a_only" for item in coverage)
    assert any(item.category == "structure" and item.status == "a_only" for item in coverage)


def test_numeric_checker_uses_all_occurrences_before_reporting_real_diff() -> None:
    a_primary = _metric(
        "interest_payable",
        448_182_540.45,
        ReportSide.A_SHARE,
        page=181,
        snippet="应付股利 448,182,540.45",
    )
    a_matching = _metric(
        "interest_payable",
        301_550_000.0,
        ReportSide.A_SHARE,
        page=171,
        snippet="应付股利 301,550,000.00",
    )
    h_matching = _metric(
        "interest_payable",
        301_550.0,
        ReportSide.H_SHARE,
        page=277,
        unit="人民币千元",
        snippet="應付股利 301,550",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(a_primary, a_matching)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_matching)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.canonical_key == "interest_payable" and d.triage == "real" for d in diffs)


def test_numeric_checker_context_mismatch_is_unresolved_not_real() -> None:
    a_revenue = _metric(
        "revenue",
        10_851_821_801.07,
        ReportSide.A_SHARE,
        page=11,
        snippet="合并利润表 营业收入 10,851,821,801.07",
    )
    h_segment_revenue = _metric(
        "revenue",
        9_709_834.0,
        ReportSide.H_SHARE,
        page=241,
        unit="人民币千元",
        section="notes",
        snippet="业务分部 总收入 9,709,834",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_segment_revenue)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)
    assert any(d.canonical_key == "revenue" and d.triage == "unresolved" and d.rule_id == "context_mismatch" for d in diffs)


def test_numeric_checker_does_not_compare_quarterly_revenue_to_annual_metric() -> None:
    a_annual_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        section="pl",
        snippet="合并利润表 营业收入 126,411",
        period="2025",
    )
    h_quarter_revenue = _metric(
        "revenue",
        33_086.0,
        ReportSide.H_SHARE,
        page=35,
        unit="人民币百万元",
        section="pl",
        snippet="本年度分季度经营指标 一季度 营业收入 33,086",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_annual_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_quarter_revenue)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)
    assert any(d.canonical_key == "revenue" and d.triage == "unresolved" for d in diffs)


def test_numeric_checker_understands_chinese_quarterly_operating_indicator_caption() -> None:
    a_annual_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        section="pl",
        snippet="合并利润表 营业收入 126,411",
        period="2025",
    )
    h_quarter_revenue = _metric(
        "revenue",
        33_086.0,
        ReportSide.H_SHARE,
        page=35,
        unit="人民币百万元",
        section="key_metrics",
        snippet="二、本年度分季度经营指标 项目 一季度 二季度 三季度 四季度 营业收入 33,086",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_annual_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_quarter_revenue)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)
    assert any(
        d.canonical_key == "revenue"
        and d.triage == "unresolved"
        and "quarterly_vs_annual" in (
            getattr(d, "review_hint", None)
            or getattr(d, "rationale", None)
            or d.summary.zh
            or d.summary.en
        )
        for d in diffs
    )


def test_numeric_checker_does_not_compare_compact_q4_revenue_to_annual_metric() -> None:
    a_annual_revenue = _metric(
        "revenue",
        32_137_830_111.0,
        ReportSide.A_SHARE,
        page=5,
        unit="元",
        section="key_metrics",
        snippet="主要会计数据 营业收入 32,137,830,111",
        period="2024",
    )
    h_q4_revenue = _metric(
        "revenue",
        3_178_541_614.0,
        ReportSide.H_SHARE,
        page=6,
        unit="元",
        section="key_metrics",
        snippet="P6 Q4营业收入 3,178,541,614",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_annual_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_q4_revenue)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)
    assert any(
        d.canonical_key == "revenue"
        and d.triage == "unresolved"
        and "quarterly_vs_annual" in (
            getattr(d, "review_hint", None)
            or getattr(d, "rationale", None)
            or d.summary.zh
            or d.summary.en
        )
        for d in diffs
    )


def test_numeric_checker_llm_review_can_downgrade_high_confidence_candidate(monkeypatch) -> None:
    from ahcc.check import numeric as numeric_check

    calls: list[str] = []

    def fake_cached_call(purpose, messages, *, json_mode=False, **kwargs):
        calls.append(messages[0]["content"])
        return {
            "comparable": False,
            "confidence": 0.91,
            "reason": "One side is a quarterly operating indicator, not an annual revenue figure.",
        }

    monkeypatch.setattr(numeric_check.settings, "numeric_use_llm_semantic_review", True, raising=False)
    monkeypatch.setattr(numeric_check.settings, "deepseek_api_key", "sk-real123")
    monkeypatch.setattr(numeric_check, "cached_call", fake_cached_call, raising=False)

    a_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        section="pl",
        snippet="合并利润表 营业收入 126,411",
        period="2025",
    )
    h_revenue = _metric(
        "revenue",
        33_086.0,
        ReportSide.H_SHARE,
        page=35,
        unit="人民币百万元",
        section="pl",
        snippet="经营指标 营业收入 33,086",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert calls
    assert "Return strict JSON" in calls[0]
    assert not any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)
    assert any(d.canonical_key == "revenue" and d.rule_id == "llm_semantic_review" for d in diffs)


def test_numeric_checker_does_not_call_deepseek_for_plain_main_statement_pair(monkeypatch) -> None:
    from ahcc.check import numeric as numeric_check

    def fail_cached_call(*_args, **_kwargs):
        raise AssertionError("Plain main-statement numeric pairs should not call DeepSeek")

    monkeypatch.setattr(numeric_check.settings, "numeric_use_llm_semantic_review", True, raising=False)
    monkeypatch.setattr(numeric_check.settings, "deepseek_api_key", "sk-real123")
    monkeypatch.setattr(numeric_check, "cached_call", fail_cached_call, raising=False)

    a_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        section="pl",
        snippet="合并利润表 营业收入 126,411",
        period="2025",
    )
    h_revenue = _metric(
        "revenue",
        120_000.0,
        ReportSide.H_SHARE,
        page=19,
        unit="人民币百万元",
        section="pl",
        snippet="Consolidated income statement Revenue 120,000",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert any(d.canonical_key == "revenue" and d.triage == "real" for d in diffs)


def test_standard_checker_does_not_recreate_quarterly_revenue_false_positive(monkeypatch) -> None:
    from ahcc.check import standard as standard_check

    monkeypatch.setattr(
        standard_check,
        "retrieve_clauses",
        lambda query, top_k=4: [{"text": "CAS and IFRS both require comparable revenue presentation."}],
    )
    monkeypatch.setattr(
        standard_check,
        "load_prompt",
        lambda name: "topic={topic_zh}; a={a_value}; h={h_value}; clauses={retrieved_clauses}",
    )
    monkeypatch.setattr(
        standard_check,
        "cached_call",
        lambda *args, **kwargs: {
            "expected": False,
            "rationale": "The difference is not explained by CAS/HKFRS standards.",
            "citations": [],
            "confidence": 0.93,
        },
    )

    pair = AlignedPair(
        canonical_key="revenue",
        topic_zh="营业收入",
        topic_en="Revenue",
        a_point=DataPoint(
            name=LocalizedString(zh="营业收入", en="Revenue"),
            canonical_key="revenue",
            value=126_411.0,
            value_text="126,411",
            unit="人民币百万元",
            period="2025",
            evidence=Evidence(
                side=ReportSide.A_SHARE,
                page=17,
                bbox=(0.0, 0.0, 1.0, 1.0),
                snippet="合并利润表 营业收入 126,411",
                section="pl",
            ),
            confidence=0.95,
        ),
        h_point=DataPoint(
            name=LocalizedString(zh="营业收入", en="Revenue"),
            canonical_key="revenue",
            value=33_086.0,
            value_text="33,086",
            unit="人民币百万元",
            period=None,
            evidence=Evidence(
                side=ReportSide.H_SHARE,
                page=35,
                bbox=(0.0, 0.0, 1.0, 1.0),
                snippet="二、本年度分季度经营指标 项目 一季度 营业收入 33,086",
                section="pl",
            ),
            confidence=0.95,
        ),
        alignment_confidence=0.95,
    )

    diffs = asyncio.run(run_standard_checks([pair]))

    assert not any(diff.canonical_key == "revenue" and diff.triage == "real" for diff in diffs)
    assert diffs == []


def test_numeric_checker_currency_factor_matches_expected_not_real() -> None:
    profile_a = _profile(
        ReportSide.A_SHARE,
        metrics=[
            _occ(_metric("total_assets", 367_718_196.0, ReportSide.A_SHARE, unit="人民币千元", snippet="资产总计 367,718,196")),
            _occ(_metric("gross_profit", 14_558_069.0, ReportSide.A_SHARE, unit="人民币千元", snippet="毛利 14,558,069")),
            _occ(_metric("interest_income", 2_845_113.0, ReportSide.A_SHARE, unit="人民币千元", snippet="利息收入 2,845,113")),
        ],
    )
    profile_h = _profile(
        ReportSide.H_SHARE,
        metrics=[
            _occ(_metric("total_assets", 52_271_308.0, ReportSide.H_SHARE, unit="人民币千元", snippet="Total assets 52,271,308")),
            _occ(_metric("gross_profit", 1_956_599.0, ReportSide.H_SHARE, unit="人民币千元", snippet="Gross profit 1,956,599")),
            _occ(_metric("interest_income", 398_080.0, ReportSide.H_SHARE, unit="人民币千元", snippet="Interest income 398,080")),
        ],
    )

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.triage == "real" for d in diffs)
    assert sum(1 for d in diffs if d.rule_id == "currency_converted_match" and d.triage == "expected") >= 2


def test_numeric_checker_rejects_shareholder_table_false_extraction() -> None:
    a_total_profit = _metric(
        "total_profit",
        100_000_000.0,
        ReportSide.A_SHARE,
        page=80,
        snippet="合并利润表 利润总额 100,000,000",
    )
    h_false_total_profit = _metric(
        "total_profit",
        99.0,
        ReportSide.H_SHARE,
        page=99,
        section="share_changes",
        snippet="前十名股东持股情况 基金名称 利润总额 99",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_total_profit)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_false_total_profit)])

    diffs = run_numeric_checks_on_profiles(profile_a, profile_h)

    assert not any(d.canonical_key == "total_profit" and d.triage == "real" for d in diffs)


def test_cross_page_event_match_generates_matched_coverage() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-event",
                page=30,
                bbox=(0, 0, 1, 1),
                text="2025年3月1日，本行与阳光科技有限公司签订战略合作协议，金额为100万元，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=160,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-event",
                page=100,
                bbox=(0, 0, 1, 1),
                text="2025年3月1日，本行与阳光科技有限公司签订战略合作协议，金额为100万元，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    matched = next(item for item in coverage if item.status == "matched")
    assert diffs == []
    assert matched.a_pages == [30]
    assert matched.h_pages == [100]
    assert matched.a_evidence[0].page == 30
    assert matched.h_evidence[0].page == 100


def test_event_entity_only_mismatch_stays_coverage_not_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-event",
                page=30,
                bbox=(0, 0, 1, 1),
                text="2025年5月1日，本行与阳光科技有限公司签订战略合作协议，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=160,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-event",
                page=100,
                bbox=(0, 0, 1, 1),
                text="2025年5月1日，本行与星河科技有限公司签订战略合作协议，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert any("主体" in item.note for item in coverage if item.status == "matched")


def test_cross_page_event_fact_mismatch_generates_real_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-event",
                page=30,
                bbox=(0, 0, 1, 1),
                text="2025年3月1日，本行与阳光科技有限公司签订战略合作协议，金额为100万元，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=160,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-event",
                page=100,
                bbox=(0, 0, 1, 1),
                text="2025年3月1日，本行与阳光科技有限公司签订战略合作协议，金额为120万元，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert any(item.status == "matched" for item in coverage)
    assert len(diffs) == 1
    assert diffs[0].triage == "real"
    assert diffs[0].rule_id == "event_fact_match"
    assert {ev.page for ev in diffs[0].evidence} == {30, 100}


def test_dividend_amount_mentions_keep_structured_roles() -> None:
    text = (
        "本公司董事会于2021年3月30日提议向全体股东派发现金股利，"
        "以本公司股本总额25,039,945千股为基数，向股东分派现金股利每10股人民币1.00元（含税），"
        "共计股利人民币2,503,994千元。"
    )
    mentions = _amount_mentions(_normalize_text(text))

    by_role = {mention.role: mention.value for mention in mentions}
    assert by_role["dividend_base_share_count"] == 25_039_945_000
    assert by_role["dividend_rate_per_10_shares"] == 1.0
    assert by_role["dividend_total"] == 2_503_994_000


def test_english_dividend_amounting_without_currency_space_is_total_dividend() -> None:
    text = (
        "the board proposed to distribute cash dividends of RMB1.00 (tax inclusive) per 10 shares "
        "to shareholders based on the total outstanding shares of 25,039,945 thousand shares, "
        "with total dividends amounting to RMB25,039,945 thousand."
    )
    mentions = _amount_mentions(_normalize_text(text))

    by_role = {mention.role: mention.value for mention in mentions}
    assert by_role["dividend_rate_per_10_shares"] == 1.0
    assert by_role["dividend_base_share_count"] == 25_039_945_000
    assert by_role["dividend_total"] == 25_039_945_000


def test_dividend_profit_distribution_total_mismatch_generates_real_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=455,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-dividend-after-year-end",
                page=453,
                bbox=(0, 0, 1, 1),
                text=(
                    "63 截至2020年12月31日止年度后的非调整事项 (1) 利润分配 "
                    "本公司董事会于2021年3月30日提议向全体股东派发现金股利，"
                    "以本公司股本总额25,039,945千股为基数，向股东分派现金股利每10股人民币1.00元（含税），"
                    "共计股利人民币2,503,994千元，此项提议尚待股东于应届年度股东大会上批准。"
                ),
                language=Language.ZH,
                section="notes",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=454,
        primary_language=Language.EN,
        texts=[
            TextSegment(
                segment_id="h-dividend-after-year-end",
                page=453,
                bbox=(0, 0, 1, 1),
                text=(
                    "63 Non-adjusting events after the year ended 31 December 2020 "
                    "(1) Profit distribution Pursuant to the resolution of the Board meeting dated 30 March 2021, "
                    "the Board proposed to distribute cash dividends of RMB1.00 (tax inclusive) per 10 shares "
                    "to shareholders based on the total outstanding shares of 25,039,945 thousand shares, "
                    "with total dividends amounting to RMB25,039,945 thousand. "
                    "The proposal is subject to the approval of the shareholders in the forthcoming annual general meeting."
                ),
                language=Language.EN,
                section="notes",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert any(item.status == "matched" for item in coverage)
    assert len(diffs) == 1
    assert diffs[0].triage == "real"
    assert diffs[0].rule_id == "event_fact_match"
    assert {ev.page for ev in diffs[0].evidence} == {453}
    assert "金额" in diffs[0].summary.zh or "amount" in diffs[0].summary.en.lower()
    assert diffs[0].diff_explanation is not None
    assert "利润分配" in diffs[0].diff_explanation.headline
    dividend_item = next(item for item in diffs[0].diff_explanation.items if item.role == "dividend_total")
    assert dividend_item.label == "股利总额"
    assert dividend_item.a_value == 2_503_994_000
    assert dividend_item.h_value == 25_039_945_000
    assert dividend_item.a_page == 453
    assert dividend_item.h_page == 453
    assert "2,503,994" in dividend_item.a_snippet
    assert "25,039,945" in dividend_item.h_snippet


def test_dividend_amount_mismatch_survives_combined_english_note_events() -> None:
    a_text = (
        "451 年度报告 2020 申万宏源集团股份有限公司 "
        "（除另有注明外，以人民币千元列示） 合并财务报表附注（续） "
        "63 截至2020年12月31日止年度后的非调整事项 (1) 利润分配 "
        "本公司董事会于2021年3月30日提议向全体股东派发现金股利，"
        "以本公司股本总额25,039,945千股为基数，向股东分派现金股利每10股人民币1.00元（含税），"
        "共计股利人民币2,503,994千元，此项提议尚待股东于应届年度股东大会上批准。"
        "(2) 发行长期债券、短期债券及收益凭证 自2021年1月1日起至报告日，"
        "本集团发行长期公司债券、短期债券及多项收益凭证，发行金额合计约为人民币310亿元。"
    )
    h_text = (
        "63 Non-adjusting events after the year ended 31 December 2020 "
        "(1) Profit distribution Pursuant to the resolution of the Board dated 30 March 2021, "
        "the Board proposed to distribute cash dividends of RMB1.00 (tax inclusive) per 10 shares "
        "to shareholders based on the total outstanding shares of 25,039,945 thousand shares, "
        "with total dividends amounting to RMB25,039,945 thousand. "
        "The proposal is subject to the approval of the shareholders in the forthcoming annual general meeting. "
        "(2) Issuance of long-term bonds, short-term bonds and structured notes "
        "From 1 January 2021 to the reporting date, the Group issued long-term corporate bond, "
        "short-term bonds and a number of structured notes. The issuance amount was approximate RMB31 billion in total. "
        "(3) Repayment of short-term bonds and structured notes "
        "From 1 January 2021 to the reporting date, the Group repaid short-term bond and a number of structured notes. "
        "The repayment amount was approximate RMB27.1 billion in total."
    )
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text)

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert any(item.status == "matched" for item in coverage)
    assert any(diff.triage == "real" and "金额/数量" in diff.summary.zh for diff in diffs)
    explained = [diff for diff in diffs if diff.diff_explanation and "利润分配" in diff.diff_explanation.headline]
    assert explained
    assert any(item.role == "dividend_total" for item in explained[0].diff_explanation.items)


def test_structured_high_value_amount_domains_generate_real_diffs() -> None:
    scenarios = [
        (
            "债券发行",
            "期后事项：本集团发行长期公司债券，发行金额合计约为人民币310亿元。",
            "Subsequent event: the Group issued long-term corporate bonds with issuance amount of approximately RMB32 billion.",
        ),
        (
            "重大诉讼",
            "重大诉讼：本公司涉及一起合同纠纷，涉案金额为人民币100万元。",
            "Material litigation: the Company was involved in a contract dispute with claim amount of RMB1.2 million.",
        ),
        (
            "担保承诺",
            "担保事项：本集团对外担保金额为人民币100万元。",
            "Guarantee commitment: the Group provided external guarantees with guarantee amount of RMB1.2 million.",
        ),
        (
            "关联交易",
            "关联交易：本集团向关联方采购服务，交易金额为人民币100万元。",
            "Related party transaction: the Group purchased services from a related party with transaction amount of RMB1.2 million.",
        ),
        (
            "股份变动",
            "股份变动：截至报告日，公司总股本为25,039,945千股。",
            "Share change: as of the reporting date, total share capital was 25,039,946 thousand shares.",
        ),
    ]

    for label, a_text, h_text in scenarios:
        profile_a, profile_h = _profiles_with_event_texts(a_text, h_text)

        coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

        assert any(item.status == "matched" for item in coverage), label
        assert len(diffs) == 1, label
        assert diffs[0].triage == "real", label
        assert diffs[0].rule_id == "event_fact_match", label


def test_zhongxin_governance_status_false_match_stays_out_of_real_diffs() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=445,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-governance",
                page=152,
                bbox=(0, 0, 1, 1),
                text=(
                    "第三章 公司治理、环境和社会 本行从落实战略发展规划和推动年度经营目标完成情况等方面"
                    "对高级管理人员进行业绩考核评价。报告期内，本行监事会依据董事和监事在忠实履职、"
                    "勤勉履职、履职专业性、履职独立性与道德水准、履职合规性等方面的表现开展年度履职评价。"
                ),
                language=Language.ZH,
                section="corporate_governance",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=335,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-governance",
                page=136,
                bbox=(0, 0, 1, 1),
                text=(
                    "第三章 公司治理、环境和社会 中信银行股份有限公司 134 本行为同时是本行员工的董事、"
                    "监事和高级管理人员提供其职位相应的报酬，包括工资、奖金、津贴补贴、职工福利费和"
                    "各项保险金、住房公积金及年金。"
                ),
                language=Language.ZH,
                section="corporate_governance",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert coverage
    assert not any(item.note.startswith("事件双边披露，存在事实差异") for item in coverage)


def test_status_only_event_mismatch_without_identity_anchors_is_not_real_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-status",
                page=30,
                bbox=(0, 0, 1, 1),
                text="报告期内，本行签订合作协议，项目状态为已完成。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=160,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-status",
                page=100,
                bbox=(0, 0, 1, 1),
                text="报告期内，本行签订合作协议，项目状态为进行中。",
                language=Language.ZH,
                section="significant_events",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_huatai_layout_overview_page_stays_out_of_real_diffs() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=384,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-layout",
                page=19,
                bbox=(0, 0, 1, 1),
                text=(
                    "全面助力 深度服务 专业赋能 中国企业 机构客户 个人客户 家一级子公司 家参股公司 "
                    "全球拓展布局 全球投资交易 全球资产配置 香港 Hong Kong 家证券营业部 家证券分公司 "
                    "华泰国际作为公司国际业务控股平台，始终坚守投资银行服务本源，致力服务金融高水平开放。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=390,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-layout",
                page=22,
                bbox=(0, 0, 1, 1),
                text=(
                    "深度服务 机构客户 全球投资交易 全面助力 中国企业 全球拓展布局 专业赋能 个人客户 全球资产配置 "
                    "华泰国际作为公司国际业务控股平台，始终坚守投资银行服务本源，致力服务金融高水平开放。"
                    "香港 Hong Kong 2024年9月，华泰国际旗下业务稳步推进。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_huatai_mda_operational_description_stays_out_of_real_diffs() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=384,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-mda",
                page=51,
                bbox=(0, 0, 1, 1),
                text=(
                    "创新业务方面，探索构建AI驱动的产业链和主体供应链图谱。优化运营方面，升级投行智能审核体系，"
                    "完成字段材料一致性智能审核的系统建设，支持高效信息抽取、智能核查校验、自动意见生成等功能。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=390,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-mda",
                page=53,
                bbox=(0, 0, 1, 1),
                text=(
                    "优化运营方面，升级投行智能审核体系，完成字段材料一致性智能审核的系统建设，支持高效信息抽取、"
                    "智能核查校验、自动意见生成等功能。赋能员工方面，不断升级拓展华泰办公数字员工助手。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_huatai_near_duplicate_equity_transfer_date_stays_coverage_not_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=384,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-transfer",
                page=60,
                bbox=(0, 0, 1, 1),
                text=(
                    "2024年度，公司第六届董事会第十四次会议审议通过了《关于转让江苏股权交易中心有限责任公司20%股权的议案》，"
                    "同意公司向江苏金财投资有限公司转让所持江苏股权交易中心20%股权，并授权公司经营管理层办理相关事项。"
                    "报告期内，江苏股权交易中心完成股权变更事项的工商变更登记手续。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=390,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-transfer",
                page=63,
                bbox=(0, 0, 1, 1),
                text=(
                    "2024 年度，公司第六届董事会第十四次会议审议通过了《关于转让江苏股权交易中心有限责任公司20%股权的议案》，"
                    "同意公司向江苏金财投资有限公司转让所持江苏股权交易中心20%股权，并授权公司经营管理层办理相关事项。"
                    "报告期内，江苏股权交易中心完成股权变更事项的工商变更登记手续。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_huatai_board_composition_with_h_supplement_stays_out_of_real_diffs() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=384,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-board",
                page=79,
                bbox=(0, 0, 1, 1),
                text=(
                    "截至报告期末，本公司董事会人员构成：年龄组别2名4名5名2名，董事类别3名5名5名，"
                    "执行董事、非执行董事、独立非执行董事，女性董事1名，男性董事12名。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=390,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-board",
                page=85,
                bbox=(0, 0, 1, 1),
                text=(
                    "截至报告期末，本公司董事会人员构成：年龄组别、董事类别、执行董事、非执行董事、独立非执行董事。"
                    "本公司执行董事尹立鸿女士于2025年3月14日辞任后，本公司已根据相关规定物色适当人选出任董事。"
                ),
                language=Language.ZH,
                section=None,
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_template_disclosure_amount_mismatch_stays_coverage_not_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-template",
                page=166,
                bbox=(0, 0, 1, 1),
                text="重要事项 □适用 √不适用 七、股份限制减持情况说明，金额为100万元。",
                language=Language.ZH,
                section="share_changes",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=180,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-template",
                page=278,
                bbox=(0, 0, 1, 1),
                text="重要事项 □ 适用 √ 不适用 七、股份限制减持情况说明，金额为120万元。",
                language=Language.ZH,
                section="share_changes",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert any(item.status == "matched" for item in coverage)


def test_financial_statement_event_candidate_stays_coverage_not_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-equity",
                page=210,
                bbox=(0, 0, 1, 1),
                text="合并所有者权益变动表 2025年度 金额单位：人民币元 重大承诺 股本 100元 资本公积 200元 未分配利润 300元。",
                language=Language.ZH,
                section="equity",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=180,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-equity",
                page=358,
                bbox=(0, 0, 1, 1),
                text="合并所有者权益变动表 2025年度 金额单位：人民币元 重大承诺 股本 120元 资本公积 200元 未分配利润 300元。",
                language=Language.ZH,
                section="equity",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_generic_amount_role_event_mismatch_stays_coverage_not_diff() -> None:
    a_text = "On 31 December 2020, the Company completed an ordinary cooperation project with amount of RMB1.0 million."
    h_text = "On 31 December 2020, the Company completed an ordinary cooperation project with amount of RMB1.2 million."
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="significant_events")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert any(item.status == "matched" for item in coverage)


def test_bond_status_wording_only_mismatch_stays_coverage_not_diff() -> None:
    a_text = "On 31 December 2020, the Group issued short-term bonds with amount of RMB40 billion."
    h_text = "On 31 December 2020, the Group proposed short-term bonds with amount of RMB40 billion."
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert any(item.status == "matched" for item in coverage)


def test_generic_percentage_event_mismatch_stays_coverage_not_diff() -> None:
    a_text = "On 31 December 2020, the Group disclosed a general financing update with ratio of 3.45%."
    h_text = "On 31 December 2020, the Group disclosed a general financing update with ratio of 5.60%."
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []


def test_different_percentage_roles_stay_coverage_not_diff() -> None:
    a_text = "Bond issuance: 2019 corporate bond issued with coupon rate of 3.45%."
    h_text = "Bond issuance: 2019 corporate bond issued with asset ratio of 5.60%."
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert any(item.status == "matched" for item in coverage)


def test_same_percentage_role_and_event_anchor_can_be_real_diff() -> None:
    a_text = "Bond issuance: 2019 corporate bond phase one issued with coupon rate of 3.45%."
    h_text = "Bond issuance: 2019 corporate bond phase one issued with coupon rate of 3.70%."
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert len(diffs) == 1
    assert diffs[0].rule_id == "event_fact_match"
    assert diffs[0].diff_explanation is not None
    assert any(item.role == "coupon_rate" for item in diffs[0].diff_explanation.items)


def test_financial_statement_percentage_event_stays_coverage_not_diff() -> None:
    a_text = (
        "Bond issuance: 2016 corporate bonds were issued with coupon rates of 3.45% and 3.70%; "
        "as at 31 December 2019 the bonds had been partially redeemed."
    )
    h_text = (
        "Notes to the consolidated financial statements 37 Cash and bank balances: "
        "restricted deposits carried interest rates of 0.90%, 1.80%, 2.85%, 3.22%, 3.92%, 5.00% and 5.60%."
    )
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="financial_statements")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert coverage


def test_shenwan_hongyuan_event_false_positives_stay_coverage_but_dividend_total_remains_real() -> None:
    scenarios = [
        (
            "bond_table_vs_note",
            "Section IV Report of the Board, unit RMB thousand: non-current liabilities, long-term bonds 97,533,336 and employee benefits 3,044,380.",
            "Notes to the consolidated financial statements Interest in associates and joint venture, long-term bonds RMB97,533,336 thousand.",
            "financial_statements",
            0,
        ),
        (
            "bond_status",
            "On 31 December 2020, the Group issued short-term bonds with amount of RMB40 billion.",
            "On 31 December 2020, the Group proposed short-term bonds with amount of RMB40 billion.",
            "notes",
            0,
        ),
        (
            "dividend_2020_real",
            "Profit distribution 2020: cash dividends of RMB1.00 per 10 shares with total dividends amounting to RMB2,503,994 thousand.",
            "Profit distribution: cash dividends of RMB1.00 per 10 shares with total dividends amounting to RMB25,039,945 thousand.",
            "notes",
            1,
        ),
        (
            "historical_dividend",
            "2019 profit distribution: cash dividends of RMB0.80 per 10 shares with total dividends amounting to RMB2,003,195 thousand.",
            "Reserves and retained profits: dividends recognised for the year ended 31 December 2019 amounted to RMB2,003,195 thousand.",
            "notes",
            0,
        ),
        (
            "litigation_generic_amount",
            "Material litigation: On September 21, 2020, Hongyuan Huifu received judgment requiring compensation of RMB10 million and equity transfer payment of RMB72,465,232.19.",
            "Material litigation: On September 21, 2020, Hongyuan Huifu received judgment requiring compensation of RMB10 million and equity transfer payment of RMB72,465,232.19.",
            "significant_events",
            0,
        ),
        (
            "short_term_debt_status",
            "Short-term debt instruments: short-term corporate bonds issued 50,054,564 and repaid 35,977,059; structured notes issued 53,636,766 and repaid 44,475,172.",
            (
                "Short-term debt instruments issued: short-term corporate bonds issued 50,054,564 and repaid 35,977,059; "
                "structured notes issued 53,636,766 and repaid 44,475,172."
            ),
            "financial_statements",
            0,
        ),
    ]

    for label, a_text, h_text, section, expected_real_count in scenarios:
        profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section=section)
        coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

        assert len(diffs) == expected_real_count, label
        assert coverage or expected_real_count, label
        if expected_real_count:
            assert diffs[0].diff_explanation is not None, label
            assert any(item.role == "dividend_total" for item in diffs[0].diff_explanation.items), label


def test_citic_ipo_history_scrambled_amount_stays_coverage_not_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=220,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-ipo",
                page=14,
                bbox=(0, 0, 1, 1),
                text=(
                    "1995 1999 2000 2002 12 A 年 月，中信集团发行 40,000 2003 1 万股。"
                    "募集资金人民币18亿元，总股数增至248,150万股，持股比例31.75%。"
                ),
                language=Language.ZH,
                section="capital_reserve",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=230,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-ipo",
                page=10,
                bbox=(0, 0, 1, 1),
                text=(
                    "2002年12月，中信集团发行40,000万股，2003年1月募集资金人民币18亿元，"
                    "总股数增至248,150万股，持股比例31.75%。"
                ),
                language=Language.ZH,
                section="capital_reserve",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert coverage


def test_citic_overseas_equity_project_scrambled_amount_stays_coverage_not_diff() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=220,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-overseas",
                page=28,
                bbox=(0, 0, 1, 1),
                text="2025 91 79.11 年，公司完成 单境外股权项目，IPO 项目 51 单，承销规模 32 亿美元。",
                language=Language.ZH,
                section="bonds",
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=230,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-overseas",
                page=33,
                bbox=(0, 0, 1, 1),
                text="2025年，公司完成91单境外股权项目，其中IPO项目51单，承销规模79.11亿美元。",
                language=Language.ZH,
                section="bonds",
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert diffs == []
    assert coverage


def test_branch_table_batch_mismatch_with_stable_rows_requires_review() -> None:
    names = [
        "北京分行",
        "上海分行",
        "广州分行",
        "深圳分行",
        "天津分行",
        "重庆分行",
        "成都分行",
        "杭州分行",
        "南京分行",
        "武汉分行",
        "长沙分行",
        "西安分行",
    ]
    a_rows = " ".join(f"{name} 10 {100000 + idx * 1000:,}" for idx, name in enumerate(names))
    h_rows = " ".join(f"{name} 10 {200000 + idx * 1000:,}" for idx, name in enumerate(names))
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[TextSegment(segment_id="a-branch", page=30, bbox=(0, 0, 1, 1), text=a_rows, language=Language.ZH)],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[TextSegment(segment_id="h-branch", page=31, bbox=(0, 0, 1, 1), text=h_rows, language=Language.ZH)],
    )

    diffs = compare_branch_tables(a_doc, h_doc)

    assert len(diffs) == len(names)
    assert all(diff.triage == "real" for diff in diffs)
    assert all(diff.rule_id == "branch_asset_scale_match" for diff in diffs)


def test_branch_table_clear_single_row_mismatch_requires_review() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-branch",
                page=30,
                bbox=(0, 0, 1, 1),
                text="北京分行 10 100,000 上海分行 8 80,000 广州分行 6 60,000",
                language=Language.ZH,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-branch",
                page=31,
                bbox=(0, 0, 1, 1),
                text="北京分行 10 120,000 上海分行 8 80,000 广州分行 6 60,000",
                language=Language.ZH,
            )
        ],
    )

    diffs = compare_branch_tables(a_doc, h_doc)

    assert len(diffs) == 1
    assert diffs[0].triage == "real"
    assert diffs[0].rule_id == "branch_asset_scale_match"
    assert "北京分行" in diffs[0].topic.zh


def test_branch_table_count_mismatch_stays_out_of_real_diffs() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-branch",
                page=30,
                bbox=(0, 0, 1, 1),
                text="北京分行 10 100,000 上海分行 8 80,000 广州分行 6 60,000",
                language=Language.ZH,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-branch",
                page=31,
                bbox=(0, 0, 1, 1),
                text="北京分行 11 120,000 上海分行 8 80,000 广州分行 6 60,000",
                language=Language.ZH,
            )
        ],
    )

    diffs = compare_branch_tables(a_doc, h_doc)

    assert diffs == []


def _branch_table_from_rows(table_id: str, page: int, rows: list[tuple[str, int, str]]) -> FinancialTable:
    cells = [
        TableCell(row=0, col=0, text="分行名称", is_header=True),
        TableCell(row=0, col=1, text="机构数量", is_header=True),
        TableCell(row=0, col=2, text="资产规模", is_header=True),
    ]
    for row_idx, (name, count, asset) in enumerate(rows, start=1):
        cells.extend(
            [
                TableCell(row=row_idx, col=0, text=name),
                TableCell(row=row_idx, col=1, text=str(count)),
                TableCell(row=row_idx, col=2, text=asset),
            ]
        )
    return FinancialTable(
        table_id=table_id,
        title=LocalizedString(zh="分支机构情况表"),
        page=page,
        bbox=(0.0, 0.0, 1.0, 1.0),
        cells=cells,
        unit="人民币百万元",
    )


def test_branch_table_fallback_reconstructs_rows_from_structured_table_cells() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[],
        tables=[
            _branch_table_from_rows(
                "A_branch",
                30,
                [
                    ("北京分行", 10, "100,000"),
                    ("上海分行", 8, "80,000"),
                    ("广州分行", 6, "60,000"),
                ],
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[],
        tables=[
            _branch_table_from_rows(
                "H_branch",
                31,
                [
                    ("北京分行", 10, "120,000"),
                    ("上海分行", 8, "80,000"),
                    ("广州分行", 6, "60,000"),
                ],
            )
        ],
    )

    diffs = compare_branch_tables(a_doc, h_doc)

    assert len(diffs) == 1
    assert diffs[0].triage == "real"
    assert diffs[0].rule_id == "branch_asset_scale_match"
    assert "北京分行" in diffs[0].topic.zh
    assert diffs[0].evidence[0].snippet == "北京分行 10 100,000"


def test_branch_table_fallback_count_mismatch_stays_out_of_real_diffs() -> None:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[],
        tables=[
            _branch_table_from_rows(
                "A_branch",
                30,
                [
                    ("北京分行", 10, "100,000"),
                    ("上海分行", 8, "80,000"),
                    ("广州分行", 6, "60,000"),
                ],
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[],
        tables=[
            _branch_table_from_rows(
                "H_branch",
                31,
                [
                    ("北京分行", 11, "120,000"),
                    ("上海分行", 8, "80,000"),
                    ("广州分行", 6, "60,000"),
                ],
            )
        ],
    )

    diffs = compare_branch_tables(a_doc, h_doc)

    assert diffs == []


def test_branch_table_matches_traditional_names_without_opencc(monkeypatch) -> None:
    from ahcc.align import glossary as glossary_module

    monkeypatch.setattr(glossary_module, "_OPENCC_CONVERTER_T2S", False)

    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-branch",
                page=30,
                bbox=(0, 0, 1, 1),
                text=(
                    "广州分行 10 100,000 乌鲁木齐分行 8 80,000 沈阳分行 6 60,000 "
                    "长沙分行 4 40,000 大连分行 3 30,000 拉萨分行 2 20,000 "
                    "无锡分行 5 50,000 卢森堡分行 1 10,000 石家庄分行 7 70,000 "
                    "青岛分行 9 90,000 黑龙江分行 11 110,000"
                ),
                language=Language.ZH,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=10,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-branch",
                page=31,
                bbox=(0, 0, 1, 1),
                text=(
                    "廣州分行 10 120,000 烏魯木齊分行 8 90,000 瀋陽分行 6 70,000 "
                    "長沙分行 4 50,000 大連分行 3 40,000 拉薩分行 2 30,000 "
                    "無錫分行 5 60,000 盧森堡分行 1 20,000 石家莊分行 7 80,000 "
                    "青島分行 9 100,000 黑龍江分行 11 120,000"
                ),
                language=Language.ZH,
            )
        ],
    )

    diffs = compare_branch_tables(a_doc, h_doc)

    assert len(diffs) == 11
    assert {diff.diff_id for diff in diffs} == {
        "BRANCH_广州分行",
        "BRANCH_乌鲁木齐分行",
        "BRANCH_沈阳分行",
        "BRANCH_长沙分行",
        "BRANCH_大连分行",
        "BRANCH_拉萨分行",
        "BRANCH_无锡分行",
        "BRANCH_卢森堡分行",
        "BRANCH_石家庄分行",
        "BRANCH_青岛分行",
        "BRANCH_黑龙江分行",
    }


def test_comparison_summary_exposes_branch_diagnostics_and_file_hashes() -> None:
    import hashlib
    import shutil
    from pathlib import Path
    from uuid import uuid4

    from ahcc.orchestrator import Orchestrator

    work_dir = Path("storage") / "test-artifacts" / f"branch-diagnostics-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    a_file = work_dir / "a.pdf"
    h_file = work_dir / "h.pdf"
    a_payload = b"A report bytes"
    h_payload = b"H report bytes"
    try:
        a_file.write_bytes(a_payload)
        h_file.write_bytes(h_payload)

        a_doc = ReportDocument(
            doc_id="A",
            side=ReportSide.A_SHARE,
            file_path=str(a_file),
            total_pages=10,
            primary_language=Language.ZH,
            texts=[],
            tables=[
                _branch_table_from_rows(
                    "A_branch",
                    30,
                    [
                        ("北京分行", 10, "100,000"),
                        ("上海分行", 8, "80,000"),
                        ("广州分行", 6, "60,000"),
                    ],
                )
            ],
            metadata={"parser_cache": {"hit": False, "key": "a-cache-key"}},
        )
        h_doc = ReportDocument(
            doc_id="H",
            side=ReportSide.H_SHARE,
            file_path=str(h_file),
            total_pages=10,
            primary_language=Language.ZH,
            texts=[],
            tables=[
                _branch_table_from_rows(
                    "H_branch",
                    31,
                    [
                        ("北京分行", 10, "120,000"),
                        ("上海分行", 8, "80,000"),
                        ("广州分行", 6, "60,000"),
                    ],
                )
            ],
            metadata={"parser_cache": {"hit": True, "key": "h-cache-key"}},
        )
        profile_a = _profile(ReportSide.A_SHARE)
        profile_h = _profile(ReportSide.H_SHARE)
        profile_a.source_doc = a_doc
        profile_h.source_doc = h_doc
        profile_a.metadata = a_doc.metadata
        profile_h.metadata = h_doc.metadata
        branch_diffs = compare_branch_tables(a_doc, h_doc)
        profile_a.metrics = [_occ(_metric("revenue", 126_411.0, ReportSide.A_SHARE, page=5, unit=None))]
        profile_h.metrics = [_occ(_metric("revenue", 126_460.0, ReportSide.H_SHARE, page=6, unit=None))]
        tamper_diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])
        job = Job(
            job_id="branch-diag",
            company_name="Branch Diagnostics",
            a_file=str(a_file),
            h_file=str(h_file),
            diffs=[*branch_diffs, *tamper_diffs],
        )

        summary = Orchestrator()._build_comparison_summary(job, profile_a, profile_h, module_warnings=[])

        assert summary["a_file_sha256"] == hashlib.sha256(a_payload).hexdigest()
        assert summary["h_file_sha256"] == hashlib.sha256(h_payload).hexdigest()
        assert summary["branch_source_doc_available"] is True
        assert summary["a_branch_count"] == 3
        assert summary["h_branch_count"] == 3
        assert summary["matched_branch_count"] == 3
        assert summary["branch_diff_count"] == 1
        assert summary["branch_alignment_ratio"] == 1.0
        assert summary["key_metric_exact_diff_count"] == 1
        assert summary["visual_text_layer_mismatch_count"] == 0
        assert summary["parser_cache_hit"] is False
        assert summary["parser_cache"]["a"]["key"] == "a-cache-key"
        assert summary["parser_cache"]["h"]["key"] == "h-cache-key"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_compare_profiles_reports_internal_consistency() -> None:
    first = _metric("total_assets", 1000.0, ReportSide.A_SHARE, page=1)
    second = _metric("total_assets", 1300.0, ReportSide.A_SHARE, page=1)
    occ = MetricOccurrences(
        canonical_key="total_assets",
        name=first.name,
        primary=first,
        all_occurrences=[first, second],
        is_internally_consistent=False,
    )
    from ahcc.profile.models import InternalInconsistency

    occ.internal_inconsistencies.append(
        InternalInconsistency(item_a=first, item_b=second, delta=300.0, delta_pct=23.1)
    )

    diffs = compare_profiles(_profile(ReportSide.A_SHARE, metrics=[occ]), _profile(ReportSide.H_SHARE))

    assert any(d.diff_type == "internal_inconsistency" and d.triage == "real" for d in diffs)


def test_disclosure_check_marks_internal_consistency_scope_by_report_side() -> None:
    from ahcc.profile.models import InternalInconsistency

    a_first = _metric("revenue", 100.0, ReportSide.A_SHARE, page=10)
    a_second = _metric("revenue", 2000.0, ReportSide.A_SHARE, page=100)
    a_occ = MetricOccurrences(
        canonical_key="revenue",
        name=a_first.name,
        primary=a_first,
        all_occurrences=[a_first, a_second],
        is_internally_consistent=False,
    )
    a_occ.internal_inconsistencies.append(
        InternalInconsistency(item_a=a_first, item_b=a_second, delta=1900.0, delta_pct=95.0)
    )

    h_first = _metric("revenue", 300.0, ReportSide.H_SHARE, page=8)
    h_second = _metric("revenue", 900.0, ReportSide.H_SHARE, page=88)
    h_occ = MetricOccurrences(
        canonical_key="revenue",
        name=h_first.name,
        primary=h_first,
        all_occurrences=[h_first, h_second],
        is_internally_consistent=False,
    )
    h_occ.internal_inconsistencies.append(
        InternalInconsistency(item_a=h_first, item_b=h_second, delta=600.0, delta_pct=66.7)
    )

    diffs = asyncio.run(
        run_disclosure_checks_on_profiles(
            _profile(ReportSide.A_SHARE, metrics=[a_occ]),
            _profile(ReportSide.H_SHARE, metrics=[h_occ]),
        )
    )

    assert any(diff.diff_type == DiffType.INTERNAL and diff.diff_scope == DiffScope.A_INTERNAL for diff in diffs)
    assert any(diff.diff_type == DiffType.INTERNAL and diff.diff_scope == DiffScope.H_INTERNAL for diff in diffs)
    a_internal_diff = next(diff for diff in diffs if diff.diff_scope == DiffScope.A_INTERNAL)
    assert a_internal_diff.diff_explanation is not None
    assert a_internal_diff.diff_explanation.items[0].a_value == 100.0
    assert a_internal_diff.diff_explanation.items[0].h_value == 2000.0
    assert a_internal_diff.diff_explanation.items[0].a_page == 10
    assert a_internal_diff.diff_explanation.items[0].h_page == 100


def test_comparison_summary_groups_diffs_by_triage_and_scope() -> None:
    from ahcc.orchestrator import Orchestrator

    cross = Diff(
        diff_id="cross-real",
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.HIGH,
        triage="real",
        diff_scope=DiffScope.CROSS_REPORT,
        topic=LocalizedString(zh="Revenue", en="Revenue"),
        summary=LocalizedString(zh="A/H revenue mismatch", en="A/H revenue mismatch"),
    )
    a_internal = Diff(
        diff_id="a-internal-unresolved",
        diff_type=DiffType.INTERNAL,
        severity=DiffSeverity.MEDIUM,
        triage="unresolved",
        diff_scope=DiffScope.A_INTERNAL,
        topic=LocalizedString(zh="Revenue", en="Revenue"),
        summary=LocalizedString(zh="A-share internal revenue mismatch", en="A-share internal revenue mismatch"),
    )
    h_internal = Diff(
        diff_id="h-internal-expected",
        diff_type=DiffType.INTERNAL,
        severity=DiffSeverity.INFO,
        triage="expected",
        diff_scope=DiffScope.H_INTERNAL,
        topic=LocalizedString(zh="Revenue", en="Revenue"),
        summary=LocalizedString(zh="H-share internal expected mismatch", en="H-share internal expected mismatch"),
    )
    a_internal_event = Diff(
        diff_id="a-internal-event",
        diff_type=DiffType.INTERNAL,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        diff_scope=DiffScope.A_INTERNAL,
        topic=LocalizedString(zh="战略合作协议", en="Strategic cooperation agreement"),
        summary=LocalizedString(zh="A-share internal event mismatch", en="A-share internal event mismatch"),
        rule_id="event_internal_fact_match",
    )
    llm_downgraded = Diff(
        diff_id="llm-semantic",
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.INFO,
        triage="unresolved",
        diff_scope=DiffScope.CROSS_REPORT,
        topic=LocalizedString(zh="营业收入", en="Revenue"),
        summary=LocalizedString(zh="DeepSeek 语义审阅降级", en="DeepSeek semantic review downgraded"),
        rule_id="llm_semantic_review",
    )
    job = Job(
        job_id="scope-counts",
        a_file="a.pdf",
        h_file="h.pdf",
        diffs=[cross, a_internal, h_internal, a_internal_event, llm_downgraded],
    )

    summary = Orchestrator()._build_comparison_summary(
        job,
        _profile(ReportSide.A_SHARE),
        _profile(ReportSide.H_SHARE),
        module_warnings=[],
    )

    assert summary["diff_scope_counts"] == {
        "real": {"cross_report": 1, "a_internal": 1, "h_internal": 0},
        "unresolved": {"cross_report": 1, "a_internal": 1, "h_internal": 0},
        "expected": {"cross_report": 0, "a_internal": 0, "h_internal": 1},
    }
    assert summary["a_internal_diff_count"] == 2
    assert summary["h_internal_diff_count"] == 1
    assert summary["cross_report_diff_count"] == 2
    assert summary["internal_event_diff_count"] == 1
    assert summary["llm_semantic_review_count"] == 1


def test_comparison_summary_infers_legacy_internal_scope_from_evidence_side() -> None:
    from ahcc.orchestrator import Orchestrator

    legacy_internal = Diff(
        diff_id="legacy-internal",
        diff_type=DiffType.INTERNAL,
        severity=DiffSeverity.HIGH,
        triage="real",
        topic=LocalizedString(zh="Revenue", en="Revenue"),
        summary=LocalizedString(zh="legacy internal mismatch", en="legacy internal mismatch"),
        evidence=[
            _evidence(ReportSide.A_SHARE, 10, "Revenue 100"),
            _evidence(ReportSide.A_SHARE, 100, "Revenue 2000"),
        ],
    )

    counts = Orchestrator._diff_scope_counts([legacy_internal])

    assert counts["real"]["a_internal"] == 1
    assert counts["real"]["cross_report"] == 0


def test_key_metric_exact_checker_keeps_tiny_front_table_mismatch_as_review_candidate() -> None:
    a_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="Revenue 126,411",
    )
    h_revenue = _metric(
        "revenue",
        126_460.0,
        ReportSide.H_SHARE,
        page=19,
        unit=None,
        section="key_metrics",
        snippet="Revenue 126,460",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])

    exact = next(diff for diff in diffs if diff.rule_id == "key_metric_exact_mismatch")
    assert exact.triage == "unresolved"
    assert exact.diff_scope == DiffScope.CROSS_REPORT
    assert exact.canonical_key == "revenue"
    assert exact.delta == 49.0


def test_key_metric_exact_checker_skips_quarterly_metric_against_annual_metric() -> None:
    a_annual_revenue = _metric(
        "revenue",
        33_100.0,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="主要会计数据 营业收入 33,100",
        period="2025",
    )
    h_quarter_revenue = _metric(
        "revenue",
        33_086.0,
        ReportSide.H_SHARE,
        page=35,
        unit=None,
        section="key_metrics",
        snippet="二、本年度分季度经营指标 项目 一季度 营业收入 33,086",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_annual_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_quarter_revenue)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])

    assert not any(diff.rule_id == "key_metric_exact_mismatch" and diff.triage == "real" for diff in diffs)


def test_key_metric_exact_checker_skips_compact_q4_metric_against_annual_metric() -> None:
    a_annual_revenue = _metric(
        "revenue",
        32_137_830_111.0,
        ReportSide.A_SHARE,
        page=5,
        unit=None,
        section="key_metrics",
        snippet="主要会计数据 营业收入 32,137,830,111",
        period="2024",
    )
    h_q4_revenue = _metric(
        "revenue",
        32_137_839_111.0,
        ReportSide.H_SHARE,
        page=6,
        unit=None,
        section="key_metrics",
        snippet="P6 Q4营业收入 32,137,839,111",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_annual_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_q4_revenue)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])

    assert not any(diff.rule_id == "key_metric_exact_mismatch" and diff.triage == "real" for diff in diffs)


def test_key_metric_visual_reference_error_does_not_become_cross_report_real() -> None:
    a_text_revenue = _metric(
        "revenue",
        32_137_830_111.0,
        ReportSide.A_SHARE,
        page=5,
        unit=None,
        section="key_metrics",
        snippet="[Revenue · 2024] 32,137,830,111",
        period="2024",
    )
    a_visible_revenue = _metric(
        "revenue",
        32_137_839_111.0,
        ReportSide.A_SHARE,
        page=5,
        unit=None,
        confidence=0.82,
        section="key_metrics",
        snippet="[OCR Revenue] 32,137,839,111",
        source="generic_pattern",
        period="2024",
    )
    h_revenue = _metric(
        "revenue",
        32_137_830_111.0,
        ReportSide.H_SHARE,
        page=5,
        unit=None,
        section="key_metrics",
        snippet="[Revenue · 2024] 32,137,830,111",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_text_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    def fake_ocr(profile: ReportProfile) -> list[MetricItem]:
        return [a_visible_revenue] if profile.side == ReportSide.A_SHARE else []

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=fake_ocr)

    assert any(
        diff.rule_id == "visual_text_layer_mismatch"
        and diff.diff_scope == DiffScope.A_INTERNAL
        and diff.triage == "real"
        for diff in diffs
    )
    assert not any(
        diff.rule_id == "key_metric_exact_mismatch"
        and diff.diff_scope == DiffScope.CROSS_REPORT
        and diff.triage == "real"
        for diff in diffs
    )


def test_key_metric_exact_checker_uses_llm_to_downgrade_non_comparable_candidate(monkeypatch) -> None:
    from ahcc.check import key_metric_tamper

    calls: list[str] = []

    def fake_cached_call(purpose, messages, *, json_mode=False, **kwargs):
        calls.append(messages[0]["content"])
        return {
            "comparable": False,
            "confidence": 0.93,
            "reason": "One side appears to be an operating indicator detail, not the same annual revenue line.",
        }

    monkeypatch.setattr(key_metric_tamper.settings, "numeric_use_llm_semantic_review", True, raising=False)
    monkeypatch.setattr(key_metric_tamper.settings, "deepseek_api_key", "sk-real123")
    monkeypatch.setattr(key_metric_tamper, "cached_call", fake_cached_call, raising=False)

    a_revenue = _metric(
        "revenue",
        33_100.0,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="主要会计数据 营业收入 33,100",
        period="2025",
    )
    h_revenue = _metric(
        "revenue",
        33_086.0,
        ReportSide.H_SHARE,
        page=35,
        unit=None,
        section="key_metrics",
        snippet="经营指标 营业收入 33,086",
        period="2025",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda profile: [])

    assert calls
    assert "Return strict JSON" in calls[0]
    assert not any(diff.rule_id == "key_metric_exact_mismatch" and diff.triage == "real" for diff in diffs)
    assert any(diff.rule_id == "llm_semantic_review" and diff.triage == "unresolved" for diff in diffs)


def test_key_metric_visual_ocr_reports_text_layer_disagreement() -> None:
    text_revenue = _metric(
        "revenue",
        126_311.0,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        section="key_metrics",
        snippet="Revenue 126,311",
    )
    h_revenue = _metric(
        "revenue",
        126_460.0,
        ReportSide.H_SHARE,
        page=19,
        unit=None,
        section="key_metrics",
        snippet="Revenue 126,460",
    )
    visible_revenue = _metric(
        "revenue",
        126_411.0,
        ReportSide.A_SHARE,
        page=17,
        unit=None,
        confidence=0.72,
        section="key_metrics",
        snippet="[OCR Revenue] 126,411",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(text_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    def fake_ocr(profile):
        return [visible_revenue] if profile.side == ReportSide.A_SHARE else []

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=fake_ocr)

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.diff_type == DiffType.INTERNAL
    assert visual.diff_scope == DiffScope.A_INTERNAL
    assert visual.triage == "real"
    assert visual.a_value == 126_411.0
    assert visual.h_value == 126_311.0


def test_visual_ocr_matches_same_page_same_key_by_row_label() -> None:
    text_total_revenue = _metric(
        "revenue",
        20_219_547.23,
        ReportSide.A_SHARE,
        page=8,
        unit=None,
        section="key_metrics",
        snippet="[Total operating revenue | 2024] 20,219,547.23",
    )
    text_revenue = _metric(
        "revenue",
        20_500_000.00,
        ReportSide.A_SHARE,
        page=8,
        unit=None,
        section="key_metrics",
        snippet="[Revenue | 2024] 20,500,000.00",
    )
    visible_total_revenue = _metric(
        "revenue",
        20_269_547.23,
        ReportSide.A_SHARE,
        page=8,
        unit=None,
        confidence=0.72,
        section="key_metrics",
        snippet="[OCR Total operating revenue] 20,269,547.23",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_total_revenue, text_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        ocr_extractor=lambda profile: [visible_total_revenue] if profile.side == ReportSide.A_SHARE else [],
    )

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.a_value == 20_269_547.23
    assert visual.h_value == 20_219_547.23


def test_visual_ocr_requires_same_row_label_before_reporting_real_mismatch() -> None:
    text_operating_cash_flow = _metric(
        "operating_cash_flow",
        27_782_626_338.16,
        ReportSide.A_SHARE,
        page=272,
        unit=None,
        section="cash_flow_statement",
        snippet="[经营活动产生的现金流量净额] 27,782,626,338.16",
    )
    visible_cash_equivalent = _metric(
        "operating_cash_flow",
        27_209_807_096.70,
        ReportSide.A_SHARE,
        page=272,
        unit=None,
        confidence=0.78,
        section="cash_flow_statement",
        snippet="[visual overlay · 期末现金及现金等价物余额] 27,209,807,096.70",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(text_operating_cash_flow)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        ocr_extractor=lambda profile: [visible_cash_equivalent] if profile.side == ReportSide.A_SHARE else [],
    )

    assert not any(diff.rule_id == "visual_text_layer_mismatch" for diff in diffs)


def test_visual_ocr_rejects_note_values_without_digit_tamper_signature() -> None:
    text_customer_deposits = _metric(
        "customer_deposits",
        919_692.0,
        ReportSide.A_SHARE,
        page=328,
        unit="人民币百万元",
        section="notes",
        snippet="[吸收存款] 919,692",
    )
    visible_customer_deposits = _metric(
        "customer_deposits",
        912_441.0,
        ReportSide.A_SHARE,
        page=328,
        unit="人民币百万元",
        confidence=0.86,
        section="notes",
        snippet="[visual overlay · 吸收存款] 912,441",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(text_customer_deposits)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        ocr_extractor=lambda profile: [visible_customer_deposits] if profile.side == ReportSide.A_SHARE else [],
    )

    assert not any(diff.rule_id == "visual_text_layer_mismatch" for diff in diffs)


def test_visual_ocr_keeps_note_total_when_digits_show_single_tamper() -> None:
    text_total_deposits = _metric(
        "customer_deposits",
        4_102_458.0,
        ReportSide.A_SHARE,
        page=257,
        unit="人民币百万元",
        section="notes",
        snippet="[合计] 4,102,458",
    )
    visible_total_deposits = _metric(
        "customer_deposits",
        4_103_458.0,
        ReportSide.A_SHARE,
        page=257,
        unit="人民币百万元",
        confidence=0.86,
        section="notes",
        snippet="[visual overlay · 吸收存款] 4,103,458",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(text_total_deposits)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        ocr_extractor=lambda profile: [visible_total_deposits] if profile.side == ReportSide.A_SHARE else [],
    )

    assert any(diff.rule_id == "visual_text_layer_mismatch" for diff in diffs)


def test_visual_ocr_matches_customer_loans_book_value_with_loans_visible_label() -> None:
    text_book_value = _metric(
        "customer_loans",
        3_911_379.0,
        ReportSide.A_SHARE,
        page=216,
        unit=None,
        section="notes",
        snippet="[发放贷款和垫款账面价值] 3,911,379",
    )
    visible_book_value = _metric(
        "customer_loans",
        3_910_379.0,
        ReportSide.A_SHARE,
        page=216,
        unit=None,
        confidence=0.86,
        section="notes",
        snippet="[visual overlay · 客户贷款及垫款] 3,910,379",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_book_value, visible_book_value)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.canonical_key == "customer_loans"
    assert visual.a_value == 3_910_379.0
    assert visual.h_value == 3_911_379.0


def test_visual_ocr_compares_parenthesized_loss_overlay_by_absolute_amount() -> None:
    text_credit_loss = _metric(
        "credit_impairment_loss",
        36_426.0,
        ReportSide.A_SHARE,
        page=174,
        unit=None,
        section="pl",
        snippet="[信用减值损失] (36,426)",
    )
    visible_credit_loss = _metric(
        "credit_impairment_loss",
        -36_526.0,
        ReportSide.A_SHARE,
        page=174,
        unit=None,
        confidence=0.86,
        section="pl",
        snippet="[visual overlay · 信用减值损失] (36,526)",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_credit_loss, visible_credit_loss)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.canonical_key == "credit_impairment_loss"
    assert visual.a_value == 36_526.0
    assert visual.h_value == 36_426.0
    assert visual.delta == 100.0


def test_visual_ocr_rejects_large_mda_tail_value_without_tamper_scale() -> None:
    text_total_profit = _metric(
        "total_profit",
        49_687.0,
        ReportSide.A_SHARE,
        page=30,
        unit=None,
        section="mda",
        snippet="[利润总额] 49,687",
    )
    visible_total_profit = _metric(
        "total_profit",
        51_474.0,
        ReportSide.A_SHARE,
        page=30,
        unit=None,
        confidence=0.86,
        section="mda",
        snippet="[visual overlay · 利润总额] 51,474",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_total_profit, visible_total_profit)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    assert not any(diff.rule_id == "visual_text_layer_mismatch" for diff in diffs)


def test_visual_ocr_reports_unit_scaled_percentage_tamper_by_raw_delta() -> None:
    text_roe = _metric(
        "weighted_average_roe",
        7.00,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        section="revenue",
        snippet="[加权平均净资产收益率] 7.00",
    )
    visible_roe = _metric(
        "weighted_average_roe",
        7.10,
        ReportSide.A_SHARE,
        page=17,
        unit="人民币百万元",
        confidence=0.86,
        section="revenue",
        snippet="[visual overlay · 加权平均净资产收益率] 7.10",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_roe, visible_roe)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    assert any(
        diff.rule_id == "visual_text_layer_mismatch"
        and diff.canonical_key == "weighted_average_roe"
        for diff in diffs
    )


def test_visual_ocr_rejects_small_scale_values_without_digit_tamper_signature() -> None:
    text_eps = _metric(
        "eps_basic",
        7.00,
        ReportSide.A_SHARE,
        page=19,
        unit="人民币百万元",
        section="eps",
        snippet="[基本每股收益] 7.00",
    )
    visible_eps = _metric(
        "eps_basic",
        8.87,
        ReportSide.A_SHARE,
        page=19,
        unit="人民币百万元",
        confidence=0.86,
        section="eps",
        snippet="[visual overlay · 基本每股收益] 8.87",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_eps, visible_eps)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    assert not any(diff.rule_id == "visual_text_layer_mismatch" for diff in diffs)


def test_visual_ocr_reports_notes_operating_cash_flow_when_digits_show_tamper() -> None:
    text_operating_cash_flow = _metric(
        "operating_cash_flow",
        162_907.0,
        ReportSide.A_SHARE,
        page=290,
        unit=None,
        section="notes",
        snippet="[经营活动产生 / (所用)的现金流量净额] 162,907",
    )
    visible_operating_cash_flow = _metric(
        "operating_cash_flow",
        162_807.0,
        ReportSide.A_SHARE,
        page=290,
        unit=None,
        confidence=0.86,
        section="notes",
        snippet="[visual overlay · 经营活动现金流量净额] 162,807",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_operating_cash_flow, visible_operating_cash_flow)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, ocr_extractor=lambda _profile: [])

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.canonical_key == "operating_cash_flow"
    assert visual.a_value == 162_807.0
    assert visual.h_value == 162_907.0


def test_extract_metrics_maps_investing_cash_flow_slash_net_line_and_tail_overlay() -> None:
    segment = TextSegment(
        segment_id="a-cf-182",
        page=182,
        bbox=(0.0, 0.0, 1.0, 1.0),
        text=(
            "合并现金流量表\n"
            "投资活动(所用) / 产生的现金流量净额\n"
            "(103,900)\n"
            "123,684\n"
            "(103,990)"
        ),
        raw_text=(
            "合并现金流量表\n"
            "投资活动(所用) / 产生的现金流量净额\n"
            "(103,900)\n"
            "123,684\n"
            "(103,990)"
        ),
        language=Language.ZH,
        section="cf",
    )
    doc = ReportDocument(
        doc_id="a-cash-flow",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=200,
        primary_language=Language.ZH,
        texts=[segment],
        metadata={"unit": "人民币百万元", "currency": Currency.CNY.value},
    )

    occurrences = extract_metrics(doc)
    items = [
        item
        for occurrence in occurrences
        for item in getattr(occurrence, "all_occurrences", [])
    ]

    hidden = [
        item for item in items
        if item.canonical_key == "investing_cash_flow"
        and item.source == "text"
        and abs(abs(item.value or 0.0) - 103_900.0) < 1e-9
    ]
    visible = [
        item for item in items
        if item.canonical_key == "investing_cash_flow"
        and item.source == "generic_pattern"
        and "visual overlay" in (item.evidence.snippet or "")
        and abs(abs(item.value or 0.0) - 103_990.0) < 1e-9
    ]
    assert hidden
    assert visible


def test_extract_metrics_prefers_non_performing_ratio_for_tail_rate_overlay() -> None:
    segment = TextSegment(
        segment_id="a-key-ratio-17",
        page=17,
        bbox=(0.0, 0.0, 1.0, 1.0),
        text=(
            "主要财务指标\n"
            "净利差\n"
            "1.32\n"
            "不良贷款率\n"
            "1.27\n"
            "1.37"
        ),
        raw_text=(
            "主要财务指标\n"
            "净利差\n"
            "1.32\n"
            "不良贷款率\n"
            "1.27\n"
            "1.37"
        ),
        language=Language.ZH,
        section="revenue",
    )
    doc = ReportDocument(
        doc_id="a-key-ratio",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=60,
        primary_language=Language.ZH,
        texts=[segment],
        metadata={"unit": "人民币百万元", "currency": Currency.CNY.value},
    )

    occurrences = extract_metrics(doc)
    items = [
        item
        for occurrence in occurrences
        for item in getattr(occurrence, "all_occurrences", [])
    ]

    assert any(
        item.canonical_key == "non_performing_loan_ratio"
        and item.source == "generic_pattern"
        and item.value_text == "1.37"
        for item in items
    )
    assert not any(
        item.canonical_key == "net_interest_spread"
        and item.source == "generic_pattern"
        and item.value_text == "1.37"
        for item in items
    )


def test_visual_ocr_can_match_note_total_row_when_account_label_is_visible() -> None:
    text_detail_receivable = _metric(
        "receivables",
        7_181_913_362.63,
        ReportSide.A_SHARE,
        page=228,
        unit=None,
        section="notes",
        snippet="[应收账款] 7,181,913,362.63",
    )
    text_total_receivable = _metric(
        "receivables",
        7_273_343_067.28,
        ReportSide.A_SHARE,
        page=228,
        unit=None,
        section="notes",
        snippet="[合计] 7,273,343,067.28",
    )
    visible_receivable_total = _metric(
        "receivables",
        7_273_343_017.28,
        ReportSide.A_SHARE,
        page=228,
        unit=None,
        confidence=0.86,
        section="notes",
        snippet="[visual overlay · 应收账款] 7,273,343,017.28",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_detail_receivable, text_total_receivable)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        ocr_extractor=lambda profile: [visible_receivable_total] if profile.side == ReportSide.A_SHARE else [],
    )

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.a_value == 7_273_343_017.28
    assert visual.h_value == 7_273_343_067.28


def test_visual_ocr_matches_cash_equivalent_end_with_generic_cash_visible_label() -> None:
    text_cash_change = _metric(
        "cash_equivalents",
        27_209_807_036.70,
        ReportSide.A_SHARE,
        page=272,
        unit=None,
        section="notes",
        snippet="[现金及现金等价物净变动情况：] 27,209,807,036.70",
    )
    text_cash_end = _metric(
        "cash_equivalents_end",
        27_209_807_036.70,
        ReportSide.A_SHARE,
        page=272,
        unit=None,
        section="notes",
        snippet="[现金及现金等价物的年末余额] 27,209,807,036.70",
    )
    visible_cash_end = _metric(
        "cash_equivalents",
        27_209_807_096.70,
        ReportSide.A_SHARE,
        page=272,
        unit=None,
        confidence=0.86,
        section="notes",
        snippet="[visual overlay · 货币资金] 27,209,807,096.70",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_cash_change, text_cash_end)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        ocr_extractor=lambda profile: [visible_cash_end] if profile.side == ReportSide.A_SHARE else [],
    )

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.canonical_key == "cash_equivalents_end"
    assert visual.a_value == 27_209_807_096.70
    assert visual.h_value == 27_209_807_036.70


def test_smart_visual_review_selects_high_risk_pages_without_full_report_ocr() -> None:
    profile = _profile(ReportSide.A_SHARE)
    profile.total_pages = 220
    profile.source_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=220,
        primary_language=Language.ZH,
        tables=[
            FinancialTable(
                table_id="A_p088_financial_indicators",
                title=LocalizedString(zh="Key financial indicators"),
                page=88,
                bbox=(0.0, 0.0, 1.0, 1.0),
                cells=[
                    TableCell(row=0, col=0, text="Metric", is_header=True),
                    TableCell(row=0, col=1, text="2025", is_header=True),
                    TableCell(row=1, col=0, text="Weighted average ROE"),
                    TableCell(row=1, col=1, text="7.10%"),
                    TableCell(row=2, col=0, text="Cost-to-income ratio"),
                    TableCell(row=2, col=1, text="28.35%"),
                ],
            ),
            FinancialTable(
                table_id="A_p160_unrelated",
                title=LocalizedString(zh="Ordinary narrative appendix"),
                page=160,
                bbox=(0.0, 0.0, 1.0, 1.0),
                cells=[
                    TableCell(row=0, col=0, text="Name"),
                    TableCell(row=0, col=1, text="Description"),
                ],
            ),
        ],
        texts=[
            TextSegment(
                segment_id="A_p120_income_statement",
                page=120,
                bbox=(0, 0, 1, 1),
                text="Consolidated income statement and statement of financial position",
                language=Language.EN,
            )
        ],
    )

    pages = _candidate_ocr_pages(profile, visual_review_mode="smart")

    assert 88 in pages
    assert 120 in pages
    assert 160 not in pages
    assert len(pages) < profile.total_pages


def test_visual_review_off_does_not_call_runtime_ocr() -> None:
    profile_a = _profile(
        ReportSide.A_SHARE,
        metrics=[
            _occ_many(
                _metric(
                    "revenue",
                    100.0,
                    ReportSide.A_SHARE,
                    page=8,
                    section="key_metrics",
                    snippet="[Revenue | 2025] 100",
                )
            )
        ],
    )
    profile_h = _profile(ReportSide.H_SHARE)
    calls = 0
    visual_status: dict = {}

    def fail_if_called(profile: ReportProfile) -> list[MetricItem]:
        nonlocal calls
        calls += 1
        raise AssertionError("runtime OCR should be disabled in off mode")

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        visual_review_mode="off",
        ocr_extractor=fail_if_called,
        visual_ocr_status=visual_status,
    )

    assert diffs == []
    assert calls == 0
    assert visual_status["mode"] == "off"
    assert visual_status["skipped_reason"] == "runtime_ocr_disabled"


def test_smart_visual_review_skips_large_easyocr_reports(monkeypatch) -> None:
    work_dir = Path("storage") / "test-artifacts" / f"ocr-large-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "large-qingdao-like.pdf"
    pdf_path.write_bytes(b"x" * 2048)
    try:
        monkeypatch.setattr(settings, "visual_ocr_easyocr_skip_pages", 100, raising=False)
        monkeypatch.setattr(settings, "visual_ocr_easyocr_skip_mb", 0.001, raising=False)
        from ahcc.parser import ocr_fallback

        monkeypatch.setattr(ocr_fallback, "_PADDLEOCR_AVAILABLE", False)
        monkeypatch.setattr(ocr_fallback, "_EASYOCR_AVAILABLE", True)

        profile_a = _profile(
            ReportSide.A_SHARE,
            metrics=[
                _occ_many(
                    _metric(
                        "revenue",
                        100.0,
                        ReportSide.A_SHARE,
                        page=8,
                        section="key_metrics",
                        snippet="[Revenue | 2025] 100",
                    )
                )
            ],
        )
        profile_a.total_pages = 225
        profile_a.source_doc = ReportDocument(
            doc_id="A",
            side=ReportSide.A_SHARE,
            file_path=str(pdf_path),
            total_pages=225,
            primary_language=Language.ZH,
        )
        profile_h = _profile(ReportSide.H_SHARE)
        visual_status: dict = {}
        calls = 0

        def fail_if_called(profile: ReportProfile) -> list[MetricItem]:
            nonlocal calls
            calls += 1
            raise AssertionError("large EasyOCR report should be skipped before extractor")

        diffs = run_key_metric_tamper_checks(
            profile_a,
            profile_h,
            visual_review_mode="smart",
            ocr_extractor=fail_if_called,
            visual_ocr_status=visual_status,
        )

        assert diffs == []
        assert calls == 0
        assert visual_status["skipped_reason"] == "easyocr_large_pdf"
        assert visual_status["engine"] == "easyocr"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_default_ocr_extractor_applies_strict_page_and_time_budget(monkeypatch) -> None:
    work_dir = Path("storage") / "test-artifacts" / f"ocr-budget-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "budget.pdf"
    pdf_path.write_bytes(b"%PDF-placeholder")
    try:
        monkeypatch.setattr(settings, "visual_ocr_strict_max_pages", 3, raising=False)
        monkeypatch.setattr(settings, "visual_ocr_max_seconds_per_side", 12.5, raising=False)
        captured: dict = {}

        def fake_extract_metrics_via_ocr(file_path, side, max_pages=None, pages=None, dpi=200, unit=None, currency=None, max_seconds=None, runtime_status=None):
            captured["pages"] = pages
            captured["max_seconds"] = max_seconds
            if runtime_status is not None:
                runtime_status.update({"processed_pages": pages, "timed_out": False})
            return []

        from ahcc.parser import ocr_fallback

        monkeypatch.setattr(ocr_fallback, "extract_metrics_via_ocr", fake_extract_metrics_via_ocr)
        profile = _profile(
            ReportSide.A_SHARE,
            metrics=[
                _occ_many(
                    *[
                        _metric(
                            "revenue",
                            float(page),
                            ReportSide.A_SHARE,
                            page=page,
                            section="key_metrics",
                            snippet=f"[Revenue | 2025] {page}",
                        )
                        for page in range(1, 12)
                    ]
                )
            ],
        )
        profile.total_pages = 20
        profile.source_doc = ReportDocument(
            doc_id="A",
            side=ReportSide.A_SHARE,
            file_path=str(pdf_path),
            total_pages=20,
            primary_language=Language.ZH,
        )
        visual_status: dict = {"mode": "strict", "sides": {}}

        items = key_metric_tamper._default_ocr_extractor(
            profile,
            visual_review_mode="strict",
            visual_ocr_status=visual_status,
        )

        assert items == []
        assert len(captured["pages"]) == 3
        assert captured["max_seconds"] == 12.5
        assert visual_status["sides"]["A"]["ocr_page_count"] == 3
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_ocr_metric_extraction_stops_when_time_budget_is_exhausted(monkeypatch) -> None:
    import fitz
    from ahcc.parser import ocr_fallback

    work_dir = Path("storage") / "test-artifacts" / f"ocr-timed-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "timed.pdf"
    try:
        doc = fitz.open()
        for page_no in range(3):
            page = doc.new_page()
            page.insert_text((72, 72), f"Revenue {page_no + 1}")
        doc.save(str(pdf_path))
        doc.close()

        monkeypatch.setattr(ocr_fallback, "_PADDLEOCR_AVAILABLE", False)
        monkeypatch.setattr(ocr_fallback, "_EASYOCR_AVAILABLE", True)
        monkeypatch.setattr(ocr_fallback, "_run_ocr_easyocr", lambda image: [("Revenue 100", 0.99)])
        monkeypatch.setattr(ocr_fallback, "_extract_metrics_from_ocr_lines", lambda *args, **kwargs: [])
        ticks = iter([0.0, 2.0, 2.0])
        monkeypatch.setattr(ocr_fallback.time, "monotonic", lambda: next(ticks, 2.0))
        runtime_status: dict = {}

        items = ocr_fallback.extract_metrics_via_ocr(
            str(pdf_path),
            ReportSide.A_SHARE,
            pages=[1, 2, 3],
            max_seconds=1.0,
            runtime_status=runtime_status,
        )

        assert items == []
        assert runtime_status["processed_pages"] == [1]
        assert runtime_status["timed_out"] is True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_smart_visual_review_keeps_late_note_pages_when_front_candidates_overflow() -> None:
    early_items = [
        _metric(
            "revenue",
            float(page * 1000),
            ReportSide.A_SHARE,
            page=page,
            section="key_metrics",
            snippet=f"[Revenue | 2025] {page * 1000:,}",
        )
        for page in range(1, 101)
    ]
    late_note_items = [
        _metric(
            "investing_cash_flow",
            -103_900.0,
            ReportSide.A_SHARE,
            page=182,
            section="",
            snippet="[Investing cash flow] 446",
        ),
        _metric(
            "central_bank_deposits",
            339_232.0,
            ReportSide.A_SHARE,
            page=209,
            section="notes",
            snippet="[Cash and deposits with central bank total | 2025] 339,232",
        ),
        _metric(
            "customer_loans",
            3_911_379.0,
            ReportSide.A_SHARE,
            page=216,
            section="notes",
            snippet="[Loans and advances carrying amount total | 2025] 3,911,379",
        ),
        _metric(
            "financial_investments",
            2_371_901.0,
            ReportSide.A_SHARE,
            page=225,
            section="notes",
            snippet="[Financial investments total | 2025] 2,371,901",
        ),
        _metric(
            "inventory",
            25_407_813_490.36,
            ReportSide.A_SHARE,
            page=235,
            section="",
            snippet="[Inventory category] 2024",
        ),
        _metric(
            "customer_deposits",
            4_102_458.0,
            ReportSide.A_SHARE,
            page=257,
            section="notes",
            snippet="[Customer deposits total | 2025] 4,102,458",
        ),
        _metric(
            "cash_equivalents_end",
            162_907.0,
            ReportSide.A_SHARE,
            page=290,
            section="notes",
            snippet="[Net cash flows from operating activities | 2025] 162,907",
        ),
    ]
    profile = _profile(ReportSide.A_SHARE, metrics=[_occ_many(early_items[0], *early_items[1:], *late_note_items)])
    profile.total_pages = 354

    pages = _candidate_ocr_pages(profile, visual_review_mode="smart")

    assert len(pages) <= 24
    assert {182, 209, 216, 225, 235, 257, 290}.issubset(set(pages))


def test_smart_visual_review_keeps_first_late_bank_note_topic_pages_under_noise() -> None:
    noisy_items = [
        _metric(
            "financial_investments",
            float(page * 1000),
            ReportSide.A_SHARE,
            page=page,
            section="notes",
            snippet=f"[金融投资合计] {page * 1000:,}",
        )
        for page in range(300, 330)
    ]
    target_items = [
        _metric(
            "investing_cash_flow",
            446.0,
            ReportSide.A_SHARE,
            page=182,
            section="cf",
            snippet="[投资活动现金流] 446",
        ),
        _metric(
            "customer_deposits",
            72_220.0,
            ReportSide.A_SHARE,
            page=181,
            section="cf",
            snippet="[经营活动产生的现金流量 客户存款净增加额] 72,220",
        ),
        _metric(
            "central_bank_deposits",
            2025.0,
            ReportSide.A_SHARE,
            page=209,
            section="notes",
            snippet="[现金及存放中央银行款项 本集团 本行 注] 2025",
        ),
        _metric(
            "customer_loans",
            3_911_379.0,
            ReportSide.A_SHARE,
            page=216,
            section="notes",
            snippet="[发放贷款和垫款账面价值] 3,911,379",
        ),
        _metric(
            "financial_investments",
            2025.0,
            ReportSide.A_SHARE,
            page=225,
            section="notes",
            snippet="[金融投资 本集团 本行 注] 2025",
        ),
        _metric(
            "customer_deposits",
            4_102_458.0,
            ReportSide.A_SHARE,
            page=257,
            section="notes",
            snippet="[吸收存款小计] 4,102,458",
        ),
        _metric(
            "cash_equivalents_end",
            162_907.0,
            ReportSide.A_SHARE,
            page=290,
            section="notes",
            snippet="[经营活动现金流量净额] 162,907",
        ),
    ]
    profile = _profile(ReportSide.A_SHARE, metrics=[_occ_many(target_items[0], *target_items[1:], *noisy_items)])
    profile.total_pages = 354
    profile.source_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=354,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id=f"A_p{page}",
                page=page,
                bbox=(0, 0, 1, 1),
                text="财务报表 附注 financial statements notes",
                language=Language.ZH,
            )
            for page in [*range(170, 231), *range(300, 330)]
        ],
    )

    pages = _candidate_ocr_pages(profile, visual_review_mode="smart")

    assert len(pages) <= 24
    assert {182, 209, 216, 225, 257, 290}.issubset(set(pages))


def test_smart_visual_review_does_not_ocr_when_no_high_risk_pages_exist() -> None:
    profile = _profile(ReportSide.A_SHARE)
    profile.total_pages = 220
    profile.source_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=220,
        primary_language=Language.ZH,
        tables=[
            FinancialTable(
                table_id="A_p160_unrelated",
                title=LocalizedString(en="Ordinary appendix"),
                page=160,
                bbox=(0.0, 0.0, 1.0, 1.0),
                cells=[
                    TableCell(row=0, col=0, text="Name"),
                    TableCell(row=0, col=1, text="Description"),
                ],
            )
        ],
    )

    pages = _candidate_ocr_pages(profile, visual_review_mode="smart")

    assert pages == []


def test_smart_visual_review_skips_h_ocr_when_text_metrics_are_sufficient_with_overlay_noise() -> None:
    h_metrics = [
        _metric("revenue", 32_137_830_111.0, ReportSide.H_SHARE, page=7, section="key_metrics"),
        _metric("net_profit_attributable", 4_344_983_858.0, ReportSide.H_SHARE, page=7, section="key_metrics"),
        _metric("total_assets", 51_420_385_832.0, ReportSide.H_SHARE, page=8, section="bs"),
        _metric("operating_cash_flow", 5_154_661_132.0, ReportSide.H_SHARE, page=9, section="cf"),
    ]
    overlay_noise = _metric(
        "revenue",
        32_137_839_111.0,
        ReportSide.H_SHARE,
        page=7,
        section="key_metrics",
        snippet="[visual overlay · Revenue] 32,137,839,111",
        source="generic_pattern",
    )
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ_many(h_metrics[0], *h_metrics[1:], overlay_noise)])
    profile_h.total_pages = 239
    profile_h.source_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=239,
        primary_language=Language.EN,
        texts=[],
        tables=[],
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(_metric("revenue", 1.0, ReportSide.A_SHARE))])

    ocr_calls: list[ReportSide] = []

    def tracking_ocr(profile: ReportProfile) -> list[MetricItem]:
        ocr_calls.append(profile.side)
        return []

    diffs = run_key_metric_tamper_checks(profile_a, profile_h, visual_review_mode="smart", ocr_extractor=tracking_ocr)

    assert ReportSide.H_SHARE not in ocr_calls
    assert not any(diff.diff_scope == DiffScope.H_INTERNAL for diff in diffs)


def test_strict_visual_review_expands_to_first_120_pages_plus_high_risk_later_pages() -> None:
    profile = _profile(ReportSide.A_SHARE)
    profile.total_pages = 220
    profile.source_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=220,
        primary_language=Language.ZH,
        tables=[
            FinancialTable(
                table_id="A_p180_regulatory_capital",
                title=LocalizedString(en="Capital adequacy and regulatory ratios"),
                page=180,
                bbox=(0.0, 0.0, 1.0, 1.0),
                cells=[
                    TableCell(row=0, col=0, text="Ratio"),
                    TableCell(row=0, col=1, text="2025"),
                    TableCell(row=1, col=0, text="Liquidity coverage ratio"),
                    TableCell(row=1, col=1, text="155.20%"),
                ],
            )
        ],
    )

    pages = _candidate_ocr_pages(profile, visual_review_mode="strict")

    assert 1 in pages
    assert 120 in pages
    assert 121 not in pages
    assert 180 in pages


def test_visual_ocr_ratio_metric_reports_small_decimal_text_layer_disagreement() -> None:
    text_roe = _metric(
        "weighted_average_roe",
        7.10,
        ReportSide.A_SHARE,
        page=88,
        unit=None,
        section="key_metrics",
        snippet="Weighted average ROE 7.10%",
    )
    h_revenue = _metric("revenue", 126_460.0, ReportSide.H_SHARE, page=19, unit=None)
    visible_roe = _metric(
        "weighted_average_roe",
        7.20,
        ReportSide.A_SHARE,
        page=88,
        unit=None,
        confidence=0.72,
        section="key_metrics",
        snippet="[OCR Weighted average ROE] 7.20%",
        source="generic_pattern",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(text_roe)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    def fake_ocr(profile):
        return [visible_roe] if profile.side == ReportSide.A_SHARE else []

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        visual_review_mode="smart",
        ocr_extractor=fake_ocr,
    )

    visual = next(diff for diff in diffs if diff.rule_id == "visual_text_layer_mismatch")
    assert visual.canonical_key == "weighted_average_roe"
    assert visual.a_value == 7.20
    assert visual.h_value == 7.10


def test_smart_visual_review_uses_embedded_overlay_without_running_expensive_ocr() -> None:
    text_revenue = _metric(
        "revenue",
        20_219_547.23,
        ReportSide.A_SHARE,
        page=8,
        unit="人民币万元",
        source="text",
        snippet="[营业总收入] 20,219,547.23",
        period="2024",
    )
    overlay_revenue = _metric(
        "revenue",
        20_269_547.23,
        ReportSide.A_SHARE,
        page=8,
        unit="人民币万元",
        confidence=0.86,
        source="generic_pattern",
        snippet="[visual overlay · 营业总收入] 20,269,547.23",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_revenue, overlay_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])
    ocr_calls = 0

    def fail_if_called(_profile):
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("smart mode should not OCR when embedded visual overlay already exists")

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        visual_review_mode="smart",
        ocr_extractor=fail_if_called,
    )

    assert ocr_calls == 0
    assert any(diff.rule_id == "visual_text_layer_mismatch" for diff in diffs)


def test_smart_visual_review_trusts_dense_embedded_overlay_coverage_without_extra_ocr() -> None:
    keys = [
        "revenue",
        "net_profit_attributable",
        "operating_cash_flow",
        "taxes_and_surcharges",
        "eps_basic",
        "weighted_average_roe",
        "receivables",
        "inventory",
    ]
    text_items = [
        _metric(
            key,
            float(page * 1000),
            ReportSide.A_SHARE,
            page=page,
            unit=None,
            source="text",
            snippet=f"[{key}] {page * 1000:,}",
            period="2024",
        )
        for page, key in zip(range(1, 50), [*keys, *["revenue"] * 41])
    ]
    overlay_items = [
        _metric(
            key,
            float(page * 1000 + 100),
            ReportSide.A_SHARE,
            page=page,
            unit=None,
            confidence=0.86,
            source="generic_pattern",
            snippet=f"[visual overlay · {key}] {page * 1000 + 100:,}",
            period="2024",
        )
        for page, key in zip(range(1, 9), keys)
    ]
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_items[0], *overlay_items, *text_items[1:])])
    profile_a.total_pages = 80
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])
    ocr_calls = 0

    def fail_if_called(_profile):
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("dense embedded visual overlay coverage should avoid extra OCR")

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        visual_review_mode="smart",
        ocr_extractor=fail_if_called,
    )

    assert ocr_calls == 0
    assert sum(1 for diff in diffs if diff.rule_id == "visual_text_layer_mismatch") >= 8


def test_smart_visual_review_skips_h_side_ocr_when_text_layer_is_sufficient() -> None:
    h_items = [
        _metric("revenue", 126_460.0, ReportSide.H_SHARE, page=1, unit=None),
        _metric("net_profit_attributable", 1_269_220.42, ReportSide.H_SHARE, page=1, unit=None),
        _metric("operating_cash_flow", 2_778_262.63, ReportSide.H_SHARE, page=4, unit=None),
        _metric("cash_equivalents_end", 27_209_807_036.70, ReportSide.H_SHARE, page=6, unit=None),
    ]
    profile_a = _profile(ReportSide.A_SHARE, metrics=[])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ_many(h_items[0], *h_items[1:])])
    profile_h.total_pages = 6
    profile_h.source_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=6,
        primary_language=Language.EN,
        texts=[
            TextSegment(
                segment_id="h-key-metrics",
                page=1,
                bbox=(0, 0, 1, 1),
                text="Key financial indicators revenue net profit cash flow",
                language=Language.EN,
            )
        ],
    )
    ocr_calls = 0

    def fail_if_called(_profile):
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("H-side smart OCR should be skipped when text-layer metrics are sufficient")

    run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        visual_review_mode="smart",
        ocr_extractor=fail_if_called,
    )

    assert ocr_calls == 0


def test_smart_visual_review_still_ocrs_uncovered_metric_pages_when_overlay_is_partial() -> None:
    text_revenue = _metric(
        "revenue",
        32_137_830_111.0,
        ReportSide.A_SHARE,
        page=5,
        unit="人民币元",
        source="text",
        snippet="[营业收入] 32,137,830,111",
        period="2024",
    )
    overlay_revenue = _metric(
        "revenue",
        32_137_839_111.0,
        ReportSide.A_SHARE,
        page=5,
        unit="人民币元",
        confidence=0.86,
        source="generic_pattern",
        snippet="[visual overlay · 营业收入] 32,137,839,111",
        period="2024",
    )
    text_cost = _metric(
        "cost_of_revenue",
        19_209_916.0,
        ReportSide.A_SHARE,
        page=9,
        unit="人民币千元",
        source="text",
        snippet="[营业成本(千元)] 19,209,916",
        period="2024",
    )
    ocr_cost = _metric(
        "cost_of_revenue",
        19_209_911.0,
        ReportSide.A_SHARE,
        page=9,
        unit="人民币千元",
        confidence=0.72,
        source="generic_pattern",
        snippet="[OCR 营业成本] 19,209,911",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_revenue, overlay_revenue), _occ(text_cost)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])
    ocr_calls = 0

    def fake_ocr(profile):
        nonlocal ocr_calls
        ocr_calls += 1
        return [ocr_cost] if profile.side == ReportSide.A_SHARE else []

    diffs = run_key_metric_tamper_checks(
        profile_a,
        profile_h,
        visual_review_mode="smart",
        ocr_extractor=fake_ocr,
    )

    assert ocr_calls == 1
    visual_cost = [
        diff for diff in diffs
        if diff.rule_id == "visual_text_layer_mismatch" and diff.canonical_key == "cost_of_revenue"
    ]
    assert visual_cost
    assert visual_cost[0].a_value == 19_209_911_000.0
    assert visual_cost[0].h_value == 19_209_916_000.0


def test_smart_visual_review_limits_partial_overlay_ocr_to_smart_budget(monkeypatch) -> None:
    from ahcc.check import key_metric_tamper

    text_items = [
        _metric(
            "revenue",
            float(page * 1000),
            ReportSide.A_SHARE,
            page=page,
            unit=None,
            source="text",
            snippet=f"[营业收入] {page * 1000:,}",
            period="2024",
        )
        for page in range(1, 81)
    ]
    overlay_first_page = _metric(
        "revenue",
        1_001.0,
        ReportSide.A_SHARE,
        page=1,
        unit=None,
        confidence=0.86,
        source="generic_pattern",
        snippet="[visual overlay · 营业收入] 1,001",
        period="2024",
    )
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ_many(text_items[0], overlay_first_page, *text_items[1:])])
    profile_a.total_pages = 100
    profile_h = _profile(ReportSide.H_SHARE, metrics=[])
    captured_pages: list[int] = []

    def fake_default_ocr(profile, *, visual_review_mode="smart", pages_override=None):
        captured_pages.extend(pages_override or [])
        return []

    monkeypatch.setattr(key_metric_tamper, "_default_ocr_extractor", fake_default_ocr)

    run_key_metric_tamper_checks(profile_a, profile_h, visual_review_mode="smart")

    assert captured_pages
    assert len(captured_pages) <= 24
    assert 1 not in captured_pages


def test_ocr_metric_fallback_log_reports_selected_page_count() -> None:
    source = (Path(__file__).resolve().parents[1] / "ahcc" / "parser" / "ocr_fallback.py").read_text(
        encoding="utf-8"
    )

    assert "OCR页数={len(pages_to_ocr)}" in source
    assert "OCR页={pages_to_ocr[-1]" not in source


def test_ocr_metric_parser_keeps_small_decimal_ratio_metrics() -> None:
    from ahcc.parser.ocr_fallback import _extract_metrics_from_ocr_lines

    items = _extract_metrics_from_ocr_lines(
        ["Weighted average ROE 7.10%"],
        page_num=88,
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
    )

    assert any(item.canonical_key == "weighted_average_roe" and item.value == 7.10 for item in items)


def test_ocr_metric_parser_keeps_multiple_same_key_rows() -> None:
    from ahcc.parser.ocr_fallback import _extract_metrics_from_ocr_lines

    items = _extract_metrics_from_ocr_lines(
        ["营业总收入 20,269,547.23", "营业收入 20,219,547.23"],
        page_num=8,
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
    )

    revenue_values = [item.value for item in items if item.canonical_key == "revenue"]

    assert 20_269_547.23 in revenue_values
    assert 20_219_547.23 in revenue_values


def test_ocr_metric_parser_classifies_attributable_profit_precisely() -> None:
    from ahcc.parser.ocr_fallback import _extract_metrics_from_ocr_lines

    items = _extract_metrics_from_ocr_lines(
        ["归属于上市公司股东的净利润 1,239,220.42"],
        page_num=8,
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
    )

    assert any(
        item.canonical_key == "net_profit_attributable" and item.value == 1_239_220.42
        for item in items
    )
    assert not any(item.canonical_key == "net_profit" for item in items)


def test_ocr_metric_parser_normalizes_common_profit_ocr_confusion() -> None:
    from ahcc.parser.ocr_fallback import _extract_metrics_from_ocr_lines

    items = _extract_metrics_from_ocr_lines(
        ["归属于上市公司股东的诤利润", "1,239,220.42"],
        page_num=8,
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
    )

    assert any(
        item.canonical_key == "net_profit_attributable" and item.value == 1_239_220.42
        for item in items
    )


def test_ocr_metric_parser_does_not_turn_adjusted_profit_tail_into_net_profit() -> None:
    from ahcc.parser.ocr_fallback import _extract_metrics_from_ocr_lines

    items = _extract_metrics_from_ocr_lines(
        [
            "归属于上市公司股东的扣除非经常性",
            "973,524.83",
            "损益的净利润",
            "经营活动产生的现金流量净额",
            "2,718,262.63",
        ],
        page_num=8,
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
    )

    assert not any(item.canonical_key == "net_profit" for item in items)


def test_ocr_temp_dir_uses_storage_workspace(monkeypatch) -> None:
    from ahcc.parser import ocr_fallback

    storage_dir = Path("storage") / "test-artifacts" / "ocr-temp-workspace"
    monkeypatch.setattr(ocr_fallback.settings, "storage_dir", storage_dir)

    with ocr_fallback._temporary_ocr_dir() as tmpdir:
        temp_path = Path(tmpdir)
        assert temp_path.exists()
        assert temp_path.parent == (storage_dir / "ocr_tmp").resolve()
        probe_file = temp_path / "probe.txt"
        probe_file.write_text("ok", encoding="utf-8")
        assert probe_file.read_text(encoding="utf-8") == "ok"


def test_easyocr_path_uses_in_memory_page_image(monkeypatch) -> None:
    import fitz

    from ahcc.parser import ocr_fallback

    class FakePix:
        width = 2
        height = 1
        n = 3
        samples = bytes([255, 0, 0, 0, 255, 0])

        def save(self, _path: str) -> None:
            raise AssertionError("EasyOCR should not depend on a localized image file path")

    class FakePage:
        def get_pixmap(self, dpi: int):
            return FakePix()

    class FakeDoc:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int):
            assert index == 0
            return FakePage()

        def close(self) -> None:
            pass

    seen_images: list[object] = []

    def fake_easyocr(image):
        seen_images.append(image)
        return [("Revenue 100", 0.91)]

    monkeypatch.setattr(fitz, "open", lambda _path: FakeDoc())
    monkeypatch.setattr(ocr_fallback, "_EASYOCR_AVAILABLE", True)
    monkeypatch.setattr(ocr_fallback, "_PADDLEOCR_AVAILABLE", False)
    monkeypatch.setattr(ocr_fallback, "_run_ocr_easyocr", fake_easyocr)
    monkeypatch.setattr(
        ocr_fallback,
        "_extract_metrics_from_ocr_lines",
        lambda lines, page_num, side, file_path, unit=None, currency=None: ["metric"],
    )

    items = ocr_fallback.extract_metrics_via_ocr("a.pdf", ReportSide.A_SHARE, pages=[1])

    assert items == ["metric"]
    assert seen_images
    assert not isinstance(seen_images[0], (str, Path))


def test_easyocr_reader_is_reused_across_pages(monkeypatch) -> None:
    from ahcc.parser import ocr_fallback

    created: list[tuple[tuple[str, ...], bool]] = []

    class FakeReader:
        def __init__(self, languages, gpu=False):
            created.append((tuple(languages), gpu))

        def readtext(self, _image):
            return [([], "Revenue 100", 0.91)]

    monkeypatch.setattr(ocr_fallback, "easyocr", type("FakeEasyOCR", (), {"Reader": FakeReader}))

    first = ocr_fallback._run_ocr_easyocr(object())
    second = ocr_fallback._run_ocr_easyocr(object())

    assert first == [("Revenue 100", 0.91)]
    assert second == [("Revenue 100", 0.91)]
    assert created == [(("ch_sim", "en"), False)]


def test_profile_ocr_fallback_is_opt_in_for_large_report_scan(monkeypatch) -> None:
    import shutil
    from uuid import uuid4

    from ahcc.parser import ocr_fallback

    work_dir = Path("storage") / "test-artifacts" / f"profile-ocr-cap-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "large-low-coverage.pdf"

    try:
        pdf_path.write_bytes(b"%PDF-1.4\n% test")
        calls: list[dict] = []

        def fake_extract_metrics_via_ocr(file_path, side, max_pages=None, pages=None, unit=None, currency=None):
            calls.append({"file_path": file_path, "side": side, "max_pages": max_pages, "pages": pages})
            return []

        monkeypatch.setattr(ocr_fallback, "extract_metrics_via_ocr", fake_extract_metrics_via_ocr)
        monkeypatch.setattr(settings, "enable_profile_ocr_fallback", False, raising=False)

        doc = ReportDocument(
            doc_id="A",
            side=ReportSide.A_SHARE,
            file_path=str(pdf_path),
            total_pages=220,
            primary_language=Language.ZH,
            texts=[],
            tables=[],
            metadata={},
        )

        extract_metrics(doc)

        assert calls == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_profile_ocr_fallback_caps_large_report_scan_when_enabled(monkeypatch) -> None:
    import shutil
    from uuid import uuid4

    from ahcc.parser import ocr_fallback

    work_dir = Path("storage") / "test-artifacts" / f"profile-ocr-cap-{uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "large-low-coverage.pdf"

    try:
        pdf_path.write_bytes(b"%PDF-1.4\n% test")
        calls: list[dict] = []

        def fake_extract_metrics_via_ocr(file_path, side, max_pages=None, pages=None, unit=None, currency=None):
            calls.append({"file_path": file_path, "side": side, "max_pages": max_pages, "pages": pages})
            return []

        monkeypatch.setattr(ocr_fallback, "extract_metrics_via_ocr", fake_extract_metrics_via_ocr)
        monkeypatch.setattr(settings, "enable_profile_ocr_fallback", True, raising=False)

        doc = ReportDocument(
            doc_id="A",
            side=ReportSide.A_SHARE,
            file_path=str(pdf_path),
            total_pages=220,
            primary_language=Language.ZH,
            texts=[],
            tables=[],
            metadata={},
        )

        extract_metrics(doc)

        assert calls == [
            {
                "file_path": str(pdf_path),
                "side": ReportSide.A_SHARE,
                "max_pages": 40,
                "pages": None,
            }
        ]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_fast_core_path_enables_deepseek_semantic_review_by_default() -> None:
    assert settings.numeric_use_llm_semantic_review is True
    assert settings.event_use_llm_semantic_review is True


def test_orchestrator_runs_key_metric_tamper_check() -> None:
    from ahcc.orchestrator import Orchestrator

    a_revenue = _metric("revenue", 126_411.0, ReportSide.A_SHARE, page=17, unit=None)
    h_revenue = _metric("revenue", 126_460.0, ReportSide.H_SHARE, page=19, unit=None)
    profile_a = _profile(ReportSide.A_SHARE, metrics=[_occ(a_revenue)])
    profile_h = _profile(ReportSide.H_SHARE, metrics=[_occ(h_revenue)])

    diffs = asyncio.run(Orchestrator()._check_key_metric_tamper(profile_a, profile_h))

    assert any(diff.rule_id == "key_metric_exact_mismatch" for diff in diffs)


def test_legacy_history_upgrade_drops_numeric_and_event_false_positives(monkeypatch) -> None:
    from ahcc.storage import repository
    from ahcc.schemas import Diff, DiffSeverity, DiffType

    numeric_raw = Diff(
        diff_id="n-raw",
        diff_type=DiffType.NUMERIC,
        severity=DiffSeverity.HIGH,
        triage="real",
        canonical_key="total_assets",
        topic=LocalizedString(zh="总资产", en="Total assets"),
        summary=LocalizedString(zh="旧版数值误报", en="legacy numeric false positive"),
        a_value=100.0,
        h_value=120.0,
        delta=20.0,
        evidence=[_evidence(ReportSide.A_SHARE, 1, "A")],
    )
    event_raw = Diff(
        diff_id="e-raw",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="上海证券", en="Shanghai Securities"),
        summary=LocalizedString(zh="事件事实不一致：上海证券（差异字段：主体，匹配置信度 0.50）", en="event mismatch"),
        evidence=[_evidence(ReportSide.A_SHARE, 16, "董事会秘书"), _evidence(ReportSide.H_SHARE, 14, "董事会秘书")],
        rule_id="event_fact_match",
    )
    governance_status_raw = Diff(
        diff_id="e-governance",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="中信银行股份有限公司", en="corporate_governance"),
        summary=LocalizedString(zh="事件事实不一致：中信银行股份有限公司（差异字段：状态，匹配置信度 0.77）", en="event mismatch"),
        evidence=[
            Evidence(
                side=ReportSide.A_SHARE,
                page=152,
                bbox=(0, 0, 1, 1),
                snippet="本行对高级管理人员进行业绩考核评价，并对董事监事开展履职评价。",
                section="corporate_governance",
            ),
            Evidence(
                side=ReportSide.H_SHARE,
                page=136,
                bbox=(0, 0, 1, 1),
                snippet="本行为董事、监事和高级管理人员提供报酬，包括工资、津贴、住房公积金及年金。",
                section="corporate_governance",
            ),
        ],
        rule_id="event_fact_match",
    )
    amount_raw = Diff(
        diff_id="e-amount",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="中信集团", en="capital_reserve"),
        summary=LocalizedString(zh="事件事实不一致：中信集团（差异字段：金额/数量，匹配置信度 0.82）", en="event mismatch"),
        evidence=[
            _evidence(ReportSide.A_SHARE, 14, "2002 12 A 年 月，中信集团发行 40,000 2003 1 万股，31.75%。"),
            _evidence(ReportSide.H_SHARE, 10, "2002年12月发行40,000万股，募集资金人民币18亿元，总股数增至248,150万股，31.75%。"),
        ],
        rule_id="event_fact_match",
    )
    shenwan_dividend_true = Diff(
        diff_id="e-shenwan-dividend",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="利润分配及股息", en="dividend_distribution"),
        summary=LocalizedString(zh="事件事实不一致：利润分配及股息（差异字段：金额/数量，匹配置信度 0.80）", en="event mismatch"),
        evidence=[
            Evidence(
                side=ReportSide.A_SHARE,
                page=69,
                bbox=(0, 0, 1, 1),
                snippet="2020年度预案 以公司总股本25,039,944,560股为基数，每10股派发现金股利人民币1.00元，共计分配现金股利人民币2,503,994,456.00元。",
                section=None,
            ),
            Evidence(
                side=ReportSide.H_SHARE,
                page=453,
                bbox=(0, 0, 1, 1),
                snippet="Profit distribution: cash dividends of RMB1.00 per 10 shares with total dividends amounting to RMB25,039,945 thousand.",
                section="financial_statements",
            ),
        ],
        rule_id="event_fact_match",
    )
    shenwan_litigation_false = Diff(
        diff_id="e-shenwan-litigation",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="litigation", en="litigation"),
        summary=LocalizedString(zh="事件事实不一致：litigation（差异字段：金额/数量，匹配置信度 0.76）", en="event mismatch"),
        evidence=[
            Evidence(
                side=ReportSide.A_SHARE,
                page=88,
                bbox=(0, 0, 1, 1),
                snippet="判决支付业绩补偿款人民币1,000万元，并支付股权转让款人民币72,465,232.19元。",
                section=None,
            ),
            Evidence(
                side=ReportSide.H_SHARE,
                page=88,
                bbox=(0, 0, 1, 1),
                snippet="The judgment required compensation of RMB10 million and equity repurchase consideration of RMB72,465,232.19.",
                section="significant_events",
            ),
        ],
        rule_id="event_fact_match",
    )
    shenwan_status_false = Diff(
        diff_id="e-shenwan-status",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="债券发行及偿还", en="bond_events"),
        summary=LocalizedString(zh="事件事实不一致：债券发行及偿还（差异字段：状态，匹配置信度 0.71）", en="event mismatch"),
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=43, bbox=(0, 0, 1, 1), snippet="已发行短期债务工具40,505,069，长期债券118,167,945。", section=None),
            Evidence(side=ReportSide.H_SHARE, page=43, bbox=(0, 0, 1, 1), snippet="short-term debt instruments issued RMB40,505,069 and long-term bonds RMB118,167,945.", section=None),
        ],
        rule_id="event_fact_match",
    )
    shenwan_percentage_false = Diff(
        diff_id="e-shenwan-percentage",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key="event_fact_match",
        topic=LocalizedString(zh="债券发行及偿还", en="bond_events"),
        summary=LocalizedString(zh="事件事实不一致：债券发行及偿还（差异字段：比例，匹配置信度 0.65）", en="event mismatch"),
        evidence=[
            Evidence(
                side=ReportSide.A_SHARE,
                page=286,
                bbox=(0, 0, 1, 1),
                snippet="2016年度公司共发行两期债券，票面利率为3.45%。截至2019年12月31日，首期债券已兑付。",
                section=None,
            ),
            Evidence(
                side=ReportSide.H_SHARE,
                page=393,
                bbox=(0, 0, 1, 1),
                snippet="Notes to the consolidated financial statements 37 Cash and bank balances: interest rates were 0.90%, 1.80%, 2.85%, 3.22%, 3.92%, 5.00% and 5.60%.",
                section="financial_statements",
            ),
        ],
        rule_id="event_fact_match",
    )
    branch_raw = [
        Diff(
            diff_id=f"BRANCH_{name}",
            diff_type=DiffType.DISCLOSURE,
            severity=DiffSeverity.MEDIUM,
            triage="unresolved",
            canonical_key=None,
            topic=LocalizedString(zh=f"分支机构资产规模：{name}", en=f"Branch asset scale: {name}"),
            summary=LocalizedString(zh=f"A股报告该分支资产规模为 1 百万元，H股报告为 2 百万元", en="branch mismatch"),
            evidence=[
                Evidence(side=ReportSide.A_SHARE, page=30, bbox=(0, 0, 1, 1), snippet=f"{name} 10 1,000", section="分支机构"),
                Evidence(side=ReportSide.H_SHARE, page=31, bbox=(0, 0, 1, 1), snippet=f"{name} 10 2,000", section="分支机构"),
            ],
        )
        for name in ("北京分行", "上海分行", "广州分行", "深圳分行")
    ]
    low_confidence_branch_raw = Diff(
        diff_id="BRANCH_天津分行",
        diff_type=DiffType.DISCLOSURE,
        severity=DiffSeverity.MEDIUM,
        triage="real",
        canonical_key=None,
        topic=LocalizedString(zh="分支机构资产规模：天津分行", en="Branch asset scale: Tianjin branch"),
        summary=LocalizedString(zh="A股报告该分支资产规模为 1 百万元，H股报告为 2 百万元", en="branch mismatch"),
        evidence=[
            Evidence(side=ReportSide.A_SHARE, page=30, bbox=(0, 0, 1, 1), snippet="天津分行 10 1,000", section="分支机构"),
            Evidence(side=ReportSide.H_SHARE, page=31, bbox=(0, 0, 1, 1), snippet="天津分行 11 2,000", section="分支机构"),
        ],
    )
    current_numeric = [
        Diff(
            diff_id="n-expected",
            diff_type=DiffType.NUMERIC,
            severity=DiffSeverity.INFO,
            triage="expected",
            canonical_key="total_assets",
            topic=LocalizedString(zh="总资产", en="Total assets"),
            summary=LocalizedString(zh="当前数值已按口径降级", en="current numeric downgraded"),
            a_value=100.0,
            h_value=100.0,
            evidence=[_evidence(ReportSide.A_SHARE, 1, "A"), _evidence(ReportSide.H_SHARE, 1, "H")],
            rule_id="currency_converted_match",
        ),
        Diff(
            diff_id="n-unresolved",
            diff_type=DiffType.NUMERIC,
            severity=DiffSeverity.INFO,
            triage="unresolved",
            canonical_key="revenue",
            topic=LocalizedString(zh="营业收入", en="Revenue"),
            summary=LocalizedString(zh="当前数值待判断", en="current numeric unresolved"),
            a_value=200.0,
            h_value=220.0,
            evidence=[_evidence(ReportSide.A_SHARE, 2, "A"), _evidence(ReportSide.H_SHARE, 2, "H")],
            rule_id="context_mismatch",
        ),
    ]

    monkeypatch.setattr(repository, "_load_current_numeric_diffs", lambda job_id: current_numeric)

    summary, diffs = repository._upgrade_legacy_job(
        "job-1",
        {"result_version": 3, "real_diff_count": 9},
        [
            numeric_raw,
            event_raw,
            governance_status_raw,
            amount_raw,
            shenwan_dividend_true,
            shenwan_litigation_false,
            shenwan_status_false,
            shenwan_percentage_false,
            *branch_raw,
            low_confidence_branch_raw,
        ],
    )

    assert summary["result_version"] == 16
    assert summary["real_diff_count"] == 5
    assert summary["expected_diff_count"] == 1
    assert summary["unresolved_diff_count"] == 1
    assert summary["event_fact_diff_count"] == 1
    assert len(diffs) == 7
    assert sum(1 for diff in diffs if diff.triage == "real") == 5
    assert sum(1 for diff in diffs if diff.rule_id == "branch_asset_scale_match" and diff.triage == "real") == 4
    assert any(diff.diff_id == "e-shenwan-dividend" for diff in diffs)
    assert not any(diff.diff_id == "e-shenwan-litigation" for diff in diffs)
    assert not any(diff.diff_id == "e-shenwan-status" for diff in diffs)
    assert not any(diff.diff_id == "e-shenwan-percentage" for diff in diffs)
    assert all(
        diff.rule_id in {"event_fact_match", "branch_asset_scale_match"}
        for diff in diffs
        if diff.triage == "real"
    )
    assert not any(diff.diff_id == "BRANCH_天津分行" for diff in diffs)

    coverage = repository._sanitize_legacy_coverage_items(
        [
            {
                "category": "event",
                "status": "matched",
                "note": "事件双边披露，存在事实差异：状态",
                "a_evidence": [governance_status_raw.evidence[0].model_dump(mode="json")],
                "h_evidence": [governance_status_raw.evidence[1].model_dump(mode="json")],
            }
        ]
    )
    assert coverage[0]["note"] == "事件双边披露，匹配事实需复核：状态"
