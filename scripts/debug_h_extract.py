"""调试 H 股文本提取。"""

import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahcc.parser.pdf_h_html import parse_h_pdf
from ahcc.align.matcher import _extract_from_texts
from ahcc.align.glossary import glossary, to_simplified

doc = parse_h_pdf("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

print(f"H 股: {doc.total_pages} 页, {len(doc.tables)} 表, {len(doc.texts)} 文本段")
print(f"语言: {doc.primary_language}")

# 统计各 section 的文本段数
from collections import Counter
sec_counts = Counter(t.section for t in doc.texts)
print(f"\nSection 分布: {dict(sec_counts)}")

# 检查文本段样本
print("\n前 10 个文本段样本:")
for seg in doc.texts[:10]:
    text = seg.text[:120].replace("\n", " ")
    print(f"  Page {seg.page} [{seg.section}]: {text}")

# 尝试匹配 glossary 术语
print("\n尝试匹配 glossary 术语:")
targets = ["total_assets", "revenue", "net_profit", "equity", "share_capital"]
for key in targets:
    entry = glossary.get_entry(key)
    if not entry:
        continue
    forms = [entry.en, *entry.aliases]
    found = False
    for seg in doc.texts:
        text_lower = seg.text.lower()
        for form in forms:
            if form and form.lower() in text_lower:
                idx = text_lower.find(form.lower())
                window = seg.text[max(0, idx-20):idx+len(form)+30]
                print(f"  {key} ({form}): Page {seg.page} -> '{window}'")
                found = True
                break
        if found:
            break
    if not found:
        print(f"  {key}: 未找到")
