"""Test EasyOCR ch_sim on H-share page."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "f:/毕马威黑客松/ah-consistency-checker")

import fitz
import easyocr

h_path = "f:/毕马威黑客松/99 年报/光大银行/H 中国光大银行股份有限公司2025年年度报告 2.pdf"
reader = easyocr.Reader(["ch_sim", "en"], gpu=False)

doc = fitz.open(h_path)

# Test page 38 (fixed_assets)
page = doc[37]
pix = page.get_pixmap(dpi=200)

with tempfile.TemporaryDirectory() as tmpdir:
    img_path = Path(tmpdir) / "page_38.png"
    pix.save(str(img_path))
    result = reader.readtext(str(img_path))

print("=== Page 38 OCR (ch_sim) ===")
for r in result:
    bbox, text, conf = r
    print(f"  [{conf:.2f}] {text}")

doc.close()
