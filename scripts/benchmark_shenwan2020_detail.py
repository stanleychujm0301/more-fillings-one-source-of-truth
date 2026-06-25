"""Quick benchmark for Shenwan Hongyuan 2020 H-share bilingual check (detailed output)."""

from __future__ import annotations

import asyncio
import json
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
    by_triage = {}
    for d in job.diffs:
        by_triage.setdefault(d.triage, []).append(d)

    print(f"Status: {job.status}")
    print(f"Duration: {job.duration_seconds:.1f}s")
    print(f"Total diffs: {len(job.diffs)}")
    for triage, diffs in by_triage.items():
        print(f"\n=== {triage} ({len(diffs)}) ===")
        for i, d in enumerate(diffs, 1):
            headline = d.diff_explanation.headline if d.diff_explanation else ""
            issue = d.diff_explanation.issue if d.diff_explanation else ""
            print(f"{i}. [{d.rule_id}] {d.severity}")
            print(f"   主题: {d.topic.zh if d.topic else ''}")
            print(f"   标题: {headline}")
            print(f"   说明: {issue[:200]}")
            if d.diff_explanation and d.diff_explanation.items:
                item = d.diff_explanation.items[0]
                print(f"   A值: {item.a_value}")
                print(f"   H值: {item.h_value}")
                print(f"   A页: {item.a_page} H页: {item.h_page}")

    # Save full JSON for inspection
    out_path = Path("f:/毕马威黑客松/ah-consistency-checker/storage/shenwan2020_diffs.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump([d.model_dump(mode="json") for d in job.diffs], f, ensure_ascii=False, indent=2)
    print(f"\nFull diffs saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
