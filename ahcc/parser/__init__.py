"""年报解析层（P2 主负责）— 把 PDF/HTML 转成 ReportDocument。"""

from ahcc.schemas import ReportDocument, ReportSide


def parse_report(file_path: str, side: ReportSide) -> ReportDocument:
    """统一入口：按文件后缀和报告侧（A/H）路由到具体解析器。"""
    from pathlib import Path
    suffix = Path(file_path).suffix.lower()
    if side == ReportSide.A_SHARE:
        from ahcc.parser.pdf_a import parse_a_pdf
        return parse_a_pdf(file_path)
    if suffix in {".html", ".htm"}:
        from ahcc.parser.pdf_h_html import parse_h_html
        return parse_h_html(file_path)
    from ahcc.parser.pdf_h_html import parse_h_pdf
    return parse_h_pdf(file_path)
