"""Inspect H-share text segment sections around BS pages."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf

PDF_PATH = "f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf"
doc = parse_h_pdf(PDF_PATH)

with open("storage/debug_h_sections.log", "w", encoding="utf-8") as f:
    for seg in doc.texts:
        if 348 <= seg.page <= 362:
            f.write(f"\n{'='*70}\n")
            f.write(f"Page {seg.page} | Section: {seg.section} | Lang: {seg.language.value}\n")
            f.write(f"Text ({len(seg.text)} chars):\n{seg.text[:600]}\n")

print("Dumped to storage/debug_h_sections.log")
