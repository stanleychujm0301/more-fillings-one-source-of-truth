"""Quick test aligning A+H operating_profit."""
import sys
import asyncio
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import align_documents

async def main():
    a_doc = parse_a_pdf("f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf")
    h_doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")
    pairs = await align_documents(a_doc, h_doc)

    for p in pairs:
        if p.canonical_key == "operating_profit":
            a_val = p.a_point.value if p.a_point else None
            h_val = p.h_point.value if p.h_point else None
            print(f"operating_profit: A={a_val} H={h_val} confidence={p.alignment_confidence}")
            if a_val and h_val:
                print(f"  delta={abs(a_val - h_val)}")
                print("PASS" if a_val > 1e10 and h_val > 1e10 else "FAIL")
            break
    else:
        print("No operating_profit pair found")

    # Summary
    print(f"\nTotal aligned pairs: {len(pairs)}")
    matched = sum(1 for p in pairs if p.a_point and p.h_point)
    print(f"Bilateral matches: {matched}")

asyncio.run(main())
