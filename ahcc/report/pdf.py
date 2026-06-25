"""PDF 报告导出（P3 实现）— reportlab + KPMG 蓝主题 + 中文字体。

结构：封面抬头 → 执行摘要仪表盘 → 严重度/类型分布图 → 差异总表（含定位列、配色分级）
→ 重大/严重差异明细卡（定位、准则引用、审阅提示）→ 披露覆盖表 → 页脚页码。
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from ahcc.report import _style as S
from ahcc.schemas import Job

_FONT_REGISTERED = False
_ILLEGAL_TEXT_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")
_MAX_TABLE_ROWS = 60
_MAX_DETAIL_CARDS = 30


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return _ILLEGAL_TEXT_RE.sub("", str(value))


def _fmt_num(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _diff_explanation_text(diff) -> str:
    explanation = getattr(diff, "diff_explanation", None)
    if not explanation:
        return diff.summary.best()
    parts = [explanation.headline, explanation.issue, explanation.location]
    return "；".join(part for part in parts if part)


def _diff_issue_text(diff) -> str:
    explanation = getattr(diff, "diff_explanation", None)
    if explanation and (explanation.issue or explanation.headline):
        return explanation.issue or explanation.headline
    return diff.summary.best()


def _evidence_location(diff) -> str:
    parts = [f"{e.side.value} P.{e.page}" for e in diff.evidence if getattr(e, "page", None)]
    return " / ".join(parts) if parts else "—"


def _side_location_text(diff, side: str, side_label: str) -> str:
    explanation = getattr(diff, "diff_explanation", None)
    if explanation and explanation.items:
        lines = []
        for item in explanation.items:
            page = item.a_page if side == "A" else item.h_page
            value = item.a_value if side == "A" else item.h_value
            snippet = item.a_snippet if side == "A" else item.h_snippet
            page_text = f"{side_label} 第{page}页" if page else f"{side_label} 未定位"
            label = item.label or "取值"
            line = f"{page_text} | {label}: {_fmt_num(value)}"
            if snippet:
                line += f" | {snippet}"
            lines.append(line)
        return "\n".join(lines)
    evidence = [e for e in diff.evidence if (e.side.value if hasattr(e.side, "value") else e.side) == side]
    return "\n".join(
        f"{side_label} 第{e.page}页 | {e.snippet or ''}" if e.page else f"{side_label} 未定位 | {e.snippet or ''}"
        for e in evidence
    ) or "—"


def _report_title(job: Job) -> str:
    if getattr(job, "check_mode", "ah") == "h_bilingual":
        return "H 股中英文报告一致性核查报告"
    return "A+H 股年报数据一致性核查报告"


def _side_labels(job: Job) -> dict[str, str]:
    if getattr(job, "check_mode", "ah") == "h_bilingual":
        return {"A": "中文", "H": "英文"}
    return {"A": "A", "H": "H"}


def _ensure_cjk_font():
    """注册中文字体：优先 Microsoft YaHei，回退 SimHei / STSong-Light。"""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("YaHei", Path("C:/Windows/Fonts/msyh.ttc"), 0),
        ("YaHei", Path("C:/Windows/Fonts/msyh.ttc"), 1),  # 某些 TTC 索引为 1
        ("SimHei", Path("C:/Windows/Fonts/simhei.ttf"), None),
    ]
    for name, path, idx in candidates:
        if not path.exists():
            continue
        try:
            if idx is not None:
                pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=idx))
            else:
                pdfmetrics.registerFont(TTFont(name, str(path)))
            _FONT_REGISTERED = True
            logger.debug(f"PDF 字体已注册：{name} ({path})")
            return
        except Exception as exc:
            logger.warning(f"字体注册失败 {path} idx={idx}: {exc}")

    # 最终回退：CID 字体
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _FONT_REGISTERED = True


def _font_name() -> str:
    from reportlab.pdfbase import pdfmetrics

    if "YaHei" in pdfmetrics._fonts:
        return "YaHei"
    if "SimHei" in pdfmetrics._fonts:
        return "SimHei"
    return "STSong-Light"


def export_pdf(job: Job, out_path: Path) -> None:
    """导出 PDF 总览报告（评委友好版）。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    _ensure_cjk_font()
    fn = _font_name()
    ink = colors.HexColor("#" + S.INK)
    ink_soft = colors.HexColor("#" + S.INK_SOFT)
    navy = colors.HexColor("#" + S.KPMG_BLUE)

    styles = {
        "title": ParagraphStyle("CJKTitle", fontName=fn, fontSize=20, leading=28, spaceAfter=2, textColor=ink),
        "subtitle": ParagraphStyle("CJKSub", fontName=fn, fontSize=9, leading=14, spaceAfter=2, textColor=ink_soft),
        "section": ParagraphStyle("CJKSection", fontName=fn, fontSize=12, leading=18, spaceBefore=14, spaceAfter=8, textColor=ink),
        "normal": ParagraphStyle("CJKNormal", fontName=fn, fontSize=10, leading=16, spaceAfter=3, textColor=ink),
        "muted": ParagraphStyle("CJKMuted", fontName=fn, fontSize=8.5, leading=12, textColor=ink_soft),
        "header": ParagraphStyle("CJKHeader", fontName=fn, fontSize=9, leading=13, textColor=ink),
        "cell": ParagraphStyle("CJKCell", fontName=fn, fontSize=8.5, leading=13, textColor=ink),
        "card_label": ParagraphStyle("CJKCardL", fontName=fn, fontSize=8.5, leading=12, textColor=ink_soft),
        "card_value": ParagraphStyle("CJKCardV", fontName=fn, fontSize=8.5, leading=12, textColor=ink),
        "chart_title": ParagraphStyle("CJKChartTitle", fontName=fn, fontSize=10, leading=14, textColor=ink),
        "footer": ParagraphStyle("CJKFooter", fontName=fn, fontSize=7.5, leading=10, textColor=colors.HexColor("#" + S.FOOTER_TEXT)),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        topMargin=16 * mm, bottomMargin=16 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
    )

    story = []
    story.extend(_build_header(job, styles, fn))
    story.append(Spacer(1, 10))
    story.extend(_build_dashboard(job, styles, fn))
    story.extend(_build_charts(job, styles, fn))
    story.append(Spacer(1, 6))
    story.extend(_build_diff_table(job, styles, fn))
    story.extend(_build_detail_cards(job, styles, fn))
    story.extend(_build_coverage(job, styles, fn))

    doc.build(story, onFirstPage=lambda c, d: _on_page(c, d, fn), onLaterPages=lambda c, d: _on_page(c, d, fn))
    logger.info(f"PDF 报告已导出：{out_path}")


