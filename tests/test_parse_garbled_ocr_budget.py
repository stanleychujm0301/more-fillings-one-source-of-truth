"""H 股解析乱码页 OCR 兜底预算的回归测试。

历史卡死根因：扫描版 PDF 几百页全部乱码时，每个乱码页都会触发一次
`ocr_page`（EasyOCR、CPU-only、单页数十秒），且每页重开一次整个 PDF——
任务被拖到数小时（jobs 表里 33000+ 秒的失败记录）。修复后：
- OCR 兜底受 `parse_garbled_ocr_max_pages` / `parse_garbled_ocr_max_seconds` 双预算限制；
- 超预算时跳过 OCR、记 `garbled_ocr_budget_exhausted` audit flag；
- 解析循环每页触发心跳回调（供 worker 子进程刷新 heartbeat 文件）。
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from ahcc.parser import pdf_h_html
from ahcc.parser.pdf_h_html import _parse_h_pdf, set_parse_heartbeat


_GARBLED_LINE = "0x1F 0x8E 0x99 ####$$$@@@!!! 0000 1111 2222 3333 4444 5555 6666 7777"


def _make_garbled_pdf(path: Path, garbled_pages: int) -> None:
    """构造可读字符占比 <10% 的页面（触发 _is_h_garbled 的低可读比例分支）。

    注意不能用 � 字符——PDF 内置字体无法编码它，页面文本会变成空串而绕过乱码检测。
    pdfplumber 对同一页会提取出同样的低可读文本，因此必然落入 OCR 兜底分支。
    """
    doc = fitz.open()
    for _ in range(garbled_pages):
        page = doc.new_page()
        page.insert_text((72, 100), _GARBLED_LINE, fontsize=10)
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def _no_heartbeat():
    yield
    set_parse_heartbeat(None)


def test_garbled_ocr_respects_page_budget(tmp_path: Path, monkeypatch, _no_heartbeat) -> None:
    pdf = tmp_path / "garbled.pdf"
    _make_garbled_pdf(pdf, garbled_pages=6)

    ocr_calls: list[int] = []

    def fake_ocr_page(file_path: str, page_num: int, dpi: int = 200):
        ocr_calls.append(page_num)
        return [{"text": "recovered text for page %d with enough readable words" % page_num}]

    import ahcc.parser.ocr_fallback as ocr_fallback

    monkeypatch.setattr(ocr_fallback, "ocr_page", fake_ocr_page)
    monkeypatch.setattr(pdf_h_html.settings, "parse_garbled_ocr_max_pages", 2)
    monkeypatch.setattr(pdf_h_html.settings, "parse_garbled_ocr_max_seconds", 999.0)

    doc = _parse_h_pdf(str(pdf))

    # 只有前 2 个乱码页做了 OCR，其余因预算用尽被跳过
    assert len(ocr_calls) == 2
    audit = doc.extraction_audit
    assert audit is not None
    assert "garbled_ocr_budget_exhausted" in (audit.warning_flags or [])
    assert any("budget" in (w or "") for w in audit.warnings or [])


def test_garbled_ocr_within_budget_recovers_pages(tmp_path: Path, monkeypatch, _no_heartbeat) -> None:
    pdf = tmp_path / "garbled_small.pdf"
    _make_garbled_pdf(pdf, garbled_pages=2)

    def fake_ocr_page(file_path: str, page_num: int, dpi: int = 200):
        return [{"text": "recovered readable english text for page %d" % page_num}]

    import ahcc.parser.ocr_fallback as ocr_fallback

    monkeypatch.setattr(ocr_fallback, "ocr_page", fake_ocr_page)
    monkeypatch.setattr(pdf_h_html.settings, "parse_garbled_ocr_max_pages", 10)
    monkeypatch.setattr(pdf_h_html.settings, "parse_garbled_ocr_max_seconds", 999.0)

    doc = _parse_h_pdf(str(pdf))

    audit = doc.extraction_audit
    assert audit is not None
    assert "garbled_ocr_budget_exhausted" not in (audit.warning_flags or [])
    joined = " ".join(seg.text for seg in doc.texts)
    assert "recovered readable english text" in joined


def test_parse_emits_heartbeat_per_page(tmp_path: Path, _no_heartbeat) -> None:
    pdf = tmp_path / "clean.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 100), f"Normal readable page {i} with plenty of english words", fontsize=10)
    doc.save(str(pdf))
    doc.close()

    beats: list[int] = []
    set_parse_heartbeat(lambda: beats.append(1))

    _parse_h_pdf(str(pdf))

    assert len(beats) >= 3  # 每页至少一次
