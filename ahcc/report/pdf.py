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
_FONT_MAP: dict[str, str] = {}  # weight -> 已注册字体名
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
    """注册中文字体三字重：YaHei Light / Regular / Bold，逐级回退 SimHei / STSong-Light。"""
    global _FONT_REGISTERED, _FONT_MAP
    if _FONT_REGISTERED:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # 尝试注册三字重（msyhl/msyh/msyhbd 均为 TTC，子字体索引 0）
    for weight in ("light", "regular", "bold"):
        path = S.FONT_PATHS.get(weight)
        name = S.FONT_NAMES.get(weight)
        if not path or not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
            _FONT_MAP[weight] = name
            logger.debug(f"PDF 字体已注册：{weight} → {name} ({path})")
        except Exception as exc:
            logger.warning(f"字体注册失败 {path}（{weight}）：{exc}")

    # 缺失字重 → 回退到 regular（若有）
    if "regular" in _FONT_MAP:
        _FONT_MAP.setdefault("light", _FONT_MAP["regular"])
        _FONT_MAP.setdefault("bold", _FONT_MAP["regular"])

    # YaHei 全失败 → 回退 SimHei
    if not _FONT_MAP:
        sim_name, sim_path = S.FONT_FALLBACK_TTF
        if sim_path.exists():
            try:
                pdfmetrics.registerFont(TTFont(sim_name, str(sim_path)))
                _FONT_MAP = {"light": sim_name, "regular": sim_name, "bold": sim_name}
            except Exception as exc:
                logger.warning(f"SimHei 注册失败：{exc}")

    # 最终回退：CID 字体
    if not _FONT_MAP:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        pdfmetrics.registerFont(UnicodeCIDFont(S.FONT_FALLBACK_CID))
        _FONT_MAP = {k: S.FONT_FALLBACK_CID for k in ("light", "regular", "bold")}

    _FONT_REGISTERED = True


def _font(weight: str = "regular") -> str:
    """按字重返回已注册字体名；缺失回退 regular → STSong-Light。"""
    if not _FONT_REGISTERED:
        _ensure_cjk_font()
    return _FONT_MAP.get(weight) or _FONT_MAP.get("regular") or S.FONT_FALLBACK_CID


def _ps(role: str, fn_map: dict, **overrides):
    """按 FONT_ROLES 角色构造 reportlab ParagraphStyle。"""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    cfg = S.font_role(role)
    params = dict(
        name=f"role_{role}",
        fontName=fn_map[cfg["weight"]],
        fontSize=cfg["size"],
        leading=cfg["leading"],
        textColor=colors.HexColor("#" + cfg["color"]),
    )
    if "color" in overrides:
        params["textColor"] = colors.HexColor("#" + overrides.pop("color"))
    params.update(overrides)
    return ParagraphStyle(**params)


