"""Debug H-share cash_equivalents extraction."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_texts, _extract_from_tables

doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

output_lines = []
output_lines.append(f"H-share unit: {doc.metadata.get('unit')}")
output_lines.append(f"H-share currency: {doc.metadata.get('currency')}")
output_lines.append("")

# Search texts for cash-related terms
for seg in doc.texts:
    if seg.section not in ("bs", "pl", "cf", "equity"):
        continue
    text = seg.text
    if "现金" in text or "貨幣" in text or "Cash" in text or "cash" in text:
        for line in text.split('\n'):
            if "现金" in line or "貨幣" in line or "Cash" in line:
                output_lines.append(f"Page {seg.page} section={seg.section}: {line.strip()}")

# Show what matcher extracts
table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)

output_lines.append("\n=== Table extraction ===")
for p in table_points:
    if "cash" in p.canonical_key or "receivable" in p.canonical_key:
        output_lines.append(f"  {p.canonical_key}: {p.value} (text='{p.value_text}') page={p.evidence.page}")

output_lines.append("\n=== Text extraction ===")
for p in text_points:
    if "cash" in p.canonical_key or "receivable" in p.canonical_key:
        output_lines.append(f"  {p.canonical_key}: {p.value} (text='{p.value_text}') page={p.evidence.page}")

output = "\n".join(output_lines)
with open("storage/debug_h_cash.log", "w", encoding="utf-8") as f:
    f.write(output)
print("Saved to storage/debug_h_cash.log")
