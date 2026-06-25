from __future__ import annotations

from pathlib import Path
import shutil
import uuid

import pytest

from ahcc.parser.chart_detect import detect_charts


def test_repeated_chart_detection_does_not_overwrite_existing_images() -> None:
    fitz = pytest.importorskip("fitz")
    work_dir = Path("storage/test-artifacts") / f"chart-detect-{uuid.uuid4().hex[:8]}"
    pdf_path = work_dir / "chart-source.pdf"
    out_dir = work_dir / "charts"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open()
        page = doc.new_page(width=400, height=400)
        page.draw_rect(fitz.Rect(40, 40, 320, 260), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        doc.save(pdf_path)
        doc.close()

        first = detect_charts(str(pdf_path), out_dir)
        second = detect_charts(str(pdf_path), out_dir)

        assert first
        assert second
        assert first[0].image_path != second[0].image_path
        assert Path(first[0].image_path or "").exists()
        assert Path(second[0].image_path or "").exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
