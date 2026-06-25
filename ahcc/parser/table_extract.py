"""表格抽取（P2 实现）— camelot 主，PaddleOCR PPStructure 兜底。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger

from ahcc.schemas import FinancialTable, LocalizedString, TableCell


def extract_tables_camelot(
    pdf_path: str,
    page_range: str = "all",
    use_lattice: bool = False,
) -> list[FinancialTable]:
    """用 camelot lattice + stream 双模式抽取有边框/半边框表格。

    Args:
        use_lattice: 是否启用 lattice 模式。默认为 False，因为 lattice 对大部分
            年报PDF极慢且召回率低，stream 模式足以补充无边框表格。
    """
    try:
        import camelot
    except ImportError:
        logger.warning("camelot-py 未安装，跳过 camelot 提取")
        return []

    tables: list[FinancialTable] = []
    pages = page_range if page_range else "all"

    # lattice 模式：有边框表格（默认关闭，速度极慢）
    if use_lattice:
        try:
            lattice_tables = camelot.read_pdf(pdf_path, pages=pages, flavor="lattice")
            for i, t in enumerate(lattice_tables, start=1):
                if t.df.empty:
                    continue
                ft = _camelot_df_to_financial(t.df, page=t.page, table_id=f"camelot_L_p{t.page}_t{i}")
                tables.append(ft)
            logger.info(f"camelot lattice 提取 {len(lattice_tables)} 表")
        except Exception as e:
            logger.warning(f"camelot lattice 失败: {e}")

    # stream 模式：无边框/弱边框表格
    try:
        stream_tables = camelot.read_pdf(pdf_path, pages=pages, flavor="stream")
        for i, t in enumerate(stream_tables, start=1):
            if t.df.empty:
                continue
            ft = _camelot_df_to_financial(t.df, page=t.page, table_id=f"camelot_S_p{t.page}_t{i}")
            tables.append(ft)
        logger.info(f"camelot stream 提取 {len(stream_tables)} 表")
    except Exception as e:
        logger.warning(f"camelot stream 失败: {e}")

    return tables


def _camelot_df_to_financial(df, page: int, table_id: str) -> FinancialTable:
    """camelot DataFrame 转 FinancialTable。"""
    cells: list[TableCell] = []
    for r_idx, row in df.iterrows():
        for c_idx, val in enumerate(row):
            text = str(val) if val is not None else ""
            is_header = r_idx == 0
            cells.append(TableCell(row=int(r_idx), col=c_idx, text=text, is_header=is_header))

    # 推断标题：看第一行是否有合并单元格特征或特殊关键词
    title = ""
    if not df.empty:
        first_row = [str(c) for c in df.iloc[0] if c is not None]
        title = " ".join(first_row)[:50]

    return FinancialTable(
        table_id=table_id,
        title=LocalizedString(zh=title),
        page=page,
        bbox=(0.0, 0.0, 0.0, 0.0),
        cells=cells,
    )


def extract_tables_ppstructure(pdf_path: str, pages: Iterable[int] | None = None) -> list[FinancialTable]:
    """PaddleOCR PPStructure 兜底，处理无边框/扫描件/复杂布局表格。

    仅渲染指定页，避免整份 PDF 转图片。
    """
    try:
        from paddleocr import PPStructure
    except ImportError:
        logger.warning("PaddleOCR 未安装，跳过 PPStructure 兜底")
        return []

    try:
        import fitz
        import numpy as np
        from PIL import Image
    except ImportError as e:
        logger.warning(f"PPStructure 页级渲染依赖缺失，跳过兜底: {e}")
        return []

    tables: list[FinancialTable] = []
    doc = None
    try:
        doc = fitz.open(pdf_path)
        page_numbers = _normalize_pages(pages, len(doc))
        if not page_numbers:
            return []

        engine = PPStructure(show_log=False, lang="ch")

        for page_idx in page_numbers:
            page = doc[page_idx - 1]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            mode = "RGBA" if pix.n >= 4 else "RGB"
            image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            if image.mode != "RGB":
                image = image.convert("RGB")
            result = engine(np.array(image))
            for res in result:
                if res.get("type") != "table":
                    continue
                # PPStructure 返回 html 格式表格
                html_table = res.get("res", {}).get("html", "")
                if not html_table:
                    continue
                table_idx = sum(1 for t in tables if t.page == page_idx) + 1
                ft = _pp_html_to_financial(html_table, page=page_idx, table_idx=table_idx)
                if ft:
                    tables.append(ft)
        logger.info(f"PPStructure 提取 {len(tables)} 表")
    except Exception as e:
        logger.warning(f"PPStructure 提取失败: {e}")
    finally:
        if doc is not None:
            doc.close()

    return tables


def _pp_html_to_financial(html_table: str, page: int, table_idx: int = 1) -> FinancialTable | None:
    """将 PPStructure 输出的 HTML 表格转为 FinancialTable。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    soup = BeautifulSoup(html_table, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    rows = table.find_all("tr")
    if not rows:
        return None

    cells: list[TableCell] = []
    for r_idx, row in enumerate(rows):
        cols = row.find_all(["td", "th"])
        for c_idx, col in enumerate(cols):
            text = col.get_text(strip=True)
            is_header = col.name == "th" or r_idx == 0
            cells.append(TableCell(row=r_idx, col=c_idx, text=text, is_header=is_header))

    return FinancialTable(
        table_id=f"pp_p{page:03d}_t{table_idx:02d}",
        title=LocalizedString(zh=""),
        page=page,
        bbox=(0.0, 0.0, 0.0, 0.0),
        cells=cells,
    )


def _normalize_pages(pages: Iterable[int] | None, total_pages: int) -> list[int]:
    if pages is None:
        return list(range(1, total_pages + 1))
    result: set[int] = set()
    for page in pages:
        try:
            value = int(page)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= total_pages:
            result.add(value)
    return sorted(result)


def merge_tables(*sources: list[FinancialTable]) -> list[FinancialTable]:
    """合并多引擎结果，去重（按 table_id 去重 + bbox 重叠度去重）。"""
    seen_ids: set[str] = set()
    merged: list[FinancialTable] = []

    for src in sources:
        for t in src:
            # 1. ID 去重
            if t.table_id in seen_ids:
                continue
            seen_ids.add(t.table_id)

            # 2. bbox 重叠度去重（与已合并列表中的表比较）
            if _is_duplicate_by_bbox(t, merged):
                continue

            merged.append(t)

    return merged


def _is_duplicate_by_bbox(t: FinancialTable, existing: list[FinancialTable], overlap_threshold: float = 0.7) -> bool:
    """判断表格是否与已合并列表中的某表格 bbox 高度重叠。"""
    tx0, ty0, tx1, ty1 = t.bbox
    t_area = (tx1 - tx0) * (ty1 - ty0)
    if t_area <= 0:
        return False

    for e in existing:
        # 只比较同页
        if e.page != t.page:
            continue
        ex0, ey0, ex1, ey1 = e.bbox
        e_area = (ex1 - ex0) * (ey1 - ey0)
        if e_area <= 0:
            continue

        # 计算交集面积
        ix0 = max(tx0, ex0)
        iy0 = max(ty0, ey0)
        ix1 = min(tx1, ex1)
        iy1 = min(ty1, ey1)
        if ix0 >= ix1 or iy0 >= iy1:
            continue
        inter_area = (ix1 - ix0) * (iy1 - iy0)

        # IoU 或重叠度
        union_area = t_area + e_area - inter_area
        iou = inter_area / union_area if union_area > 0 else 0
        if iou >= overlap_threshold:
            return True

    return False
