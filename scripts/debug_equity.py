"""Debug why total_equity is not found."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.glossary import glossary, to_simplified
from ahcc.align.matcher import _extract_number_near_label, _extract_all_numbers

doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

# Find page 357 text
for seg in doc.texts:
    if seg.page == 357:
        text_simplified = to_simplified(seg.text)
        print(f"Page 357 section={seg.section}")
        print(f"Text length: {len(text_simplified)}")
        print(f"Contains '权益总额': {'权益总额' in text_simplified}")
        print(f"Contains '權益總額': {'權益總額' in text_simplified}")

        entry = glossary.get_entry("equity")
        print(f"\nGlossary equity forms: {[entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]}")

        for form in [entry.zh_cn, entry.zh_hk, entry.en, *entry.aliases]:
            if not form:
                continue
            if form in text_simplified:
                print(f"  MATCHED: '{form}' in text")
                val, val_text = _extract_number_near_label(text_simplified, form)
                print(f"  Extracted: {val_text} -> {val}")
            else:
                form_s = to_simplified(form)
                if form_s in text_simplified:
                    print(f"  MATCHED (simplified): '{form_s}' (from '{form}') in text")
                    val, val_text = _extract_number_near_label(text_simplified, form_s)
                    print(f"  Extracted: {val_text} -> {val}")

        # Also search for the exact position
        idx = text_simplified.find("权益总额")
        print(f"\n'权益总额' found at index: {idx}")
        if idx >= 0:
            print(f"Context: ...{text_simplified[max(0,idx-20):idx+60]}...")
        break