def export_pdf(job: Job, out_path: Path) -> None:
    """导出 PDF 总览报告（苹果风格 + 专业金融质感版）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer

    _ensure_cjk_font()
    fn_map = {"light": _font("light"), "regular": _font("regular"), "bold": _font("bold")}

    styles = {
        "cover_eyebrow": _ps("cover_eyebrow", fn_map),
        "cover_title": _ps("cover_title", fn_map),
        "cover_subtitle": _ps("cover_subtitle", fn_map),
        "cover_meta": _ps("cover_meta", fn_map),
        "cover_confidential": _ps("cover_confidential", fn_map),
        "section_eyebrow": _ps("section_eyebrow", fn_map, spaceAfter=2),
        "section": _ps("section_title", fn_map, spaceBefore=14, spaceAfter=8),
        "kpi_number": _ps("kpi_number", fn_map, alignment=1),
        "kpi_alert": _ps("kpi_alert", fn_map, alignment=1),
        "kpi_label": _ps("kpi_label", fn_map, alignment=1),
        "title": _ps("section_title", fn_map, fontSize=18, leading=24),
        "subtitle": _ps("cover_meta", fn_map),
        "normal": _ps("body", fn_map, spaceAfter=3),
        "muted": _ps("body_small", fn_map),
        "header": _ps("table_header", fn_map),
        "cell": _ps("table_cell", fn_map),
        "card_label": _ps("body_small", fn_map),
        "card_value": _ps("body_small", fn_map, color=S.INK),
        "chart_title": _ps("section_title", fn_map, fontSize=10, leading=14),
        "footer": _ps("footer", fn_map),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )

    import tempfile
    import shutil

    tmp_dir = Path(tempfile.mkdtemp(prefix="ahcc_pdf_chart_"))
    try:
        story = []
        # 封面由 onFirstPage 画在第一页；正文从第二页开始
        story.append(PageBreak())
        story.extend(_build_header(job, styles))
        story.append(Spacer(1, 10))
        story.extend(_build_dashboard(job, styles))
        story.extend(_build_charts(job, styles, tmp_dir))
        story.append(Spacer(1, 6))
        story.extend(_build_diff_table(job, styles))
        story.extend(_build_detail_cards(job, styles))
        story.extend(_build_coverage(job, styles))

        def _first(c, d):
            _draw_cover_page(c, d, job)

        def _later(c, d):
            _on_page(c, d)

        doc.build(story, onFirstPage=_first, onLaterPages=_later)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info(f"PDF 报告已导出：{out_path}")


def _draw_cover_page(canvas, doc, job: Job) -> None:
    """第一页封面：坐标精确绘制，大留白 + Light 大标题 + 海军蓝细线 + 元数据 + 保密声明。"""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    width, height = doc.pagesize
    fn_light = _font("light")
    fn_regular = _font("regular")
    fn_bold = _font("bold")

    canvas.saveState()

    # 顶部字标 eyebrow
    canvas.setFont(fn_bold, 10)
    canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.drawString(20 * mm, height - 26 * mm, S.WORDMARK_BRAND)
    canvas.setFont(fn_regular, 10)
    canvas.setFillColor(colors.HexColor("#" + S.FOOTER_TEXT))
    canvas.drawString(20 * mm + canvas.stringWidth(S.WORDMARK_BRAND, fn_bold, 10) + 6,
                      height - 26 * mm, "·  " + S.WORDMARK_PRODUCT)

    # 海军蓝细横线（标题上方）
    y_rule = height - 120 * mm
    canvas.setStrokeColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.setLineWidth(1.0)
    canvas.line(20 * mm, y_rule, 56 * mm, y_rule)

    # 大标题（Light 32，多行）
    title = _clean_text(_report_title(job))
    lines = _wrap_title(title)
    canvas.setFillColor(colors.HexColor("#" + S.INK))
    y_title = y_rule - 16 * mm
    for i, line in enumerate(lines):
        canvas.setFont(fn_light, 30)
        canvas.drawString(20 * mm, y_title - i * 13 * mm, line)

    # 公司名（Regular）
    company = _clean_text(job.company_name or "—")
    y_company = y_title - len(lines) * 13 * mm - 4 * mm
    canvas.setFont(fn_regular, 13)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawString(20 * mm, y_company, company)

    # 元数据块
    def _ts(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    gen_time = _ts(job.finished_at) or _ts(job.started_at) or "—"
    dur = S.format_duration(job.duration_seconds)
    meta_lines = [
        ("任务编号", _clean_text(job.job_id)),
        ("核查耗时", dur),
        ("生成时间", gen_time),
    ]
    y_meta = y_company - 18 * mm
    for label, value in meta_lines:
        canvas.setFont(fn_regular, 9)
        canvas.setFillColor(colors.HexColor("#" + S.NEUTRAL))
        canvas.drawString(20 * mm, y_meta, label)
        canvas.setFont(fn_regular, 9)
        canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
        canvas.drawString(44 * mm, y_meta, value)
        y_meta -= 6.5 * mm

    # 底部保密声明 + hairline
    canvas.setStrokeColor(colors.HexColor("#" + S.HAIRLINE))
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, 22 * mm, width - 20 * mm, 22 * mm)
    canvas.setFont(fn_light, 8)
    canvas.setFillColor(colors.HexColor("#B0B8C4"))
    canvas.drawString(20 * mm, 16 * mm, "本报告由系统自动生成，仅供内部核查使用；含保密信息，未经授权不得对外披露。")
    canvas.restoreState()


def _wrap_title(title: str) -> list[str]:
    """把报告标题断成≤2 行，优先在语义分隔处断开。"""
    if "数据一致性" in title:
        head, _, tail = title.partition("数据一致性")
        return [head + "数据", "一致性" + tail]
    if "中英文报告一致性" in title:
        head, _, tail = title.partition("中英文报告")
        return [head + "中英文报告", tail]
    if len(title) > 14:
        mid = len(title) // 2
        return [title[:mid], title[mid:]]
    return [title]


def _on_page(canvas, doc) -> None:
    """内容页（第 2 页起）：顶部 running header + 底部页脚，均以 hairline 分隔。"""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    fn = _font("regular")
    canvas.saveState()
    width, height = doc.pagesize

    # running header
    canvas.setFont(fn, 7.5)
    canvas.setFillColor(colors.HexColor("#" + S.FOOTER_TEXT))
    canvas.drawString(18 * mm, height - 12 * mm, S.WORDMARK)
    canvas.setStrokeColor(colors.HexColor("#" + S.HAIRLINE))
    canvas.setLineWidth(0.3)
    canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)

    # footer
    canvas.setFont(fn, 7.5)
    canvas.setFillColor(colors.HexColor("#" + S.FOOTER_TEXT))
    canvas.drawString(18 * mm, 8 * mm, "保密 · Confidential")
    canvas.drawRightString(width - 18 * mm, 8 * mm, f"第 {doc.page} 页")
    canvas.setStrokeColor(colors.HexColor("#" + S.HAIRLINE))
    canvas.setLineWidth(0.3)
    canvas.line(18 * mm, 11 * mm, width - 18 * mm, 11 * mm)
    canvas.restoreState()


def _build_header(job: Job, styles):
    """正文首页抬头（封面之后）：字标 eyebrow + 标题 + 海军蓝细线 + 元数据。"""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    def _ts(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    gen_time = _ts(job.finished_at) or _ts(job.started_at) or "—"
    dur = S.format_duration(job.duration_seconds)
    meta = (
        f"公司：{_clean_text(job.company_name or '—')}　|　任务编号：{_clean_text(job.job_id)}　|　"
        f"核查耗时：{dur}　|　生成时间：{gen_time}"
    )
    inner = [
        [Paragraph(S.WORDMARK, styles["cover_eyebrow"])],
        [Paragraph(_clean_text(_report_title(job)), styles["title"])],
        [Paragraph(meta, styles["subtitle"])],
    ]
    t = Table(inner, colWidths=[174 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (0, 0), 2),
        ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
        ("TOPPADDING", (0, 2), (-1, 2), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 1.2, colors.HexColor("#" + S.KPMG_BLUE)),
    ]))
    return [t]


def _section_eyebrow(title: str, styles):
    """章节眉标：bold navy eyebrow + 一条海军蓝细线，制造编辑式分区感。"""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    eyebrow = Paragraph(_clean_text(title), styles["section_eyebrow"])
    line = Table([[""]], colWidths=[174 * mm], rowHeights=[1.4])
    line.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + S.KPMG_BLUE)),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [Spacer(1, 12), eyebrow, Spacer(1, 2), line, Spacer(1, 8)]


def _build_dashboard(job: Job, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    summary = job.comparison_summary or {}
    bilingual = getattr(job, "check_mode", "ah") == "h_bilingual"
    real_count = summary.get("real_diff_count", sum(1 for d in job.diffs if d.triage == "real"))

    # (label, value, is_alert) —— oxblood 仅给「真实差异」且 > 0
    cards = [
        ("差异总数", summary.get("total_diff_count", len(job.diffs)), False),
        ("真实差异", real_count, True),
        ("预期差异", summary.get("expected_diff_count", sum(1 for d in job.diffs if d.triage == "expected")), False),
        ("待判断", summary.get("unresolved_diff_count", sum(1 for d in job.diffs if d.triage == "unresolved")), False),
        ("披露覆盖", summary.get("coverage_count", len(job.coverage_items)), False),
        ("提取预警", summary.get("warning_count", 0), False),
    ]

    sep_style = ParagraphStyle(
        "kpi_sep", fontName=_font("light"), fontSize=6, leading=6, alignment=1,
        textColor=colors.HexColor("#" + S.HAIRLINE),
    )

    def make_card(label, value, is_alert):
        alert_on = bool(is_alert and value)
        value_style = styles["kpi_alert"] if alert_on else styles["kpi_number"]
        accent = S.ALERT if alert_on else S.KPMG_BLUE
        return Table(
            [[Paragraph(_clean_text(str(value)), value_style)],
             [Paragraph("———", sep_style)],
             [Paragraph(_clean_text(label), styles["kpi_label"])]],
            colWidths=[52 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + S.PANEL)),
                ("LINEABOVE", (0, 0), (-1, 0), 2.0 if alert_on else 1.0, colors.HexColor("#" + accent)),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, 0), 14),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 2),
                ("TOPPADDING", (0, 2), (-1, 2), 0),
                ("BOTTOMPADDING", (0, 2), (-1, 2), 13),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]),
        )

    rows = [
        [make_card(*cards[i]) for i in range(3)],
        [make_card(*cards[i]) for i in range(3, 6)],
    ]
    grid = Table(rows, colWidths=[58 * mm] * 3, hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    out = _section_eyebrow("执行摘要 · EXECUTIVE SUMMARY", styles)
    out.append(grid)

    if bilingual:
        def pct(v):
            return f"{(v or 0) * 100:.1f}%"
        bi = (
            f"翻译覆盖率 {pct(summary.get('translation_coverage'))}　|　"
            f"表格覆盖率 {pct(summary.get('table_coverage'))}　|　"
            f"跨币种核对一致 {summary.get('cross_currency_matched', 0)} 项"
        )
        out.append(Spacer(1, 6))
        out.append(Paragraph(_clean_text(bi), styles["muted"]))
    return out


def _build_charts(job: Job, styles, tmp_dir: Path):
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

    from ahcc.report._charts import donut_png, hbar_png

    sev = S.severity_distribution(job.diffs)
    typ = S.type_distribution(job.diffs)
    if not sev and not typ:
        out = _section_eyebrow("分布概览 · DISTRIBUTION", styles)
        out.append(Paragraph("本次核查未识别差异。", styles["muted"]))
        return out

    def _img(png_path, target_w_mm):
        ir = ImageReader(str(png_path))
        iw, ih = ir.getSize()
        w = target_w_mm * mm
        h = w * ih / iw
        return Image(str(png_path), width=w, height=h)

    cells = []
    try:
        if sev:
            p = donut_png(sev, tmp_dir / "sev.png", title="严重度分布")
            if p:
                cells.append(_img(p, 82))
        if typ:
            p = hbar_png(typ, tmp_dir / "typ.png", title="差异类型分布")
            if p:
                cells.append(_img(p, 82))
    except Exception as exc:
        logger.warning(f"PDF 分布图渲染失败：{exc}")
        cells = []

    if not cells:
        return []
    while len(cells) < 2:
        cells.append("")
    grid = Table([cells], colWidths=[87 * mm, 87 * mm], hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    out = _section_eyebrow("分布概览 · DISTRIBUTION", styles)
    out.append(grid)
    return out


def _build_diff_table(job: Job, styles):
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
            Paragraph(_clean_text(S.severity_label_zh(d.severity)), _sev_cell_style(sev_key)),
            Paragraph(_clean_text(S.triage_label_zh(d.triage)), _triage_cell_style(str(getattr(d.triage, "value", d.triage)).lower())),
            Paragraph(_clean_text(d.topic.best()), styles["cell"]),
            Paragraph(_clean_text(_evidence_location(d)), styles["cell"]),
            Paragraph(_clean_text(_diff_explanation_text(d)[:160]), styles["cell"]),
        ])

    col_widths = [13 * mm, 16 * mm, 13 * mm, 16 * mm, 28 * mm, 23 * mm, 65 * mm]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#" + S.INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#" + S.KPMG_BLUE)),
        ("FONTNAME", (0, 0), (-1, 0), _font("bold")),
        ("FONTNAME", (0, 1), (-1, -1), _font("regular")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#" + S.HAIRLINE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + S.STRIPE)]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    for row_idx, sev_key in accent_rows:
        width = S.severity_border_width(sev_key)
        if width:
            color = S.ALERT if S.severity_is_high(sev_key) else S.INK_SOFT
            style.append(("LINEBEFORE", (0, row_idx), (0, row_idx), width, colors.HexColor("#" + color)))
    table.setStyle(TableStyle(style))

    out = _section_eyebrow("差异总览 · 按严重度排序", styles)
    out.append(table)
    if len(sorted_diffs) > _MAX_TABLE_ROWS:
        out.append(Spacer(1, 3))
        out.append(Paragraph(f"注：另有 {len(sorted_diffs) - _MAX_TABLE_ROWS} 条差异详见 Excel 报告「差异清单」。", styles["muted"]))
    return out


def _sev_cell_style(sev_key):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        f"sev_{sev_key}", fontName=_font("regular"), fontSize=8, leading=11, alignment=1,
        textColor=colors.HexColor("#" + S.severity_accent(sev_key)),
    )


def _triage_cell_style(tri_key):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        f"tri_{tri_key}", fontName=_font("regular"), fontSize=8, leading=11, alignment=1,
        textColor=colors.HexColor("#" + S.triage_accent(tri_key)),
    )


def _build_detail_cards(job: Job, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    focus = [d for d in job.diffs if str(getattr(d.severity, "value", d.severity)).lower() in ("high", "critical")]
    focus = sorted(focus, key=lambda d: S.severity_rank(d.severity), reverse=True)
    if not focus:
        return []

    labels = _side_labels(job)
    out = _section_eyebrow("重大 / 严重差异明细 · KEY FINDINGS", styles)
    for d in focus[:_MAX_DETAIL_CARDS]:
        sev_key = str(getattr(d.severity, "value", d.severity)).lower()
        accent_hex = S.severity_accent(sev_key)

        title_row = [
            Paragraph(_clean_text(d.topic.best()), _card_title_style()),
            Paragraph(_clean_text(S.severity_label_zh(d.severity)), _sev_cell_style(sev_key)),
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
            ("FONTNAME", (0, 0), (-1, -1), _font("regular")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + S.PANEL)),
            ("LINEBEFORE", (0, 0), (0, -1), width, colors.HexColor("#" + accent_hex)),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#" + S.HAIRLINE)),
            ("INNERGRID", (0, 1), (-1, -1), 0.3, colors.HexColor("#" + S.HAIRLINE)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        out.append(KeepTogether([card, Spacer(1, 8)]))

    if len(focus) > _MAX_DETAIL_CARDS:
        out.append(Paragraph(f"注：另有 {len(focus) - _MAX_DETAIL_CARDS} 条重大/严重差异详见 Excel 报告。", styles["muted"]))
    return out


def _card_title_style():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle("cardT", fontName=_font("bold"), fontSize=11, leading=14, textColor=colors.HexColor("#" + S.INK))


def _build_coverage(job: Job, styles):
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
    col_widths = [18 * mm, 18 * mm, 36 * mm, 16 * mm, 16 * mm, 70 * mm]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#" + S.INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#" + S.KPMG_BLUE)),
        ("FONTNAME", (0, 0), (-1, 0), _font("bold")),
        ("FONTNAME", (0, 1), (-1, -1), _font("regular")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#" + S.HAIRLINE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + S.STRIPE)]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    out = _section_eyebrow("披露覆盖 / 未匹配项 · COVERAGE", styles)
    out.append(table)
    return out
