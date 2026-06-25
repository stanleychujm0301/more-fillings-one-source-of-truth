"""Debug BS anchor heuristic for long_term_investments."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_a import parse_a_pdf
from ahcc.align.matcher import _extract_from_tables, _extract_from_texts

doc = parse_a_pdf("f:/毕马威黑客松/99 年报/国泰海通/A 国泰海通证券股份有限公司2025年年度报告.pdf")

table_points = _extract_from_tables(doc)
text_points = _extract_from_texts(doc)

print("=== Table points for long_term_investments ===")
for p in table_points:
    if p.canonical_key == "long_term_investments":
        print(f"  page={p.evidence.page} value={p.value} text='{p.value_text}'")

print("\n=== Text points for long_term_investments ===")
for p in text_points:
    if p.canonical_key == "long_term_investments":
        print(f"  page={p.evidence.page} value={p.value} text='{p.value_text}' snippet={p.evidence.snippet[:80]}")

print("\n=== Text points for total_assets ===")
for p in text_points:
    if p.canonical_key == "total_assets":
        print(f"  page={p.evidence.page} value={p.value}")

# Check anchor logic
bs_anchor_pages = set()
for p in text_points:
    if p.canonical_key == "total_assets" and p.evidence.page:
        bs_anchor_pages.add(p.evidence.page)
        bs_anchor_pages.add(p.evidence.page - 1)
        bs_anchor_pages.add(p.evidence.page + 1)

print(f"\nAnchor pages: {bs_anchor_pages}")

all_candidates = [p for p in table_points + text_points if p.canonical_key == "long_term_investments"]
print(f"All candidates: {len(all_candidates)}")
for p in all_candidates:
    print(f"  page={p.evidence.page} value={p.value}")

anchored = [p for p in all_candidates if p.evidence.page in bs_anchor_pages]
print(f"Anchored candidates: {len(anchored)}")
for p in anchored:
    print(f"  page={p.evidence.page} value={p.value}")