def _on_page(canvas, doc, fn: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    canvas.saveState()
    canvas.setFont(fn, 7.5)
    canvas.setFillColor(colors.HexColor("#" + S.FOOTER_TEXT))
    width = doc.pagesize[0]
    canvas.drawString(15 * mm, 8 * mm, "KPMG · A+H Consistency Checker · 保密")
    canvas.drawRightString(width - 15 * mm, 8 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#" + S.HAIRLINE))
    canvas.line(15 * mm, 11 * mm, width - 15 * mm, 11 * mm)
    canvas.restoreState()


def _build_header(job: Job, styles, fn):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    def _ts(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    gen_time = _ts(job.finished_at) or _ts(job.started_at) or "—"
    dur = f"{job.duration_seconds:.1f} 秒" if job.duration_seconds else "—"
    meta = (
        f"公司：{_clean_text(job.company_name or '—')}　|　任务编号：{_clean_text(job.job_id)}　|　"
        f"核查耗时：{dur}　|　生成时间：{gen_time}"
    )
    inner = [
        [Paragraph(_clean_text(_report_title(job)), styles["title"])],
        [Paragraph(meta, styles["subtitle"])],
    ]
    t = Table(inner, colWidths=[180 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (0, 0), 12),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#" + S.KPMG_BLUE)),
    ]))
    return [t]


def _build_dashboard(job: Job, styles, fn):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    summary = job.comparison_summary or {}
    bilingual = getattr(job, "check_mode", "ah") == "h_bilingual"
    real_count = summary.get("real_diff_count", sum(1 for d in job.diffs if d.triage == "real"))

    cards = [
        ("差异总数", summary.get("total_diff_count", len(job.diffs)), S.INK, S.KPMG_BLUE),
        ("真实差异", real_count, S.ALERT if real_count else S.INK, S.ALERT if real_count else S.KPMG_BLUE),
        ("预期差异", summary.get("expected_diff_count", sum(1 for d in job.diffs if d.triage == "expected")), S.INK, S.KPMG_BLUE),
        ("待判断", summary.get("unresolved_diff_count", sum(1 for d in job.diffs if d.triage == "unresolved")), S.INK, S.KPMG_BLUE),
        ("披露覆盖", summary.get("coverage_count", len(job.coverage_items)), S.INK, S.KPMG_BLUE),
        ("提取预警", summary.get("warning_count", 0), S.INK, S.KPMG_BLUE),
    ]

    def make_card(label, value, value_color, accent_color):
        return Table(
            [[Paragraph(_clean_text(str(value)), ParagraphStyle_cardvalue_large(fn, value_color))],
             [Paragraph(_clean_text(label), ParagraphStyle_cardcaption(fn))]],
            colWidths=[52 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + S.DASHBOARD_CARD_BG)),
                ("LINEABOVE", (0, 0), (-1, 0), 2.0, colors.HexColor("#" + accent_color)),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]),
        )

    rows = [
        [make_card(*cards[i]) for i in range(3)],
        [make_card(*cards[i]) for i in range(3, 6)],
    ]
    grid = Table(rows, colWidths=[56 * mm] * 3, hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    out = [Paragraph("执行摘要", styles["section"]), grid]

    if bilingual:
        def pct(v):
            return f"{(v or 0) * 100:.1f}%"
        bi = (
            f"翻译覆盖率 {pct(summary.get('translation_coverage'))}　|　"
            f"表格覆盖率 {pct(summary.get('table_coverage'))}　|　"
            f"跨币种核对一致 {summary.get('cross_currency_matched', 0)} 项"
        )
        out.append(Spacer(1, 4))
        out.append(Paragraph(_clean_text(bi), styles["muted"]))
    return out


def ParagraphStyle_cardvalue_large(fn, color):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        "cardVL", fontName=fn, fontSize=22, leading=24, alignment=1,
        textColor=colors.HexColor("#" + color),
    )


