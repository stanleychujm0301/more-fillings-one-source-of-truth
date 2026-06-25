"""Benchmark Shenwan 2020 with LLM disabled to test regex path."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahcc.config import settings
from ahcc.orchestrator import Orchestrator


async def main() -> None:
    settings.bilingual_use_llm_fact_compare = False

    zh_path = "f:/毕马威黑客松/ah-consistency-checker/storage/uploads/H_ZH_申万宏源：2020年度报告.pdf"
    en_path = "f:/毕马威黑客松/ah-consistency-checker/storage/uploads/H_EN_申万宏源：2020年度报告 英文 2.pdf"

    job = await Orchestrator().run(
        zh_path,
        en_path,
        company_name="申万宏源2020年中英文测试-正则路径",
        check_mode="h_bilingual",
        bilingual_level="fast",
    )

    by_rule = {}
    for d in job.diffs:
        by_rule.setdefault(d.rule_id, []).append(d)

    print(f"Status: {job.status}")
    print(f"Duration: {job.duration_seconds:.1f}s")
    print(f"Total diffs: {len(job.diffs)}")
    print(f"Real diffs: {sum(1 for d in job.diffs if d.triage == 'real')}")
    print(f"Unresolved diffs: {sum(1 for d in job.diffs if d.triage == 'unresolved')}")

    for rule, diffs in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        print(f"\n=== {rule} ({len(diffs)}) ===")
        for i, d in enumerate(diffs[:10], 1):
            headline = d.diff_explanation.headline if d.diff_explanation else ""
            issue = d.diff_explanation.issue if d.diff_explanation else ""
            print(f"{i}. [{d.triage}] {d.severity} {headline}")
            print(f"   {issue[:160]}")
            if d.diff_explanation and d.diff_explanation.items:
                item = d.diff_explanation.items[0]
                print(f"   A={item.a_value} H={item.h_value} pages={item.a_page}/{item.h_page}")

    out_path = Path("f:/毕马威黑客松/ah-consistency-checker/storage/shenwan2020_regex_diffs.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump([d.model_dump(mode="json") for d in job.diffs], f, ensure_ascii=False, indent=2)
    print(f"\nSaved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
