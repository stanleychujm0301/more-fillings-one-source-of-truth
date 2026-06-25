"""调试 H 股文本编码问题。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # PyMuPDF

doc = fitz.open("f:/毕马威黑客松/99 年报/国泰海通/H 国泰海通证券股份有限公司2025年年度报告.pdf")

# 检查前 5 页的文本
lines = []
for page_idx in range(5):
    page = doc[page_idx]
    text = page.get_text()
    lines.append(f"=== Page {page_idx + 1} ===")
    lines.append(f"Text length: {len(text)}")
    lines.append(f"First 200 chars: {text[:200]!r}")
    lines.append("")

doc.close()

Path("storage/debug_h_encoding.log").write_text("\n".join(lines), encoding="utf-8")
print("已输出到 storage/debug_h_encoding.log")
