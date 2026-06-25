"""List all H-share extracted points."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

# Monkey-patch OCR fallback to avoid EasyOCR model download
_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_keypoints

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

import asyncio

async def main():
    h_doc = parse_h_pdf(h_path)
    points = await _extract_keypoints(h_doc)
    print(f"=== H-share extracted {len(points)} points ===")
    for p in sorted(points, key=lambda x: x.canonical_key):
        ev = p.evidence
        print(f"  {p.canonical_key:25s} = {p.value:>12.1f}  (page={ev.page if ev else 'N/A'}, conf={p.confidence})")

asyncio.run(main())
