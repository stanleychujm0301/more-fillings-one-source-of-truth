"""Strict 模式语义审查降级测试。

semantic_evaluator 已废弃（LLM 事实对比合并了数字+语义审查），传入不再执行。
这些测试验证：传入各种 evaluator 配置不会导致崩溃，也不产生 bilingual_semantic_mismatch 差异。
"""

from __future__ import annotations

from ahcc.check.bilingual import run_bilingual_checks
from ahcc.schemas import Language, ReportDocument, ReportSide


def _empty_doc(side: ReportSide) -> ReportDocument:
    return ReportDocument(
        doc_id="x",
        side=side,
        file_path="x",
        total_pages=1,
        primary_language=Language.ZH,
    )


def test_strict_evaluator_exception_does_not_crash():
    """semantic_evaluator 抛异常时，run_bilingual_checks 不崩溃（evaluator 不再执行）。"""

    def boom(pairs):
        raise RuntimeError("LLM down")

    result = run_bilingual_checks(
        _empty_doc(ReportSide.H_SHARE),
        _empty_doc(ReportSide.H_SHARE),
        semantic_evaluator=boom,
        enable_semantic=True,
    )
    # semantic_evaluator 不再执行，不应产生 semantic_mismatch 差异
    assert not any(d.rule_id == "bilingual_semantic_mismatch" for d in result.diffs)


def test_strict_evaluator_returns_none_does_not_crash():
    """semantic_evaluator 返回 None（LLM 不可用）时不崩溃（evaluator 不再执行）。"""

    def none_eval(pairs):
        return None

    result = run_bilingual_checks(
        _empty_doc(ReportSide.H_SHARE),
        _empty_doc(ReportSide.H_SHARE),
        semantic_evaluator=none_eval,
        enable_semantic=True,
    )
    # semantic_evaluator 不再执行，不应产生 semantic_mismatch 差异
    assert not any(d.rule_id == "bilingual_semantic_mismatch" for d in result.diffs)
