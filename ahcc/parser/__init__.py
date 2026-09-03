"""年报解析层（P2 主负责）— 把 PDF/HTML 转成 ReportDocument。"""

from ahcc.schemas import ReportDocument, ReportSide


def parse_report(file_path: str, side: ReportSide) -> ReportDocument:
    """统一入口：按文件后缀和报告侧（A/H）路由到具体解析器。"""
    from pathlib import Path
    suffix = Path(file_path).suffix.lower()
    if side == ReportSide.A_SHARE:
        from ahcc.parser.pdf_a import parse_a_pdf
        doc = parse_a_pdf(file_path)
    elif suffix in {".html", ".htm"}:
        from ahcc.parser.pdf_h_html import parse_h_html
        doc = parse_h_html(file_path)
    else:
        from ahcc.parser.pdf_h_html import parse_h_pdf
        doc = parse_h_pdf(file_path)
    _annotate_tables(doc)
    return doc


def _annotate_tables(doc: ReportDocument) -> None:
    """列头注解：为每张表构建列坐标系（column_headers/header_row_indices/period）。

    放在 parse_report 出口统一做 —— H 股解析缓存在 parse_h_pdf 内部，
    缓存命中与新鲜解析都会经过这里，无需 bump 缓存版本即可获得列头。
    """
    from ahcc.table import annotate_table

    for table in doc.tables:
        try:
            annotate_table(table)
        except Exception:  # noqa: BLE001 — 单表注解失败不阻断解析
            continue
