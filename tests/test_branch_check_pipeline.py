"""分支机构核查主 pipeline 路径的回归测试。

覆盖两点：
1. `run_branch_checks` 用轻量 fitz 文本抽取（不依赖 profile.source_doc）即可稳定
   产出分支机构差异 + 诊断字段——这是修复"分支机构核查不稳定"的核心改动。
2. `run_disclosure_checks_on_profiles` 不再重复产出 branch_asset_scale_match 差异
   （该职责已收拢到 orchestrator._check_branch，避免同一处差异被报告两次）。
"""

from __future__ import annotations

from pathlib import Path

import fitz

from ahcc.check.branch_disclosure import load_branch_lightweight_doc, run_branch_checks
from ahcc.schemas import ReportSide


def _make_branch_pdf(path: Path, rows: list[str]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    for i, row in enumerate(rows):
        page.insert_text((72, 100 + i * 20), row, fontsize=10, fontname="china-s")
    doc.save(str(path))
    doc.close()


def test_run_branch_checks_produces_diffs_and_diagnostics_from_lightweight_docs(tmp_path: Path) -> None:
    a_pdf = tmp_path / "a.pdf"
    h_pdf = tmp_path / "h.pdf"
    _make_branch_pdf(
        a_pdf,
        [
            "北京分行 75 810,136",
            "上海分行 60 620,000",
            "广州分行 40 410,000",
        ],
    )
    _make_branch_pdf(
        h_pdf,
        [
            "北京分行 75 900,000",   # 资产规模不一致 -> 应产生差异
            "上海分行 60 620,000",   # 完全一致 -> 不产生差异
            "广州分行 40 500,000",   # 资产规模不一致 -> 应产生差异
        ],
    )

    diffs, diagnostics = run_branch_checks(str(a_pdf), str(h_pdf))

    assert {d.diff_id for d in diffs} == {"BRANCH_北京分行", "BRANCH_广州分行"}
    assert all(d.rule_id == "branch_asset_scale_match" and d.triage == "real" for d in diffs)
    assert diagnostics["a_branch_count"] == 3
    assert diagnostics["h_branch_count"] == 3
    assert diagnostics["matched_branch_count"] == 3
    assert diagnostics["branch_diff_count"] == 2


def test_load_branch_lightweight_doc_does_not_require_full_parser(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_branch_pdf(pdf, ["北京分行 75 810,136"])

    doc = load_branch_lightweight_doc(str(pdf), ReportSide.A_SHARE)

    assert doc.side == ReportSide.A_SHARE
    assert doc.total_pages == 1
    assert doc.tables == []  # 轻量路径不解析表格/图表，只取纯文本
    assert any("北京分行" in seg.text for seg in doc.texts)


def test_disclosure_profile_check_no_longer_duplicates_branch_diffs() -> None:
    """profile 披露检查路径不应再产出 branch_asset_scale_match —— 该职责已迁移到
    orchestrator._check_branch，避免主 pipeline 里同一处分支差异被报告两次。"""
    import asyncio

    from ahcc.check.disclosure import run_disclosure_checks_on_profiles
    from ahcc.profile.models import ReportProfile
    from ahcc.schemas import Language, ReportDocument, TextSegment

    a_doc = ReportDocument(
        doc_id="A",
        side=ReportSide.A_SHARE,
        file_path="a.pdf",
        total_pages=1,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="a-1",
                page=1,
                bbox=(0, 0, 1, 1),
                text="北京分行 75 810,136",
                language=Language.ZH,
            )
        ],
    )
    h_doc = ReportDocument(
        doc_id="H",
        side=ReportSide.H_SHARE,
        file_path="h.pdf",
        total_pages=1,
        primary_language=Language.ZH,
        texts=[
            TextSegment(
                segment_id="h-1",
                page=1,
                bbox=(0, 0, 1, 1),
                text="北京分行 75 900,000",
                language=Language.ZH,
            )
        ],
    )
    profile_a = ReportProfile(
        doc_id="A-doc", side=ReportSide.A_SHARE, total_pages=1, metrics=[], narratives=[], structure=[]
    )
    profile_h = ReportProfile(
        doc_id="H-doc", side=ReportSide.H_SHARE, total_pages=1, metrics=[], narratives=[], structure=[]
    )
    profile_a.source_doc = a_doc
    profile_h.source_doc = h_doc

    diffs = asyncio.run(run_disclosure_checks_on_profiles(profile_a, profile_h))

    assert not any(d.rule_id == "branch_asset_scale_match" for d in diffs)
