"""Show all aligned pairs with percentage differences."""
import sys, asyncio, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import align_documents

a_path = "f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf"
h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

async def main():
    a_doc = parse_a_pdf(a_path)
    h_doc = parse_h_pdf(h_path)
    pairs = await align_documents(a_doc, h_doc)

    print("=== All aligned pairs ===")
    for p in sorted(pairs, key=lambda x: x.canonical_key):
        a_val = p.a_point.value if p.a_point else None
        h_val = p.h_point.value if p.h_point else None
        a_str = f"{a_val:>12.1f}" if a_val is not None else "           -"
        h_str = f"{h_val:>12.1f}" if h_val is not None else "           -"

        if a_val is not None and h_val is not None and a_val != 0:
            pct = (h_val - a_val) / abs(a_val) * 100
            delta_str = f"{pct:+.2f}%"
        else:
            delta_str = "N/A"

        if a_val == h_val:
            status = "OK"
        elif a_val is not None and h_val is not None:
            status = "DIFF"
        else:
            status = "MISS"
        print(f"  [{status}] {p.canonical_key:25s}  A={a_str}  H={h_str}  delta={delta_str}")

asyncio.run(main())
