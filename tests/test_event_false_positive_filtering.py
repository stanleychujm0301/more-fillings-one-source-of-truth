from __future__ import annotations

import pytest

from ahcc.check.coverage import run_event_checks_on_profiles
from ahcc.profile.models import ReportProfile
from ahcc.schemas import Evidence, Language, ReportDocument, ReportSide, TextSegment


def _profile(side: ReportSide) -> ReportProfile:
    return ReportProfile(
        doc_id=f"{side.value}-doc",
        side=side,
        total_pages=10,
        metrics=[],
        narratives=[],
        structure=[],
        metadata={},
    )


def _profiles_with_event_texts(
    a_text: str,
    h_text: str,
    section: str = "notes",
    h_language: Language = Language.ZH,
) -> tuple[ReportProfile, ReportProfile]:
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=100,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-event",
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
        primary_language=h_language,
        texts=[
            TextSegment(
                segment_id="h-event",
                page=80,
                bbox=(0, 0, 1, 1),
                text=h_text,
                language=h_language,
                section=section,
            )
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc
    return profile_a, profile_h


def test_unrelated_profit_distribution_events_not_real_diff() -> None:
    """光大银行样本质疑：不同利润分配方案不应被匹配为同一事项并生成差异。"""
    a_text = (
        "利润分配：本公司董事会于2025年6月27日决议向全体股东派发现金股利，"
        "以总股本200亿股为基数，每10股派息1.50元，共计股利30亿元。"
    )
    h_text = (
        "利润分配：董事会于2024年8月11日决议向全体股东派发现金股利，"
        "以总股本250亿股为基数，每10股派息1.20元，共计股利3亿元。"
    )
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="significant_events")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert not any(diff.triage == "real" for diff in diffs)


def test_unrelated_bond_issues_not_real_diff() -> None:
    """不同债券发行的金额不应被当作同一笔债券发行差异。"""
    a_text = "本集团2025年3月公开发行长期公司债券，发行金额为人民币400亿元。"
    h_text = "本集团2024年11月非公开发行短期公司债券，发行金额为人民币5亿元。"
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert not any(diff.triage == "real" for diff in diffs)


def test_magnitude_share_count_mismatch_not_real_diff() -> None:
    """股份数量数量级相差过大时，应视为不同事项而非真实差异。"""
    a_text = "股份变动：截至报告期末，公司总股本为200,000,000股。"
    h_text = "股份变动：截至2024年12月31日，公司总股本为11,167,000,000股。"
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="share_changes")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert not any(diff.triage == "real" for diff in diffs)


def test_related_party_without_counterparty_not_matched_to_specific_one() -> None:
    """无交易对手方的通用关联交易金额段落，不应与具体关联交易对齐生成差异。"""
    a_text = "本行与阳光科技有限公司发生关联交易，交易金额为人民币100万元。"
    h_text = "本行与星河科技有限公司发生关联交易，交易金额为人民币1.2亿元。"
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="significant_events")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert not any(diff.triage == "real" for diff in diffs)


def test_bond_topic_not_labeled_as_related_party() -> None:
    """债券发行事项不应在 diff 中显示为关联交易。"""
    a_text = "本集团发行公司债券，发行金额为人民币200亿元。"
    h_text = "本集团发行公司债券，发行金额为人民币150亿元。"
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    _, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    # 即使因某种原因产生差异，也不应出现“关联交易”字样
    for diff in diffs:
        assert "关联" not in diff.summary.zh
        assert "related party" not in diff.summary.en.lower()


def test_real_event_diff_with_same_identity_anchor_still_detected() -> None:
    """相同日期、相同对手方、不同金额的真实事件差异仍应被召回。"""
    a_text = "2025年3月1日，本行与阳光科技有限公司签订战略合作协议，金额为100万元。"
    h_text = "2025年3月1日，本行与阳光科技有限公司签订战略合作协议，金额为120万元。"
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="significant_events")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert any(item.status == "matched" for item in coverage)
    assert len(diffs) == 1
    assert diffs[0].triage == "real"
    assert diffs[0].rule_id == "event_fact_match"


