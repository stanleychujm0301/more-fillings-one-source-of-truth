"""Debug script for Everbright Bank A+H reports."""
import sys, asyncio, time
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import align_documents
from ahcc.check.numeric import run_numeric_checks
from ahcc.check.disclosure import run_disclosure_checks

a_path = "f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf"
h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

async def main():
    t1 = time.time()
    print("=== Parsing A-share ===")
    a_doc = parse_a_pdf(a_path)
    print(f"A: {a_doc.total_pages} pages, {len(a_doc.tables)} tables, {len(a_doc.texts)} text segments")
    t2 = time.time()
    print(f"A parse: {t2-t1:.1f}s")

    t1 = time.time()
    print("\n=== Parsing H-share ===")
    h_doc = parse_h_pdf(h_path)
    print(f"H: {h_doc.total_pages} pages, {len(h_doc.tables)} tables, {len(h_doc.texts)} text segments")
    t2 = time.time()
    print(f"H parse: {t2-t1:.1f}s")

    print("\n=== Aligning ===")
    t1 = time.time()
    pairs = await align_documents(a_doc, h_doc)
    t2 = time.time()
    print(f"Align: {t2-t1:.1f}s, {len(pairs)} pairs")
    matched = sum(1 for p in pairs if p.a_point and p.h_point)
    print(f"  Bilateral: {matched}")

    print("\n=== All aligned pairs ===")
    for i, p in enumerate(pairs):
        a = p.a_point
        h = p.h_point
        a_str = f"{a.canonical_key}={a.value}" if a else "None"
        h_str = f"{h.canonical_key}={h.value}" if h else "None"
        print(f"  {i+1}. A:{a_str} | H:{h_str}")

    print("\n=== Numeric checks ===")
    t1 = time.time()
    numeric_diffs = await asyncio.to_thread(run_numeric_checks, pairs)
    t2 = time.time()
    print(f"Numeric: {t2-t1:.1f}s, {len(numeric_diffs)} diffs")
    for d in numeric_diffs:
        topic = d.topic
        topic_str = topic.zh if topic and topic.zh else (topic.en if topic and topic.en else d.canonical_key)
        print(f"  [{d.severity}] {d.diff_type} | {topic_str} | A={d.a_value} H={d.h_value}")

    print("\n=== Disclosure checks ===")
    t1 = time.time()
    disc_diffs = await run_disclosure_checks(a_doc, h_doc)
    t2 = time.time()
    print(f"Disclosure: {t2-t1:.1f}s, {len(disc_diffs)} diffs")
    for d in disc_diffs:
        topic = d.topic
        topic_str = topic.zh if topic and topic.zh else (topic.en if topic and topic.en else d.canonical_key)
        summary = d.summary
        summary_str = summary.zh if summary and summary.zh else (summary.en if summary and summary.en else '—')
        print(f"  [{d.severity}] {topic_str}: {summary_str}")

asyncio.run(main())
