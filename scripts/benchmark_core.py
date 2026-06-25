"""Benchmark core pipeline without chart detection/checking."""
import sys
import asyncio
import time
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import align_documents
from ahcc.check.numeric import run_numeric_checks
from ahcc.check.standard import run_standard_checks
from ahcc.check.disclosure import run_disclosure_checks

async def main():
    a_path = "f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf"
    h_path = "f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf"

    t0 = time.time()

    t1 = time.time()
    a_doc = parse_a_pdf(a_path)
    t2 = time.time()
    print(f"A parse: {t2-t1:.1f}s")

    t1 = time.time()
    h_doc = parse_h_pdf(h_path)
    t2 = time.time()
    print(f"H parse: {t2-t1:.1f}s")

    t1 = time.time()
    pairs = await align_documents(a_doc, h_doc)
    t2 = time.time()
    print(f"Align: {t2-t1:.1f}s")
    print(f"  Total pairs: {len(pairs)}")
    matched = sum(1 for p in pairs if p.a_point and p.h_point)
    print(f"  Bilateral: {matched}")

    t1 = time.time()
    numeric_diffs = await asyncio.to_thread(run_numeric_checks, pairs)
    t2 = time.time()
    print(f"Numeric check: {t2-t1:.1f}s ({len(numeric_diffs)} diffs)")

    t1 = time.time()
    standard_diffs = await run_standard_checks(pairs)
    t2 = time.time()
    print(f"Standard check: {t2-t1:.1f}s ({len(standard_diffs)} diffs)")

    t1 = time.time()
    disclosure_diffs = await run_disclosure_checks(a_doc, h_doc)
    t2 = time.time()
    print(f"Disclosure check: {t2-t1:.1f}s ({len(disclosure_diffs)} diffs)")

    total = time.time() - t0
    print(f"\nTotal core pipeline: {total:.1f}s")

asyncio.run(main())
