"""Chart region detection for embedded images and vector-drawn charts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from loguru import logger

from ahcc.schemas import ChartRegion


def detect_charts(pdf_path: str, out_dir: Path, max_pages: int | None = None) -> list[ChartRegion]:
    """Detect chart-like regions and save screenshots.

    `max_pages=None` scans the full report. Output file names are collision-safe
    so repeated runs do not overwrite a locked PNG from a previous job.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF is unavailable; chart detection skipped.")
        return []

    charts: list[ChartRegion] = []
    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc) if max_pages is None else min(len(doc), max_pages)
        logger.info(f"Chart detection: {pdf_path}, pages={len(doc)}, processing={page_count}")

        for page_idx in range(page_count):
            page = doc[page_idx]
            page_num = page_idx + 1

            for img_idx, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha > 3 or "CMYK" in str(pix.colorspace).upper():
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Skip unreadable embedded image page={page_num} xref={xref}: {exc}")
                    continue

                if pix.width < 100 or pix.height < 100:
                    continue

                stem = f"chart_p{page_num:03d}_img{img_idx:02d}_{_short_hash(pdf_path, page_num, xref, img_idx)}"
                img_path = _save_pixmap(pix, out_dir, stem, fitz)
                if img_path is None:
                    continue

                charts.append(
                    ChartRegion(
                        chart_id=f"chart_p{page_num:03d}_img{img_idx:02d}",
                        page=page_num,
                        bbox=(0.0, 0.0, float(pix.width), float(pix.height)),
                        chart_type=_guess_chart_type(pix.width, pix.height),
                        image_path=str(img_path),
                    )
                )

            try:
                drawings = page.get_drawings()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Skip unreadable vector drawings page={page_num}: {exc}")
                continue

            for draw_idx, bbox in enumerate(_merge_drawings(drawings), start=1):
                x0, y0, x1, y1 = bbox
                width = x1 - x0
                height = y1 - y0
                if width < 100 or height < 100:
                    continue
                aspect = width / height if height > 0 else 999
                if aspect > 10 or aspect < 0.1:
                    continue

                try:
                    rect = fitz.Rect(x0, y0, x1, y1)
                    pix = page.get_pixmap(clip=rect, dpi=150)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Skip vector chart crop page={page_num}: {exc}")
                    continue
                if pix.width <= 0 or pix.height <= 0:
                    continue

                stem = f"chart_p{page_num:03d}_draw{draw_idx:02d}_{_short_hash(pdf_path, page_num, bbox, draw_idx)}"
                img_path = _save_pixmap(pix, out_dir, stem, fitz)
                if img_path is None:
                    continue

                charts.append(
                    ChartRegion(
                        chart_id=f"chart_p{page_num:03d}_draw{draw_idx:02d}",
                        page=page_num,
                        bbox=(x0, y0, x1, y1),
                        chart_type="unknown",
                        image_path=str(img_path),
                    )
                )
    finally:
        doc.close()

    logger.info(f"Chart detection complete: {len(charts)} regions")
    return charts


def _short_hash(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:8]


def _unique_output_path(out_dir: Path, stem: str) -> Path:
    candidate = out_dir / f"{stem}.png"
    if not candidate.exists():
        return candidate
    for idx in range(2, 1000):
        candidate = out_dir / f"{stem}_{idx}.png"
        if not candidate.exists():
            return candidate
    return out_dir / f"{stem}_{_short_hash(stem, 'overflow')}.png"


def _save_pixmap(pix, out_dir: Path, stem: str, fitz_module) -> Path | None:
    img_path = _unique_output_path(out_dir, stem)
    try:
        pix.save(str(img_path))
        return img_path
    except ValueError:
        try:
            converted = fitz_module.Pixmap(fitz_module.csRGB, pix)
            converted.save(str(img_path))
            return img_path
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Chart image save failed: {img_path} ({exc})")
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Chart image save failed: {img_path} ({exc})")
        return None


def _guess_chart_type(width: int, height: int) -> str:
    """Rough chart type guess based on image aspect ratio."""
    aspect = width / height if height > 0 else 1.0
    if 0.8 <= aspect <= 1.2:
        return "pie"
    if aspect > 2.0:
        return "bar"
    if aspect < 0.5:
        return "line"
    return "unknown"


def _merge_drawings(drawings: list[dict], threshold: float = 20.0) -> list[tuple[float, float, float, float]]:
    """Merge nearby drawing bounding boxes."""
    if not drawings:
        return []

    bboxes = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect:
            bboxes.append((rect.x0, rect.y0, rect.x1, rect.y1))

    if not bboxes:
        return []

    bboxes.sort(key=lambda item: (item[1], item[0]))
    merged = [list(bboxes[0])]

    for bbox in bboxes[1:]:
        last = merged[-1]
        horizontal_overlap = not (bbox[2] < last[0] - threshold or bbox[0] > last[2] + threshold)
        vertical_overlap = not (bbox[3] < last[1] - threshold or bbox[1] > last[3] + threshold)

        if horizontal_overlap and vertical_overlap:
            last[0] = min(last[0], bbox[0])
            last[1] = min(last[1], bbox[1])
            last[2] = max(last[2], bbox[2])
            last[3] = max(last[3], bbox[3])
        else:
            merged.append(list(bbox))

    return [tuple(item) for item in merged]
