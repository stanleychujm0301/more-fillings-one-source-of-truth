from __future__ import annotations

import asyncio

from ahcc.check.branch_disclosure import compare_branch_tables
from ahcc.check.coverage import (
    _amount_mentions,
    _normalize_text,
    build_disclosure_coverage,
    run_event_checks_on_profiles,
)
from ahcc.check.disclosure import run_disclosure_checks_on_profiles
from ahcc.check.numeric import run_numeric_checks_on_profiles
from ahcc.profile import summarize_profile
from ahcc.profile.compare import compare_metrics, compare_profiles
from ahcc.profile.extract_metrics import extract_metrics
from ahcc.profile.extract_narratives import extract_narratives
from ahcc.profile.extract_structure import extract_structure
from ahcc.profile.models import ChapterNode, MetricItem, MetricOccurrences, NarrativeBlock, ReportProfile
from ahcc.schemas import (
    Currency,
    Evidence,
    ExtractionAudit,
    FinancialTable,
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
) -> MetricItem:
    return MetricItem(
        canonical_key=key,
        name=LocalizedString(zh=key, en=key),
        value=value,
        value_text=str(value),
        unit=unit,
        currency=Currency.CNY,
        page=page,
        evidence=Evidence(
            side=side,
            page=page,
            bbox=(0.0, 0.0, 1.0, 1.0),
            snippet=snippet or f"{key}: {value}",
            section=section,
        ),
        confidence=confidence,
        source="table",
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


def test_branch_table_batch_mismatch_with_stable_rows_remains_real_diffs() -> None:
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


def test_branch_table_clear_single_row_mismatch_remains_real_diff() -> None:
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
            triage="real",
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

    assert summary["result_version"] == 11
    assert summary["real_diff_count"] == 5
    assert summary["expected_diff_count"] == 1
    assert summary["unresolved_diff_count"] == 1
    assert summary["event_fact_diff_count"] == 1
    assert len(diffs) == 7
    assert sum(1 for diff in diffs if diff.triage == "real") == 5
    assert any(diff.diff_id == "e-shenwan-dividend" for diff in diffs)
    assert not any(diff.diff_id == "e-shenwan-litigation" for diff in diffs)
    assert not any(diff.diff_id == "e-shenwan-status" for diff in diffs)
    assert not any(diff.diff_id == "e-shenwan-percentage" for diff in diffs)
    assert all(
        diff.rule_id in {"branch_asset_scale_match", "event_fact_match"}
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
