"""List all H-share table points (before dedup)."""
import sys, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

# Monkey-patch OCR fallback to avoid EasyOCR model download
_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

h_doc = parse_h_pdf(h_path)
table_points = _extract_from_tables(h_doc)

print(f"=== H-share extracted {len(table_points)} table points ===")
for p in sorted(table_points, key=lambda x: (x.canonical_key, x.value or 0)):
    ev = p.evidence
    print(f"  {p.canonical_key:25s} = {p.value:>12.1f}  (page={ev.page if ev else 'N/A'}, snippet={ev.snippet if ev else 'N/A'})")
