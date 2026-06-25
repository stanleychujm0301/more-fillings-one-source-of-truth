"""Test rowspan fix for Everbright Bank H-share extraction."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts
from ahcc.schemas import ReportSide

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

print("=== Parsing H-share ===")
h_doc = parse_h_pdf(h_path)
print(f"H: {h_doc.total_pages} pages, {len(h_doc.tables)} tables, {len(h_doc.texts)} text segments")

print("\n=== Testing _extract_from_tables (no OCR/LLM) ===")
table_points = _extract_from_tables(h_doc)
print(f"Extracted {len(table_points)} points from tables")

# Check for share_capital
sc_points = [p for p in table_points if p.canonical_key == "share_capital"]
print(f"\nshare_capital points from tables:")
for p in sc_points:
    ev = p.evidence
    print(f"  value={p.value}, page={ev.page if ev else 'N/A'}, snippet={ev.snippet if ev else 'N/A'}")

print("\n=== Testing _extract_from_texts (no OCR/LLM) ===")
text_points = _extract_from_texts(h_doc)
print(f"Extracted {len(text_points)} points from texts")

sc_text = [p for p in text_points if p.canonical_key == "share_capital"]
print(f"\nshare_capital points from texts:")
for p in sc_text:
    ev = p.evidence
    print(f"  value={p.value}, page={ev.page if ev else 'N/A'}, snippet={ev.snippet if ev else 'N/A'}")

print("\n=== Testing aggregate for key metrics ===")
all_points = table_points + text_points
for key in ["total_assets", "total_liabilities", "revenue", "net_profit", "share_capital", "fixed_assets"]:
    candidates = [p for p in all_points if p.canonical_key == key]
    if candidates:
        best = max(candidates, key=lambda p: (p.confidence or 0, p.value or 0))
        ev = best.evidence
        print(f"  {key:20s} = {best.value:>15.1f}  (conf={best.confidence}, page={ev.page if ev else 'N/A'})")
    else:
        print(f"  {key:20s} = {'N/A':>15s}")
