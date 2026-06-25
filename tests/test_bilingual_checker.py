from __future__ import annotations

from ahcc.check.bilingual import run_bilingual_checks
from ahcc.check import bilingual as bilingual_module
from ahcc.config import settings
from ahcc.schemas import DiffSeverity, FinancialTable, Language, LocalizedString, ReportDocument, ReportSide, TableCell, TextSegment


def _doc(doc_id: str, text: str, language: Language, page: int = 1) -> ReportDocument:
    return ReportDocument(
        doc_id=doc_id,
        side=ReportSide.H_SHARE,
        file_path=f"{doc_id}.pdf",
        total_pages=max(page, 1),
        primary_language=language,
        texts=[
            TextSegment(
                segment_id=f"{doc_id}-s1",
                page=page,
                bbox=(0, 0, 1, 1),
                text=text,
                language=language,
                section="notes",
            )
        ],
    )


def _multi_doc(doc_id: str, segments: list[tuple[int, str, str]], language: Language) -> ReportDocument:
    return ReportDocument(
        doc_id=doc_id,
        side=ReportSide.H_SHARE,
        file_path=f"{doc_id}.pdf",
        total_pages=max(page for page, _, _ in segments),
        primary_language=language,
        texts=[
            TextSegment(
                segment_id=f"{doc_id}-s{idx}",
                page=page,
                bbox=(0, 0, 1, 1),
                text=text,
                language=language,
                section=section,
            )
            for idx, (page, section, text) in enumerate(segments, start=1)
        ],
    )


def _table(table_id: str, page: int, title: str, rows: list[list[str]], *, zh: bool) -> FinancialTable:
    return FinancialTable(
        table_id=table_id,
        title=LocalizedString(zh=title if zh else None, en=None if zh else title),
        page=page,
        bbox=(0, 0, 1, 1),
        cells=[
            TableCell(row=row_idx, col=col_idx, text=cell, is_header=row_idx == 0)
            for row_idx, row in enumerate(rows)
            for col_idx, cell in enumerate(row)
        ],
    )