def test_multiple_similar_events_create_ambiguous_matches_not_real_diffs() -> None:
    """同 domain 存在多个相似事件时，模棱两可的配对不应生成真实差异。"""
    a_texts = [
        "本集团发行2025年度第一期公司债券，发行金额为人民币20亿元。",
        "本集团发行2025年度第二期公司债券，发行金额为人民币30亿元。",
        "本集团发行2025年度第三期公司债券，发行金额为人民币40亿元。",
    ]
    h_texts = [
        "本集团发行2024年度第一期公司债券，发行金额为人民币15亿元。",
        "本集团发行2024年度第二期公司债券，发行金额为人民币28亿元。",
        "本集团发行2024年度第三期公司债券，发行金额为人民币35亿元。",
    ]
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=100,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id=f"a-bond-{i}",
                page=30 + i,
                bbox=(0, 0, 1, 1),
                text=text,
                language=Language.ZH,
                section="notes",
            )
            for i, text in enumerate(a_texts)
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=100,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id=f"h-bond-{i}",
                page=80 + i,
                bbox=(0, 0, 1, 1),
                text=text,
                language=Language.ZH,
                section="notes",
            )
            for i, text in enumerate(h_texts)
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    # 允许存在 matched coverage，但不应有真实差异
    assert any(item.status == "matched" for item in coverage)
    assert not any(diff.triage == "real" for diff in diffs)


def test_everbright_bank_bond_false_positives_blocked() -> None:
    """光大银行 A+H 年报样本质疑：不同年份/名称/期次的债券不应生成真实差异。

    这些片段来自真实 PDF 证据（job 5bb856e0），仅含债券基础信息而无明确期次时，
    必须依赖签名（年份+名称）区分，不能仅凭金额/利率角色相同就判差异。
    """
    a_texts = [
        "于 2022 年 2 月 17 日发行的 2022 年小型微型企业贷款专项金融债券，金额为人民币 400.00 亿元，期限为 3 年，票面利率为 2.73%。",
        "于 2023 年 11 月 8 日发行的 2023 年非公开定向债务融资工具，金额为人民币 30.00 亿元，期限为 3 年，票面利率为 2.85%。",
        "于 2024 年 8 月 23 日发行的 2024 年公司债券第二期，固定利率式公司债券，金额为人民币 250.00 亿元，期限为 3 年，票面利率为 2.07%。",
    ]
    h_texts = [
        "于 2023 年 4 月 10 日发行的 2023 年金融债券，金额为人民币 50.00 亿元，期限为 15 年，票面利率为 3.64%。",
        "于 2024 年 3 月 4 日发行的 2024 年非公开定向债务融资工具第一期，固定利率式债务融资工具，金额为人民币 20.00 亿元，期限为 3 年，票面利率为 2.45%。",
        "于 2024 年 8 月 23 日发行的 2024 年乡村振兴债券第一期，固定利率式公司债券，金额为人民币 50.00 亿元，期限为 3 年，票面利率为 2.05%。",
    ]
    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=300,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id=f"a-bond-{i}",
                page=260 + i,
                bbox=(0, 0, 1, 1),
                text=text,
                language=Language.ZH,
                section="notes",
            )
            for i, text in enumerate(a_texts)
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=300,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id=f"h-bond-{i}",
                page=265 + i,
                bbox=(0, 0, 1, 1),
                text=text,
                language=Language.ZH,
                section="notes",
            )
            for i, text in enumerate(h_texts)
        ],
    )
    profile_a = _profile(ReportSide.A_SHARE)
    profile_h = _profile(ReportSide.H_SHARE)
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert not any(diff.triage == "real" for diff in diffs)
    # 同一年份+名称的债券仍应正常匹配为 coverage
    assert any(item.status == "matched" for item in coverage)


def test_financial_statement_note_number_not_share_count_diff() -> None:
    """财务报告附注/章节编号不应被误标为股份数量差异。"""
    a_text = "22.3 权益工具的相关信息在本财务报表附注中披露，需进一步确认。"
    h_text = "17.30 权益工具的相关信息在本财务报表附注中披露，需进一步确认。"
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="notes")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert not any(diff.triage == "real" and "股份" in diff.summary.zh for diff in diffs)
    assert not any(
        diff.triage == "real"
        and diff.diff_explanation
        and any(item.role in {"share_count", "dividend_base_share_count"} for item in diff.diff_explanation.items)
        for diff in diffs
    )


def test_real_share_count_diff_still_detected() -> None:
    """真实的大额股本/股份数量差异仍应被召回。"""
    a_text = "本集团总股本为 25,039,945 千股。"
    h_text = "The Group total share capital was 26,000,000 thousand shares."
    profile_a, profile_h = _profiles_with_event_texts(a_text, h_text, section="share_changes")

    coverage, diffs = run_event_checks_on_profiles(profile_a, profile_h)

    assert any(item.status == "matched" for item in coverage)
    assert any(diff.triage == "real" for diff in diffs)
    assert any(
        diff.diff_explanation
        and any(item.role in {"share_count", "dividend_base_share_count"} for item in diff.diff_explanation.items)
        for diff in diffs
    )
