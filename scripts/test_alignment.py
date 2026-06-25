"""Test full alignment for Everbright Bank."""
import sys, asyncio, types
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

# Monkey-patch OCR fallback to avoid EasyOCR model download
_ocr_mod = types.ModuleType("ahcc.parser.ocr_fallback")
_ocr_mod.extract_keypoints_from_page_images = lambda *a, **k: []
sys.modules["ahcc.parser.ocr_fallback"] = _ocr_mod

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import align_documents
from ahcc.check.numeric import run_numeric_checks

a_path = "f:/毕马威黑客松/99 年报/光大银行/A 中国光大银行股份有限公司2025年年度报告.pdf"
h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

async def main():
    print("=== Parsing A-share ===")
    a_doc = parse_a_pdf(a_path)
    print(f"A: {a_doc.total_pages} pages, {len(a_doc.tables)} tables, {len(a_doc.texts)} text segments")

    print("\n=== Parsing H-share ===")
    h_doc = parse_h_pdf(h_path)
    print(f"H: {h_doc.total_pages} pages, {len(h_doc.tables)} tables, {len(h_doc.texts)} text segments")

    print("\n=== Aligning ===")
    pairs = await align_documents(a_doc, h_doc)
    print(f"Aligned: {len(pairs)} pairs")
    matched = sum(1 for p in pairs if p.a_point and p.h_point)
    print(f"  Bilateral: {matched}")

    print("\n=== Key metrics ===")
    for p in pairs:
        if p.canonical_key in ["total_assets", "total_liabilities", "revenue", "net_profit", "share_capital", "fixed_assets", "operating_profit"]:
            a_val = p.a_point.value if p.a_point else None
            h_val = p.h_point.value if p.h_point else None
            a_str = f"{a_val:>12.1f}" if a_val is not None else "           —"
            h_str = f"{h_val:>12.1f}" if h_val is not None else "           —"
            print(f"  {p.canonical_key:20s}  A={a_str}  H={h_str}")

    print("\n=== Numeric checks ===")
    diffs = run_numeric_checks(pairs)
    print(f"Found {len(diffs)} numeric diffs")
    for d in diffs:
        topic = d.topic.zh if d.topic and d.topic.zh else d.canonical_key
        a_str = f"{d.a_value:>12.1f}" if d.a_value is not None else "           —"
        h_str = f"{d.h_value:>12.1f}" if d.h_value is not None else "           —"
        print(f"  [{d.severity}] {topic:20s}  A={a_str}  H={h_str}")

asyncio.run(main())
