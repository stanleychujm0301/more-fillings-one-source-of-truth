# -*- coding: utf-8 -*-
"""Render annual report PDF pages to PNG assets for the finals deck."""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import fitz  # PyMuPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(ROOT, "demo", "stage")
OUT = os.path.join(ROOT, "demo", "assets")

A_PDF = os.path.join(STAGE, "光大银行2025_A股年报.pdf")
H_PDF = os.path.join(STAGE, "光大银行2025_H股年报.pdf")

# (source pdf, page index (0-based), output name)
# Interior pages picked by density scan: A p171 = 合并资产负债表 (59 long numbers),
# H p19 = 綜合資產負債表 (80 long numbers).
TARGETS = [
    (A_PDF, 0, "cover-a.png"),
    (H_PDF, 0, "cover-h.png"),
    (A_PDF, 170, "page-a.png"),
    (H_PDF, 18, "page-h.png"),
]

ZOOM = 2.0


def find_interior_page(doc, keywords):
    """Pick a page containing a dense financial table (balance sheet style)."""
    for i in range(min(len(doc), 400)):
        text = doc[i].get_text()
        hits = sum(1 for k in keywords if k in text)
        if hits >= 2:
            return i
    return min(10, len(doc) - 1)


def render(doc, page_no, out_name):
    page = doc[page_no]
    pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM))
    path = os.path.join(OUT, out_name)
    pix.save(path)
    print("saved", out_name, f"(page {page_no + 1}, {pix.width}x{pix.height})")


def main():
    os.makedirs(OUT, exist_ok=True)
    for src, page_no, name in TARGETS:
        doc = fitz.open(src)
        if page_no is None:
            kws = ["资产总计", "负债合计"] if "_A股" in src else ["資產總額", "負債總額"]
            page_no = find_interior_page(doc, kws)
        render(doc, page_no, name)
        doc.close()


if __name__ == "__main__":
    main()