def ParagraphStyle_cardcaption(fn):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        "cardVC", fontName=fn, fontSize=9, leading=11, alignment=1,
        textColor=colors.HexColor("#" + S.INK_SOFT),
    )


def _build_charts(job: Job, styles, fn):
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    sev = S.severity_distribution(job.diffs)
    typ = S.type_distribution(job.diffs)
    if not sev and not typ:
        return [Paragraph("本次核查未识别差异。", styles["muted"])]

    drawings = []
    try:
        if sev:
            sev_colors = ["#" + S.mono_color(i) for i in range(len(sev))]
            drawings.append(_bar_drawing("严重度分布", sev, sev_colors, fn, styles["chart_title"]))
        if typ:
            type_colors = ["#" + S.mono_color(i) for i in range(len(typ))]
            drawings.append(_bar_drawing("差异类型分布", typ, type_colors, fn, styles["chart_title"]))
    except Exception as exc:
        logger.warning(f"PDF 分布图渲染失败：{exc}")
        return []

    if not drawings:
        return []
    row = [d for d in drawings]
    while len(row) < 2:
        row.append("")
    grid = Table([row], colWidths=[90 * mm, 90 * mm], hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [Spacer(1, 4), Paragraph("分布概览", styles["section"]), grid]


def _bar_drawing(title, dist, color_hexes, fn, title_style):
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib.colors import HexColor

    values = list(dist.values())
    names = list(dist.keys())
    d = Drawing(200, 150)
    d.add(String(4, 132, _clean_text(title), fontName=title_style.fontName, fontSize=title_style.fontSize, fillColor=HexColor("#" + S.CHART_TITLE_COLOR)))

    chart = VerticalBarChart()
    chart.x = 14
    chart.y = 20
    chart.width = 172
    chart.height = 96
    chart.data = [values]
    chart.categoryAxis.categoryNames = [_clean_text(n) for n in names]
    chart.categoryAxis.labels.fontName = fn
    chart.categoryAxis.labels.fontSize = 7.5
    chart.categoryAxis.labels.dy = -2
    chart.categoryAxis.strokeColor = None
    chart.categoryAxis.visibleTicks = 0
    chart.valueAxis.visible = 0
    chart.valueAxis.valueMin = 0
    maxv = max(values) if values else 1
    chart.valueAxis.valueMax = maxv
    chart.barWidth = 10
    chart.groupSpacing = 18
    chart.barLabels.fontName = fn
    chart.barLabels.fontSize = 7.5
    chart.barLabels.fillColor = HexColor("#" + S.INK_SOFT)
    chart.barLabelFormat = "%d"
    chart.barLabels.nudge = 8
    chart.strokeColor = None
    for i in range(len(values)):
        chart.bars[(0, i)].fillColor = HexColor(color_hexes[i % len(color_hexes)])
        chart.bars[(0, i)].strokeColor = None
    d.add(chart)
    return d


def _build_diff_table(job: Job, styles, fn):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if not job.diffs:
        return []

    sorted_diffs = sorted(job.diffs, key=lambda d: S.severity_rank(d.severity), reverse=True)
    shown = sorted_diffs[:_MAX_TABLE_ROWS]

    header = [Paragraph(h, styles["header"]) for h in ["ID", "类型", "严重度", "分流", "主题", "定位", "差异说明"]]
    data = [header]
    accent_rows = []  # (row_idx, severity_key)
    for i, d in enumerate(shown, start=1):
        sev_key = str(getattr(d.severity, "value", d.severity)).lower()
        accent_rows.append((i, sev_key))
        data.append([
            Paragraph(_clean_text(str(d.diff_id)), styles["cell"]),
            Paragraph(_clean_text(S.diff_type_label_zh(d.diff_type)), styles["cell"]),
            Paragraph(_clean_text(S.severity_label_zh(d.severity)), _sev_cell_style(fn, sev_key)),
            Paragraph(_clean_text(S.triage_label_zh(d.triage)), _triage_cell_style(fn, str(getattr(d.triage, "value", d.triage)).lower())),
            Paragraph(_clean_text(d.topic.best()), styles["cell"]),
            Paragraph(_clean_text(_evidence_location(d)), styles["cell"]),
            Paragraph(_clean_text(_diff_explanation_text(d)[:160]), styles["cell"]),
        ])

    col_widths = [14 * mm, 16 * mm, 13 * mm, 16 * mm, 30 * mm, 24 * mm, 67 * mm]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#" + S.INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#" + S.KPMG_BLUE)),
        ("FONTNAME", (0, 0), (-1, -1), fn),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#" + S.HAIRLINE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + S.STRIPE)]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_idx, sev_key in accent_rows:
        width = S.severity_border_width(sev_key)
        if width:
            color = S.ALERT if S.severity_is_high(sev_key) else S.INK_SOFT
            style.append(("LINEBEFORE", (0, row_idx), (0, row_idx), width, colors.HexColor("#" + color)))
    table.setStyle(TableStyle(style))

    out = [Paragraph("差异总览（按严重度排序）", styles["section"]), table]
    if len(sorted_diffs) > _MAX_TABLE_ROWS:
        out.append(Spacer(1, 3))
        out.append(Paragraph(f"注：另有 {len(sorted_diffs) - _MAX_TABLE_ROWS} 条差异详见 Excel 报告「差异清单」。", styles["muted"]))
    return out


def _sev_cell_style(fn, sev_key):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        f"sev_{sev_key}", fontName=fn, fontSize=8, leading=11, alignment=1,
        textColor=colors.HexColor("#" + S.severity_accent(sev_key)),
    )