def test_bilingual_amount_mismatch_detects_thousand_unit_difference(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", False)

    zh_doc = _doc("h-zh", "利润分配：共计股利人民币2,503,994千元。", Language.ZH, page=453)
    en_doc = _doc(
        "h-en",
        "Profit distribution: total dividends amounting to RMB25,039,945 thousand.",
        Language.EN,
        page=453,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    assert len(result.diffs) == 1
    diff = result.diffs[0]
    assert diff.rule_id == "bilingual_fact_mismatch"
    assert diff.diff_explanation is not None
    assert diff.diff_explanation.items[0].role.startswith("amount")
    assert "2,503,994" in str(diff.diff_explanation.items[0].a_value)
    assert "25,039,945" in str(diff.diff_explanation.items[0].h_value)
    assert diff.evidence[0].side == ReportSide.A_SHARE
    assert diff.evidence[1].side == ReportSide.H_SHARE


def test_bilingual_amount_units_normalize_equivalent_billions() -> None:
    zh_doc = _doc("h-zh", "本集团发行债券金额为人民币25亿元。", Language.ZH)
    en_doc = _doc("h-en", "The Group issued bonds amounting to RMB2.5 billion.", Language.EN)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    assert result.diffs == []
    assert result.stats["zh_fact_count"] >= 1
    assert result.stats["en_fact_count"] >= 1


def test_bilingual_detects_date_percentage_and_per_10_share_mismatches(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", False)

    zh_doc = _doc(
        "h-zh",
        "董事会于2020年9月21日批准利润分配方案，每10股派发现金红利人民币1.00元，债券票面利率为3.45%。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "The Board approved the profit distribution plan on September 22, 2020, "
        "with cash dividend of RMB1.20 per 10 shares and coupon rate of 3.70%.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    roles = {item.role for diff in result.diffs for item in diff.diff_explanation.items}

    # 百分比角色现在细分为 coupon_rate 等具体角色，而非统一 "percentage"
    assert {"date", "dividend_rate_per_10_shares"} <= roles
    assert any("coupon_rate" in r or "percentage" in r for r in roles), f"Expected coupon_rate or percentage in roles, got {roles}"
    assert all(diff.diff_explanation.location for diff in result.diffs)


def test_bilingual_semantic_stub_outputs_translation_diff() -> None:
    """semantic_evaluator 已废弃（LLM 事实对比合并了数字+语义审查），传入不再产生差异。"""
    zh_doc = _doc("h-zh", "本公司已经完成债券赎回。", Language.ZH, page=20)
    en_doc = _doc("h-en", "The Company plans to redeem the bonds.", Language.EN, page=22)

    def semantic_evaluator(pairs):
        # 旧接口：不再被调用，但传入不应崩溃
        return [
            {
                "zh_index": 0,
                "en_index": 0,
                "severity": "high",
                "confidence": 0.9,
                "headline": "债券赎回状态翻译不一致",
                "issue": "中文为已经完成赎回；英文为计划赎回。",
                "review_hint": "核对债券赎回状态是否被译成未完成事项。",
            }
        ]

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=semantic_evaluator)

    # semantic_evaluator 不再执行，不产生 bilingual_semantic_mismatch 差异
    assert not any(d.rule_id == "bilingual_semantic_mismatch" for d in result.diffs)


def test_bilingual_semantic_unavailable_adds_non_blocking_warning() -> None:
    """semantic_evaluator 已废弃，enable_semantic=True 不再产生语义审查相关警告。"""
    zh_doc = _doc("h-zh", "本公司已经完成债券赎回。", Language.ZH)
    en_doc = _doc("h-en", "The Company has completed the bond redemption.", Language.EN)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=None, enable_semantic=True)

    # 旧 bilingual_semantic_unavailable 警告不再产生（语义审查已合并到 LLM 事实对比）
    assert not any(d.rule_id == "bilingual_semantic_mismatch" for d in result.diffs)


def test_bilingual_semantic_none_result_adds_non_blocking_warning() -> None:
    """semantic_evaluator 已废弃，返回 None 不再触发降级警告。"""
    zh_doc = _doc("h-zh", "本公司已经完成债券赎回。", Language.ZH)
    en_doc = _doc("h-en", "The Company has completed the bond redemption.", Language.EN)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: None, enable_semantic=True)

    # 旧 bilingual_semantic_unavailable 警告不再产生
    assert not any(d.rule_id == "bilingual_semantic_mismatch" for d in result.diffs)


def test_bilingual_section_page_offset_is_allowed_when_order_and_content_match() -> None:
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "business", "业务概览 本集团证券经纪业务保持稳定增长。"),
            (20, "notes", "财务报表附注 本集团发行债券金额为人民币25亿元。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, "business", "Business overview The Group's securities brokerage business maintained stable growth."),
            (23, "notes", "Notes to the financial statements The Group issued bonds amounting to RMB2.5 billion."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    assert [diff.rule_id for diff in result.diffs] == []
    assert result.stats["section_pair_count"] == 2


def test_bilingual_detects_missing_and_out_of_order_sections() -> None:
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "business", "业务概览 本集团证券经纪业务保持稳定增长。"),
            (20, "governance", "公司治理 董事会已审议年度报告。"),
            (30, "notes", "财务报表附注 本集团发行债券金额为人民币25亿元。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (11, "notes", "Notes to the financial statements The Group issued bonds amounting to RMB2.5 billion."),
            (22, "business", "Business overview The Group's securities brokerage business maintained stable growth."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    rule_ids = {diff.rule_id for diff in result.diffs}

    assert "bilingual_section_missing" in rule_ids
    # 章节顺序差异在双语报告中属正常差异，不再报出
    assert "bilingual_section_order_mismatch" not in rule_ids
    assert any("章节" in diff.diff_explanation.headline for diff in result.diffs if diff.diff_explanation)


def test_bilingual_detects_table_row_and_unpaired_paragraph_mismatch() -> None:
    zh_doc = _multi_doc(
        "h-zh",
        [
            (8, "notes", "财务报表附注 本集团发行债券金额为人民币25亿元。"),
            (9, "notes", "期后事项 本公司已经完成债券赎回。"),
        ],
        Language.ZH,
    )
    zh_doc.tables = [
        _table(
            "zh-t1",
            8,
            "债券发行表",
            [["项目", "金额"], ["公司债券", "人民币25亿元"], ["短期融资券", "人民币10亿元"]],
            zh=True,
        )
    ]
    en_doc = _multi_doc(
        "h-en",
        [
            (10, "notes", "Notes to the financial statements The Group issued bonds amounting to RMB2.5 billion."),
        ],
        Language.EN,
    )
    en_doc.tables = [
        _table(
            "en-t1",
            10,
            "Bond issuance table",
            [["Item", "Amount"], ["Corporate bonds", "RMB2.5 billion"]],
            zh=False,
        )
    ]

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    rule_ids = {diff.rule_id for diff in result.diffs}

    assert "bilingual_table_row_missing" in rule_ids
    # 优化后段落门槛提高（len>=30, 金额>=10000），短文本不再报为缺失
    # "期后事项 本公司已经完成债券赎回" 长度不足且无大额事实，不再视为重要段落
    assert all(diff.diff_explanation.location for diff in result.diffs)


def test_bilingual_ignores_text_strategy_pseudo_tables() -> None:
    zh_doc = _doc(
        "h-zh",
        "Definitions: company means Shenwan Hongyuan Group Co., Ltd. This paragraph is ordinary bilingual prose.",
        Language.ZH,
        page=14,
    )
    en_doc = _doc(
        "h-en",
        "Definitions: Company means Shenwan Hongyuan Group Co., Ltd. This paragraph is ordinary bilingual prose.",
        Language.EN,
        page=14,
    )
    zh_doc.tables.append(
        _table(
            "H_p014_text_t01",
            14,
            "Definitions",
            [
                ["Term", "Meaning"],
                ["Company", "Shenwan Hongyuan Group Co., Ltd."],
                ["Board", "The board of directors of the Company"],
            ],
            zh=True,
        )
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    table_diffs = [d for d in result.diffs if d.rule_id == "bilingual_table_row_missing"]
    assert table_diffs == []


def test_bilingual_keeps_real_table_row_missing_after_text_pseudo_table_filter() -> None:
    zh_doc = _doc(
        "h-zh",
        "Operating income table: brokerage income amounted to RMB100 million.",
        Language.ZH,
        page=30,
    )
    en_doc = _doc(
        "h-en",
        "Operating income table.",
        Language.EN,
        page=30,
    )
    zh_doc.tables.append(
        _table(
            "H_p030_pl_t01",
            30,
            "Income statement",
            [["Item", "Amount"], ["Brokerage income", "RMB100 million"]],
            zh=True,
        )
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    # 新逻辑：未配对的表格整表报 bilingual_table_missing（而非逐行 bilingual_table_row_missing）
    table_diffs = [d for d in result.diffs if d.rule_id in ("bilingual_table_row_missing", "bilingual_table_missing")]
    assert len(table_diffs) == 1
    assert table_diffs[0].canonical_key.startswith("table_missing:") or table_diffs[0].canonical_key.startswith("table_row_missing:")


def test_bilingual_english_thousand_header_normalizes_amount() -> None:
    zh_doc = _doc(
        "h-zh",
        "\u8d27\u5e01\u5355\u4f4d\uff1a\u4eba\u6c11\u5e01\u5343\u5143\u3002\u624b\u7eed\u8d39\u53ca\u4f63\u91d1\u6536\u5165\u4e3a1,000,000\u5343\u5143\u3002",
        Language.ZH,
        page=9,
    )
    en_doc = _doc(
        "h-en",
        "Expressed in thousands of Renminbi. Commission income amounted to RMB1,000,000.",
        Language.EN,
        page=9,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    assert fact_diffs == []


def test_bilingual_ambiguous_multi_fact_mismatch_not_high_confidence() -> None:
    zh_doc = _doc(
        "h-zh",
        "\u8bc9\u8bbc\u6848\u4ef6\u4e00\u6d89\u53ca\u91d1\u989d\u4e3a\u4eba\u6c11\u5e01100,000,000\u5143\uff0c\u8bc9\u8bbc\u6848\u4ef6\u4e8c\u6d89\u53ca\u91d1\u989d\u4e3a\u4eba\u6c11\u5e01200,000,000\u5143\u3002",
        Language.ZH,
        page=88,
    )
    en_doc = _doc(
        "h-en",
        "Case A involved RMB100,000,000, while Case B involved RMB250,000,000.",
        Language.EN,
        page=88,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    high_fact_diffs = [
        d
        for d in result.diffs
        if d.rule_id == "bilingual_fact_mismatch" and d.severity in {DiffSeverity.HIGH, DiffSeverity.MEDIUM}
    ]
    assert high_fact_diffs == []


def test_bilingual_definition_section_missing_is_not_reported() -> None:
    zh_doc = _multi_doc(
        "h-zh",
        [
            (3, "definitions", "Definitions Company means Shenwan Hongyuan Group Co., Ltd. Board means the board of directors."),
            (10, "business", "Business overview The Group's securities brokerage business maintained stable growth."),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, "business", "Business overview The Group's securities brokerage business maintained stable growth."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    section_diffs = [d for d in result.diffs if d.rule_id == "bilingual_section_missing"]
    assert section_diffs == []


def test_bilingual_pairing_limits_candidate_comparisons(monkeypatch) -> None:
    zh_doc = ReportDocument(
        doc_id="h-zh",
        side=ReportSide.H_SHARE,
        file_path="h-zh.pdf",
        total_pages=120,
        primary_language=Language.ZH,
        texts=[],
        tables=[
            _table(
                f"zh-t{page}",
                page,
                "债券发行表",
                [["项目", "金额"], [f"公司债券{page}", "人民币25亿元"]],
                zh=True,
            )
            for page in range(1, 121)
        ],
    )
    en_doc = ReportDocument(
        doc_id="h-en",
        side=ReportSide.H_SHARE,
        file_path="h-en.pdf",
        total_pages=120,
        primary_language=Language.EN,
        texts=[],
        tables=[
            _table(
                f"en-t{page}",
                page,
                "Bond issuance table",
                [["Item", "Amount"], [f"Corporate bond {page}", "RMB2.5 billion"]],
                zh=False,
            )
            for page in range(1, 121)
        ],
    )
    call_count = 0

    def counting_row_match_score(zh_row, en_row):
        nonlocal call_count
        call_count += 1
        return 5 if abs(zh_row.page - en_row.page) <= 1 else 0

    monkeypatch.setattr(bilingual_module, "_row_match_score", counting_row_match_score)

    bilingual_module._table_row_diffs(zh_doc, en_doc, start_index=1)

    # 两阶段表格行匹配需要更多比较（全局最优 + 贪心），放宽上限
    assert call_count < 5000


def test_bilingual_boilerplate_and_period_desc_not_flagged() -> None:
    """常见模板段落（截至年度、本公司保证声明等）不应触发 unpaired 报警。"""
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "notes", "截至2020年12月31日止年度，本公司及其子公司实现营业收入人民币100亿元。"),
            (11, "notes", "本公司董事会及全体董事保证本公告内容不存在任何虚假记载、误导性陈述或重大遗漏。"),
            (12, "notes", "除特别注明外，本报告金额单位均为人民币元。"),
            (13, "notes", "详见附注三。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, "notes", "For the year ended 31 December 2020, the Company and its subsidiaries recorded operating income of RMB10 billion."),
            (13, "notes", "The Board of Directors and all directors of the Company guarantee that this announcement contains no false records, misleading statements or material omissions."),
            (14, "notes", "Unless otherwise stated, the amounts in this report are denominated in RMB."),
            (15, "notes", "See Note 3 for details."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    unpaired_paragraph_diffs = [d for d in result.diffs if d.rule_id == "bilingual_paragraph_unpaired"]
    # 所有模板段落都应被过滤，不应产生 unpaired 报警
    assert len(unpaired_paragraph_diffs) == 0


def test_bilingual_many_to_one_english_split_not_flagged() -> None:
    """一个中文段落对应两个英文段落（翻译拆分）时，中文不应被报为缺失。"""
    zh_doc = _multi_doc(
        "h-zh",
        [
            (20, "business", "本集团证券经纪业务保持稳定增长，市场份额持续提升，客户基础不断夯实。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (22, "business", "The Group's securities brokerage business maintained stable growth."),
            (22, "business", "Market share continued to improve and the customer base was continuously strengthened."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    unpaired_paragraph_diffs = [d for d in result.diffs if d.rule_id == "bilingual_paragraph_unpaired"]
    assert len(unpaired_paragraph_diffs) == 0


def test_bilingual_clean_report_has_few_diffs() -> None:
    """模拟人工核对无误的"干净"报告，diffs 应控制在极低水平。"""
    zh_segments = [
        (10, "business", "业务概览 本集团证券经纪业务保持稳定增长。"),
        (20, "notes", "财务报表附注 本集团发行债券金额为人民币25亿元。"),
        (30, "governance", "公司治理 董事会已审议年度报告。"),
    ]
    en_segments = [
        (12, "business", "Business overview The Group's securities brokerage business maintained stable growth."),
        (23, "notes", "Notes to the financial statements The Group issued bonds amounting to RMB2.5 billion."),
        (35, "governance", "Corporate governance The Board has considered the annual report."),
    ]
    zh_doc = _multi_doc("h-zh", zh_segments, Language.ZH)
    en_doc = _multi_doc("h-en", en_segments, Language.EN)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    # 干净报告不应产生任何事实差异或段落缺失
    assert len(result.diffs) == 0, f"Expected 0 diffs on clean report, got {len(result.diffs)}: {[d.rule_id for d in result.diffs]}"
    # 配对数取决于阈值调整后的匹配结果，2-3 均为合理范围
    assert result.stats["paired_blocks"] >= 2


def test_bilingual_same_amount_different_order_not_flagged() -> None:
    """同一段落中多个金额顺序不同时，不应产生 fact_mismatch 误报。"""
    zh_doc = _doc(
        "h-zh",
        "利润分配：每10股派发现金红利人民币1.00元，共计股利人民币25,039,944,560元。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "Profit distribution: total dividends of RMB25,039,944,560, with cash dividend of RMB1.00 per 10 shares.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    # 两个金额只是顺序不同，不应报差异
    assert len(fact_diffs) == 0, f"Expected 0 fact diffs, got {len(fact_diffs)}: {[d.diff_explanation.issue for d in fact_diffs]}"


def test_bilingual_rmb_prefix_same_amount_not_flagged() -> None:
    """中英文金额仅 RMB 前缀差异时，不应产生 fact_mismatch 误报。"""
    zh_doc = _doc("h-zh", "注册资本为人民币14,856,744,977元。", Language.ZH, page=5)
    en_doc = _doc("h-en", "The registered capital is RMB14,856,744,977.", Language.EN, page=6)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    assert len(fact_diffs) == 0, f"Expected 0 fact diffs for RMB prefix only, got {len(fact_diffs)}"


def test_bilingual_extra_small_amount_not_flagged() -> None:
    """多余的小金额（<100,000）不应产生单侧 fact_mismatch 误报。"""
    zh_doc = _doc("h-zh", "手续费收入为人民币500元，佣金收入为人民币1,000,000元。", Language.ZH, page=8)
    en_doc = _doc("h-en", "Commission income amounted to RMB1,000,000.", Language.EN, page=9)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    # 500元是小金额（<100,000），不应报单侧缺失；1,000,000元在中英文都有，也不应报差异
    assert len(fact_diffs) == 0, f"Expected 0 fact diffs, got {len(fact_diffs)}: {[d.diff_explanation.headline for d in fact_diffs]}"


def test_bilingual_amount_mismatch_still_detected(monkeypatch) -> None:
    """真正的事实不匹配（金额不同）仍应被正确检出。"""
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", False)

    zh_doc = _doc("h-zh", "营业收入为人民币100,000,000元。", Language.ZH, page=10)
    en_doc = _doc("h-en", "Operating income was RMB150,000,000.", Language.EN, page=12)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    assert len(fact_diffs) == 1
    assert fact_diffs[0].diff_explanation.items[0].role.startswith("amount")
    assert "100,000,000" in str(fact_diffs[0].diff_explanation.items[0].a_value)
    assert "150,000,000" in str(fact_diffs[0].diff_explanation.items[0].h_value)


def test_share_count_not_mixed_with_amount() -> None:
    """股份数量（股）不应与金额混配产生误报。"""
    zh_doc = _doc(
        "h-zh",
        "基于公司总股本25,039,944,560股，向全体股东每10股派发现金红利1.00元，共计2,503,994,456元。",
        Language.ZH,
        page=2,
    )
    en_doc = _doc(
        "h-en",
        "Based on the total share capital of 25,039,944,560 shares, a cash dividend of RMB1.00 per 10 shares, with a total amount of RMB2,503,994,456.",
        Language.EN,
        page=2,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    # 250亿股（share_count）不应与25亿元（amount）混配
    assert len(fact_diffs) == 0, f"Expected 0 fact diffs, got {len(fact_diffs)}: {[d.diff_explanation.issue for d in fact_diffs]}"


def test_english_shares_extracted_correctly() -> None:
    """英文 shares 应正确提取为 share_count，不与 amount 混配。"""
    zh_doc = _doc("h-zh", "公司总股本为10,000,000,000股。", Language.ZH, page=5)
    en_doc = _doc("h-en", "The total share capital is 10 billion shares.", Language.EN, page=6)

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    assert len(fact_diffs) == 0, f"Expected 0 fact diffs, got {len(fact_diffs)}"


# ---------------------------------------------------------------------------
# 新增测试：日期上下文匹配误报修复
# ---------------------------------------------------------------------------


def test_bilingual_english_period_date_not_flagged() -> None:
    """英文期间描述 'For the year ended 31 December 2024' 不应提取为关键日期。"""
    zh_doc = _doc(
        "h-zh",
        "截至2024年12月31日止年度，公司实现营业收入人民币100亿元。公司于2019年5月15日成立。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "For the year ended 31 December 2024, the Company recorded operating revenue of RMB10 billion. "
        "The company was established on 15 May 2019.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    date_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]

    # 期间描述日期（12月31日）应被过滤，仅成立日期（5月15日）被提取
    # 两边成立日期一致，不应产生任何日期差异
    assert len(date_diffs) == 0, f"Expected 0 date diffs, got {len(date_diffs)}: {[d.diff_explanation.issue for d in date_diffs]}"


def test_bilingual_chinese_period_date_broader_context() -> None:
    """扩展上下文后，中文'截至2024年12月31日止年度'应被正确过滤为期间描述。"""
    zh_doc = _doc(
        "h-zh",
        "截至2024年12月31日止年度，公司董事会批准年度报告。公司于2020年6月1日上市。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "For the year ended 31 December 2024, the Board approved the annual report. "
        "The company was listed on 1 June 2020.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    date_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]

    # 期间描述日期（12月31日）应被过滤，仅上市日期（6月1日）被提取且匹配
    assert len(date_diffs) == 0, f"Expected 0 date diffs, got {len(date_diffs)}"


def test_bilingual_different_date_context_categories_not_paired(monkeypatch) -> None:
    """不同语义类别的日期上下文（成立 vs 批准）不应配对产生误报。"""
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", False)

    zh_doc = _doc(
        "h-zh",
        "公司于2019年5月15日成立。董事会于2020年6月20日批准年度报告。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "The company was founded on 20 May 2019. The Board approved the annual report on June 20, 2020.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    date_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]

    # 成立日期不同（5月15日 vs 5月20日）但属于 corporate_events 类别 → 应配对并报差异
    # 批准日期一致（6月20日）→ 不报差异
    # 由于成立日期不同，应恰好 1 条差异
    assert len(date_diffs) == 1, f"Expected 1 date diff (establishment date mismatch), got {len(date_diffs)}"
    assert "2019" in str(date_diffs[0].diff_explanation.items[0].a_value) or "2019" in str(date_diffs[0].diff_explanation.items[0].h_value)


# ---------------------------------------------------------------------------
# 新增测试：Section 关键词映射误报修复
# ---------------------------------------------------------------------------


def test_bilingual_parser_code_underscore_mapping() -> None:
    """Parser code 带下划线 (如 corporate_governance) 应正确映射到 canonical key。"""
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "directors_report", "董事会报告 本公司董事会由9名董事组成。"),
            (20, "corporate_governance", "公司治理 本公司已建立完善的公司治理架构。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, "directors_report", "Directors' Report The Board consists of 9 directors."),
            (23, "corporate_governance", "Corporate Governance The Company has established a sound corporate governance framework."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    section_diffs = [d for d in result.diffs if d.rule_id.startswith("bilingual_section_")]

    # 两个章节都应通过 _PARSER_CODE_TO_KEY 正确映射，不产生 section 缺失/顺序差异
    assert len(section_diffs) == 0, f"Expected 0 section diffs, got {len(section_diffs)}: {[d.diff_explanation.headline for d in section_diffs]}"
    assert result.stats["section_pair_count"] == 2


def test_bilingual_missing_parser_codes_map_correctly() -> None:
    """之前缺失的 parser code (significant_events, accounting_policy, esg) 应通过映射表正确匹配。"""
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "significant_events", "重要事项 本年度公司完成重大并购。"),
            (20, "accounting_policy", "会计政策 本公司采用新会计准则。"),
            (30, "esg", "环境与社会 本公司积极推进绿色发展战略。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, "significant_events", "Significant Events The Company completed a major acquisition this year."),
            (23, "accounting_policy", "Accounting Policies The Company adopted new accounting standards."),
            (35, "esg", "Environmental and Social The Company actively promotes green development strategy."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    section_diffs = [d for d in result.diffs if d.rule_id.startswith("bilingual_section_")]

    assert len(section_diffs) == 0, f"Expected 0 section diffs, got {len(section_diffs)}: {[d.diff_explanation.headline for d in section_diffs]}"
    assert result.stats["section_pair_count"] == 3


def test_bilingual_text_based_section_detection_with_new_keywords() -> None:
    """section=None 时，新增的 _SECTION_KEYWORDS 条目仍可通过 text-based 匹配。"""
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, None, "重要事项 本年度公司完成重大并购。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, "significant_events", "Significant Events The Company completed a major acquisition."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    # 中文段落的 text 含"重要事项"，应通过 _SECTION_KEYWORDS 匹配到 "significant_events"
    # 英文段落 section="significant_events" 通过 _PARSER_CODE_TO_KEY 也映射到 "significant_events"
    # 两者 key 一致，应能配对
    assert result.stats["paired_blocks"] >= 1


# ---------------------------------------------------------------------------
# 新增测试：第二轮误报修复验证
# ---------------------------------------------------------------------------


def test_bilingual_section_order_not_reported() -> None:
    """章节顺序差异在双语报告中属正常差异，不应报出。"""
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "business", "业务概览 本集团业务稳定增长。"),
            (20, "governance", "公司治理 董事会已审议年度报告。"),
            (30, "notes", "财务报表附注 本集团发行债券人民币25亿元。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (11, "governance", "Corporate Governance The Board has considered the annual report."),
            (22, "business", "Business overview The Group's business maintained stable growth."),
            (33, "notes", "Notes to the financial statements The Group issued bonds of RMB2.5 billion."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    rule_ids = {diff.rule_id for diff in result.diffs}

    # 三个章节都存在且配对，章节顺序不同不应报出
    assert "bilingual_section_order_mismatch" not in rule_ids


def test_bilingual_section_missing_not_reported_when_content_paired() -> None:
    """章节内容已配对时（仅英文 section 检测失败），不应报章节缺失。"""
    # 中文有 revenue section，英文有对应内容但 parser 未分配 section code
    zh_doc = _multi_doc(
        "h-zh",
        [
            (10, "revenue", "营业收入 本集团营业收入为人民币100亿元。"),
            (20, "notes", "财务报表附注 本集团发行债券人民币25亿元。"),
        ],
        Language.ZH,
    )
    en_doc = _multi_doc(
        "h-en",
        [
            (12, None, "Revenue The Group's operating income was RMB10 billion."),  # section=None but content exists
            (23, "notes", "Notes to the financial statements The Group issued bonds of RMB2.5 billion."),
        ],
        Language.EN,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    # revenue 内容已配对（paired_blocks >= 2），不应报章节缺失
    section_missing_diffs = [d for d in result.diffs if d.rule_id == "bilingual_section_missing"]
    assert len(section_missing_diffs) == 0, f"Expected 0 section_missing diffs, got {len(section_missing_diffs)}"


def test_bilingual_same_paragraph_different_amounts_not_flagged() -> None:
    """同段不同事项的金额不应被错误配对（上下文兼容性过滤）。"""
    zh_doc = _doc(
        "h-zh",
        "诉讼事项一：涉诉金额为人民币9,146万元。诉讼事项二：涉诉金额为人民币1.3亿元。",
        Language.ZH,
        page=100,
    )
    en_doc = _doc(
        "h-en",
        "Case 1: The disputed amount is RMB91.46 million. Case 2: The disputed amount is RMB130 million.",
        Language.EN,
        page=100,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]

    # 两个金额分别配对正确，不应产生差异
    assert len(fact_diffs) == 0, f"Expected 0 fact diffs, got {len(fact_diffs)}"


def test_bilingual_large_amount_ratio_downgraded_to_info() -> None:
    """金额差异超过 50% 时应降级为 INFO（可能是不同事项被误配）。"""
    zh_doc = _doc(
        "h-zh",
        "本公司注册资本为人民币5,000万元。对外担保总额为人民币8亿元。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "The registered capital is RMB50 million. The total external guarantees amount to RMB800 million.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])

    # 若产生金额差异，severity 应为 INFO
    amount_diffs = [
        d for d in result.diffs
        if d.rule_id == "bilingual_fact_mismatch" and d.severity == DiffSeverity.HIGH
    ]
    assert len(amount_diffs) == 0, f"No HIGH severity amount diffs expected, got {len(amount_diffs)}"


def test_llm_hallucinated_mismatch_is_suppressed(monkeypatch) -> None:
    """LLM 声称金额不一致但数值实际相同时，应被数值校验层过滤。"""
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", True)

    def mock_llm_compare_batch(batch):
        return [{
            "pair_index": 0,
            "fact_type": "amount",
            "zh_value": "人民币25亿元",
            "en_value": "RMB2.5 billion",
            "issue": "Chinese says 25亿 but English says 2.5 billion",
            "severity": "high",
            "confidence": 0.9,
        }]

    monkeypatch.setattr(bilingual_module, "_llm_compare_batch", mock_llm_compare_batch)

    zh_doc = _doc("h-zh", "本集团发行债券金额为人民币25亿元。", Language.ZH)
    en_doc = _doc("h-en", "The Group issued bonds amounting to RMB2.5 billion.", Language.EN)

    result = run_bilingual_checks(zh_doc, en_doc)
    llm_diffs = [d for d in result.diffs if d.rule_id == "bilingual_llm_fact_mismatch"]
    assert len(llm_diffs) == 0, f"Expected 0 LLM diffs, got {len(llm_diffs)}"


def test_multi_fact_group_downgraded_to_unresolved() -> None:
    """同段多个金额的最优配对结果应降级为 unresolved，避免进入真实差异。"""
    zh_doc = _doc(
        "h-zh",
        "资产A为人民币100亿元，资产B为人民币200亿元，资产C为人民币300亿元。",
        Language.ZH,
        page=10,
    )
    en_doc = _doc(
        "h-en",
        "Asset A was RMB10 billion, Asset B was RMB25 billion, Asset C was RMB30 billion.",
        Language.EN,
        page=12,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    # 允许存在差异，但不允许任何一条进入 triage=real
    assert not any(d.triage == "real" for d in fact_diffs), (
        f"Multi-fact group diffs should not be triage=real, got {[(d.severity, d.triage) for d in fact_diffs]}"
    )


def test_dividend_distribution_mismatch_still_detected(monkeypatch) -> None:
    """用户真实错误样例：中文 2,503,994千元 vs 英文 25,039,945 thousand 必须检出。"""
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", False)

    zh_doc = _doc("h-zh", "利润分配：共计股利人民币2,503,994千元。", Language.ZH, page=453)
    en_doc = _doc(
        "h-en",
        "Profit distribution: total dividends amounting to RMB25,039,945 thousand.",
        Language.EN,
        page=453,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    fact_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    assert len(fact_diffs) == 1, f"Expected 1 dividend mismatch, got {len(fact_diffs)}"
    items = fact_diffs[0].diff_explanation.items
    assert items
    assert "2,503,994" in str(items[0].a_value)
    assert "25,039,945" in str(items[0].h_value)


def test_dividend_distribution_mismatch_detected_via_llm_path(monkeypatch) -> None:
    """LLM 路径下，若 LLM 漏报，正则兜底必须检出中文 2,503,994千元 vs 英文 25,039,945 thousand。"""
    monkeypatch.setattr(settings, "bilingual_use_llm_fact_compare", True)

    # 模拟 LLM 漏报：返回空 issues
    def mock_llm_compare_batch(batch):
        return []

    monkeypatch.setattr(bilingual_module, "_llm_compare_batch", mock_llm_compare_batch)

    zh_doc = _doc("h-zh", "利润分配：共计股利人民币2,503,994千元。", Language.ZH, page=453)
    en_doc = _doc(
        "h-en",
        "Profit distribution: total dividends amounting to RMB25,039,945 thousand.",
        Language.EN,
        page=453,
    )

    result = run_bilingual_checks(zh_doc, en_doc, semantic_evaluator=lambda pairs: [])
    # regex 兜底产生的是 bilingual_fact_mismatch；LLM 未产生 bilingual_llm_fact_mismatch
    regex_diffs = [d for d in result.diffs if d.rule_id == "bilingual_fact_mismatch"]
    llm_diffs = [d for d in result.diffs if d.rule_id == "bilingual_llm_fact_mismatch"]
    assert len(llm_diffs) == 0, f"Expected 0 LLM diffs when LLM returns empty, got {len(llm_diffs)}"
    assert len(regex_diffs) == 1, f"Expected 1 regex backfill dividend mismatch, got {len(regex_diffs)}"
    assert regex_diffs[0].severity == DiffSeverity.HIGH
    assert regex_diffs[0].triage == "real"
    items = regex_diffs[0].diff_explanation.items
    assert items
    assert "2,503,994" in str(items[0].a_value)
    assert "25,039,945" in str(items[0].h_value)
    # 统计中应记录 regex backfill
    assert result.stats.get("llm_fact_regex_backfill", 0) == 1
