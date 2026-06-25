"""PDF 内嵌预览 + 证据高亮（P5 实现）。

实现策略：
- PyMuPDF (fitz) 把指定页渲染为 PNG，bbox 处画红框
- 用 st.image 显示
- 上下页按钮 + 跳转输入框
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st


def render_pdf_page(pdf_path: str | Path, page: int, highlight_bbox=None) -> bytes:
    """渲染指定 PDF 页为 PNG bytes，可选高亮 bbox。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        st.error("缺少 PyMuPDF，无法预览 PDF。pip install pymupdf")
        return b""

    doc = fitz.open(str(pdf_path))
    if page < 1 or page > doc.page_count:
        return b""
    pdf_page = doc.load_page(page - 1)

    if highlight_bbox:
        x0, y0, x1, y1 = highlight_bbox
        rect = fitz.Rect(x0, y0, x1, y1)
        annot = pdf_page.add_rect_annot(rect)
        annot.set_colors(stroke=(0.85, 0.12, 0.12))
        annot.set_border(width=2)
        annot.update()

    pix = pdf_page.get_pixmap(dpi=120)
    return pix.tobytes("png")


def show_evidence(pdf_path: str | Path, page: int, bbox=None, caption: str = "") -> None:
    """便捷封装：显示一条证据图。"""
    img = render_pdf_page(pdf_path, page, bbox)
    if img:
        st.image(img, caption=caption or f"P.{page}")