def _triage_cell_style(fn, tri_key):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        f"tri_{tri_key}", fontName=fn, fontSize=8, leading=11, alignment=1,
        textColor=colors.HexColor("#" + S.triage_accent(tri_key)),
    )


def _build_detail_cards(job: Job, styles, fn):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    focus = [d for d in job.diffs if str(getattr(d.severity, "value", d.severity)).lower() in ("high", "critical")]
    focus = sorted(focus, key=lambda d: S.severity_rank(d.severity), reverse=True)
    if not focus:
        return []

    labels = _side_labels(job)
    out = [Spacer(1, 6), Paragraph("重大/严重差异明细", styles["section"])]
    for d in focus[:_MAX_DETAIL_CARDS]:
        sev_key = str(getattr(d.severity, "value", d.severity)).lower()
        accent_hex = S.severity_accent(sev_key)

        title_row = [
            Paragraph(_clean_text(d.topic.best()), ParagraphStyle_cardtitle(fn)),
            Paragraph(_clean_text(S.severity_label_zh(d.severity)), _sev_cell_style(fn, sev_key)),
        ]
        rows = [[
            Table([title_row], colWidths=[140 * mm, 22 * mm], style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]), hAlign="LEFT"),
            "",
        ]]

        def kv(label, value):
            rows.append([
                Paragraph(_clean_text(label), styles["card_label"]),
                Paragraph(_clean_text(value), styles["card_value"]),
            ])

        kv("问题", _diff_issue_text(d))
        kv("类型 / 分流", f"{S.diff_type_label_zh(d.diff_type)} / {S.triage_label_zh(d.triage)}")
        kv(f"{labels['A']} 定位与取值", _side_location_text(d, "A", labels["A"]))
        kv(f"{labels['H']} 定位与取值", _side_location_text(d, "H", labels["H"]))
        if d.delta is not None:
            kv("差异值", _fmt_num(d.delta))
        citation = S.standard_citation_text(d)
        if citation:
            kv("准则引用", citation)
        hint = (d.diff_explanation.review_hint if d.diff_explanation and d.diff_explanation.review_hint else "")
        if hint:
            kv("审阅提示", hint)

        card = Table(rows, colWidths=[28 * mm, 134 * mm])
        width = S.severity_border_width(sev_key)
        card.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("FONTNAME", (0, 0), (-1, -1), fn),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + S.PANEL)),
            ("LINEBEFORE", (0, 0), (0, -1), width, colors.HexColor("#" + accent_hex)),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#" + S.HAIRLINE)),
            ("INNERGRID", (0, 1), (-1, -1), 0.3, colors.HexColor("#" + S.HAIRLINE)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        out.append(KeepTogether([card, Spacer(1, 6)]))

    if len(focus) > _MAX_DETAIL_CARDS:
        out.append(Paragraph(f"注：另有 {len(focus) - _MAX_DETAIL_CARDS} 条重大/严重差异详见 Excel 报告。", styles["muted"]))
    return out


def ParagraphStyle_cardtitle(fn):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle("cardT", fontName=fn, fontSize=11, leading=14, textColor=colors.HexColor("#" + S.INK))


def _build_coverage(job: Job, styles, fn):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if not job.coverage_items:
        return []

    header = [Paragraph(h, styles["header"]) for h in ["状态", "类别", "主题", "A页码", "H页码", "说明"]]
    data = [header]
    for item in job.coverage_items[:_MAX_TABLE_ROWS]:
        data.append([
            Paragraph(_clean_text(item.status), styles["cell"]),
            Paragraph(_clean_text(item.category), styles["cell"]),
            Paragraph(_clean_text(item.topic.best()), styles["cell"]),
            Paragraph(_clean_text(",".join(str(p) for p in item.a_pages) or "—"), styles["cell"]),
            Paragraph(_clean_text(",".join(str(p) for p in item.h_pages) or "—"), styles["cell"]),
            Paragraph(_clean_text(item.note[:120]), styles["cell"]),
        ])
    col_widths = [18 * mm, 18 * mm, 36 * mm, 16 * mm, 16 * mm, 76 * mm]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#" + S.INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#" + S.KPMG_BLUE)),
        ("FONTNAME", (0, 0), (-1, -1), fn),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#" + S.HAIRLINE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + S.STRIPE)]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [Spacer(1, 6), Paragraph("披露覆盖 / 未匹配项", styles["section"]), table]
