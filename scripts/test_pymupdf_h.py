"""Test PyMuPDF on H-share financial pages."""
import sys
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

import fitz  # PyMuPDF

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"

doc = fitz.open(h_path)

# Try page 38 (fixed_assets page) - 0-indexed = 37
page = doc[37]
print("=== Page 38 (PyMuPDF) ===")
text = page.get_text()
for line in text.split("\n")[:40]:
    print(line)

print("\n=== Page 19 (PyMuPDF) ===")
page = doc[18]
text = page.get_text()
for line in text.split("\n")[:30]:
    print(line)

doc.close()
