# -*- coding: utf-8 -*-
"""门槛 d 准备：跑 3 个代表性任务并持久化（供 evidence_spotcheck 抽验）。

选取：主办方篡改样本对（overlay 证据链）、注入对（位置孪生证据链）、
干净真实对（数值/披露证据链）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ahcc.orchestrator import Orchestrator
from ahcc.storage.repository import save_job

PAIRS = [
    (
        "F:/毕马威黑客松/样本测试/sample/青岛啤酒A股年报_含错误_测试版.pdf",
        "F:/毕马威黑客松/样本测试/sample/青岛啤酒_2024年H股年报.pdf",
    ),
    (
        "storage/eval/final3/injected_光大银行_2025年H股年报.pdf",
        "F:/毕马威黑客松/样本测试/sample/光大银行_2025年H股年报.pdf",
    ),
    (
        "F:/毕马威黑客松/样本测试/光大证券/A 光大证券.pdf",
        "F:/毕马威黑客松/样本测试/光大证券/H 光大证券.pdf",
    ),
]


async def main():
    for a, h in PAIRS:
        if not Path(a).exists() or not Path(h).exists():
            print(f"SKIP missing: {a} / {h}")
            continue
        job = await Orchestrator().run(a, h)
        save_job(job)
        print(f"DONE {job.job_id} diffs={len(job.diffs)}")


if __name__ == "__main__":
    asyncio.run(main())
