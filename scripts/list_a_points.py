"""List all A-share extracted points."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

# Monkey-patch OCR fallback
_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.matcher import _extract_keypoints

a_path = "f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf"

import asyncio

async def main():
    a_doc = parse_a_pdf(a_path)
    points = await _extract_keypoints(a_doc)
    print(f"=== A-share extracted {len(points)} points ===")
    for p in sorted(points, key=lambda x: x.canonical_key):
        ev = p.evidence
        print(f"  {p.canonical_key:25s} = {p.value:>12.1f}  (page={ev.page if ev else 'N/A'}, conf={p.confidence}, snippet={ev.snippet if ev else 'N/A'})")

asyncio.run(main())
