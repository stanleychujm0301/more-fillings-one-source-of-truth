"""Quick benchmark for Shenwan Hongyuan 2020 H-share bilingual check."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahcc.orchestrator import Orchestrator


async def main() -> None:
    zh_path = "f:/毕马威黑客松/ah-consistency-checker/storage/uploads/H_ZH_申万宏源：2020年度报告.pdf"
    en_path = "f:/毕马威黑客松/ah-consistency-checker/storage/uploads/H_EN_申万宏源：2020年度报告 英文 2.pdf"

    job = await Orchestrator().run(
        zh_path,
        en_path,
        company_name="申万宏源2020年中英文测试",
        check_mode="h_bilingual",
        bilingual_level="strict",
    )

    summary = job.comparison_summary or {}
    real = sum(1 for d in job.diffs if d.triage == "real")
    expected = sum(1 for d in job.diffs if d.triage == "expected")
    unresolved = sum(1 for d in job.diffs if d.triage == "unresolved")
    llm = sum(1 for d in job.diffs if d.rule_id == "bilingual_llm_fact_mismatch")
    regex = sum(1 for d in job.diffs if d.rule_id == "bilingual_fact_mismatch")
    layout = sum(1 for d in job.diffs if d.rule_id.startswith(("bilingual_section_", "bilingual_table_", "bilingual_paragraph_unpaired")))

    print(f"Status: {job.status}")
    print(f"Duration: {job.duration_seconds:.1f}s")
    print(f"Total diffs: {len(job.diffs)}")
    print(f"  real: {real}")
    print(f"  expected: {expected}")
    print(f"  unresolved: {unresolved}")
    print(f"By rule:")
    print(f"  bilingual_llm_fact_mismatch: {llm}")
    print(f"  bilingual_fact_mismatch: {regex}")
    print(f"  layout diffs: {layout}")
    print(f"Summary real_diff_count: {summary.get('real_diff_count')}")
    print(f"Summary unresolved_diff_count: {summary.get('unresolved_diff_count')}")

    print("\nTop 20 real diffs:")
    for d in [d for d in job.diffs if d.triage == "real"][:20]:
        headline = d.diff_explanation.headline if d.diff_explanation else ""
        issue = d.diff_explanation.issue if d.diff_explanation else ""
        print(f"  [{d.rule_id}] {d.severity} {headline}")
        print(f"      {issue[:160]}")


if __name__ == "__main__":
    asyncio.run(main())
