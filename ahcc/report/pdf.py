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
_MAX_DETAIL_CARDS = 1


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
        "hero_title": _ps("section_title", fn_map, fontSize=17, leading=22, color=S.INK),
        "hero_muted": _ps("body_small", fn_map, color=S.INK_SOFT),
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

    import shutil

    tmp_dir = S.make_report_temp_dir(out_path.parent, "ahcc_pdf_chart")
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
    """第一页封面：浅色 one-page executive report 首页。"""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    width, height = doc.pagesize
    fn_regular = _font("regular")
    fn_bold = _font("bold")

    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#" + S.REPORT_SURFACE))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)

    summary = job.comparison_summary or {}
    real_count = summary.get("real_diff_count", sum(1 for d in job.diffs if d.triage == "real"))
    unresolved_count = summary.get("unresolved_diff_count", sum(1 for d in job.diffs if d.triage == "unresolved"))
    conclusion = S.conclusion_label(int(real_count or 0), int(unresolved_count or 0))
    company = _clean_text(job.company_name or "—")
    gen_time = S.format_beijing_datetime(job.finished_at or job.started_at)
    dur = S.format_duration(job.duration_seconds)
    total_count = summary.get("total_diff_count", len(job.diffs))
    expected_count = summary.get("expected_diff_count", sum(1 for d in job.diffs if d.triage == "expected"))
    coverage_count = summary.get("coverage_count", len(job.coverage_items))

    canvas.setFillColor(colors.HexColor("#" + S.COVER_PANEL_TINT))
    canvas.rect(width - 51 * mm, 0, 51 * mm, height, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#" + S.COVER_GRID_LINE))
    canvas.setLineWidth(0.25)
    for x in range(int(width - 46 * mm), int(width - 5 * mm), int(8 * mm)):
        canvas.line(x, 16 * mm, x, height - 16 * mm)
    for y in range(int(24 * mm), int(height - 20 * mm), int(12 * mm)):
        canvas.line(width - 51 * mm, y, width - 5 * mm, y)

    hero_x = 15 * mm
    hero_y = 43 * mm
    hero_w = width - 30 * mm
    hero_h = 230 * mm
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.HexColor("#" + S.REPORT_PANEL_BORDER))
    canvas.setLineWidth(0.55)
    canvas.roundRect(hero_x, hero_y, hero_w, hero_h, 8 * mm, fill=1, stroke=1)

    canvas.setFillColor(colors.HexColor("#" + S.HERO_WASH))
    canvas.roundRect(hero_x + 7 * mm, hero_y + 45 * mm, hero_w - 14 * mm, hero_h - 54 * mm, 7 * mm, fill=1, stroke=0)

    canvas.setFillColor(colors.white)
    canvas.roundRect(hero_x + hero_w - 66 * mm, hero_y + 69 * mm, 49 * mm, 126 * mm, 5 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#" + S.HERO_LINE))
    canvas.setLineWidth(0.55)
    canvas.roundRect(hero_x + hero_w - 66 * mm, hero_y + 69 * mm, 49 * mm, 126 * mm, 5 * mm, fill=0, stroke=1)

    canvas.setStrokeColor(colors.HexColor("#" + S.HERO_LINE))
    canvas.setLineWidth(0.4)
    for offset in (0, 18, 36, 54):
        x = hero_x + 94 * mm + offset * mm
        canvas.line(x, hero_y + 61 * mm, x + 23 * mm, hero_y + 189 * mm)

    canvas.setFont(fn_bold, 10)
    canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.drawString(hero_x + 10 * mm, hero_y + hero_h - 21 * mm, S.WORDMARK_BRAND)
    canvas.setFont(fn_regular, 9)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawString(hero_x + 25 * mm, hero_y + hero_h - 21 * mm, "· " + S.WORDMARK_PRODUCT)

    canvas.setFont(fn_bold, 7.1)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawRightString(hero_x + hero_w - 10 * mm, hero_y + hero_h - 17 * mm, "EXECUTIVE REPORT")
    canvas.setFont(fn_bold, 8.2)
    canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.drawRightString(hero_x + hero_w - 10 * mm, hero_y + hero_h - 23 * mm, "ONE PAGE REVIEW")

    title = _clean_text(_report_title(job))
    lines = _wrap_title(title)
    canvas.setStrokeColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.setLineWidth(2.4)
    canvas.line(hero_x + 10 * mm, hero_y + hero_h - 51 * mm, hero_x + 58 * mm, hero_y + hero_h - 51 * mm)

    y_title = hero_y + hero_h - 82 * mm
    for i, line in enumerate(lines):
        canvas.setFont(fn_bold, 28)
        canvas.setFillColor(colors.HexColor("#" + S.INK))
        canvas.drawString(hero_x + 10 * mm, y_title - i * 12.3 * mm, line)

    canvas.setFont(fn_regular, 8.6)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawString(hero_x + 10 * mm, y_title - len(lines) * 13 * mm - 2 * mm, "Evidence-led disclosure consistency review")

    canvas.setFont(fn_regular, 9.5)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawString(hero_x + 10 * mm, y_title - len(lines) * 13 * mm - 16 * mm, "项目名称")
    canvas.setFont(fn_bold, 13.5)
    canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.drawString(hero_x + 10 * mm, y_title - len(lines) * 13 * mm - 24 * mm, company[:34])

    metric_x = hero_x + hero_w - 59 * mm
    metric_y = hero_y + 127 * mm
    metric_w = 40 * mm
    metric_h = 51 * mm
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.HexColor("#" + S.REPORT_PANEL_BORDER))
    canvas.roundRect(metric_x, metric_y, metric_w, metric_h, 5 * mm, fill=1, stroke=1)
    canvas.setStrokeColor(colors.HexColor("#" + (S.ALERT if real_count else S.KPMG_BLUE)))
    canvas.setLineWidth(1.6)
    canvas.line(metric_x + 5 * mm, metric_y + metric_h - 10 * mm, metric_x + metric_w - 5 * mm, metric_y + metric_h - 10 * mm)
    canvas.setFont(fn_bold, 7.6)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawString(metric_x + 5 * mm, metric_y + metric_h - 17 * mm, "REVIEW PRIORITY")
    canvas.setFont(fn_bold, 12)
    canvas.setFillColor(colors.HexColor("#" + S.INK))
    canvas.drawString(metric_x + 5 * mm, metric_y + metric_h - 27 * mm, conclusion)
    canvas.setFont(fn_bold, 30)
    canvas.setFillColor(colors.HexColor("#" + (S.ALERT if real_count else S.KPMG_BLUE)))
    canvas.drawString(metric_x + 5 * mm, metric_y + 8 * mm, str(real_count))
    canvas.setFont(fn_regular, 7.4)
    canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
    canvas.drawString(metric_x + 21 * mm, metric_y + 13 * mm, "真实差异")

    _draw_cover_signal(
        canvas,
        hero_x + hero_w - 58 * mm,
        hero_y + 84 * mm,
        38 * mm,
        [
            ("Total", total_count, S.KPMG_BLUE),
            ("Real", real_count, S.ALERT if real_count else S.KPMG_BLUE),
            ("Pending", unresolved_count, S.INK_SOFT),
            ("Covered", coverage_count, S.KPMG_MEDIUM_BLUE),
        ],
    )

    path_y = hero_y + 68 * mm
    path = [
        ("01", "真实差异", "锁定需复核事项"),
        ("02", "证据定位", "追溯页码和摘录"),
        ("03", "人工复核", "形成项目结论"),
    ]
    canvas.setFont(fn_bold, 8)
    canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.drawString(hero_x + 10 * mm, path_y + 13 * mm, "REVIEW FLOW")
    for idx, (num, label, note) in enumerate(path):
        x = hero_x + 10 * mm + idx * 43 * mm
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(colors.HexColor("#" + S.REPORT_PANEL_BORDER))
        canvas.roundRect(x, path_y - 1 * mm, 38 * mm, 18 * mm, 3 * mm, fill=1, stroke=1)
        canvas.setFont(fn_bold, 8.5)
        canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
        canvas.drawString(x + 4 * mm, path_y + 9 * mm, num)
        canvas.setFillColor(colors.HexColor("#" + S.INK))
        canvas.drawString(x + 12 * mm, path_y + 9 * mm, label)
        canvas.setFont(fn_regular, 7.2)
        canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
        canvas.drawString(x + 4 * mm, path_y + 4 * mm, note)

    meta_lines = [
        ("核查模式", S.check_mode_label(getattr(job, "check_mode", "ah"))),
        ("核查耗时", dur),
        ("生成时间", gen_time),
        ("任务编号", _clean_text(job.job_id)),
    ]
    card_y = hero_y + 15 * mm
    card_h = 25 * mm
    card_w = (hero_w - 27 * mm) / 4
    for idx, (label, value) in enumerate(meta_lines):
        x = hero_x + 10 * mm + idx * (card_w + 2.3 * mm)
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(colors.HexColor("#" + S.REPORT_PANEL_BORDER))
        canvas.roundRect(x, card_y, card_w, card_h, 3.5 * mm, fill=1, stroke=1)
        canvas.setStrokeColor(colors.HexColor("#" + S.KPMG_BLUE))
        canvas.setLineWidth(0.75)
        canvas.line(x + 4 * mm, card_y + card_h - 6 * mm, x + card_w - 4 * mm, card_y + card_h - 6 * mm)
        canvas.setFont(fn_regular, 8)
        canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
        canvas.drawString(x + 4 * mm, card_y + card_h - 13 * mm, label)
        canvas.setFont(fn_bold, 8.5)
        canvas.setFillColor(colors.HexColor("#" + S.INK))
        if label == "生成时间" and "北京时间" in value:
            time_value = value.replace(" 北京时间", "")
            canvas.drawString(x + 4 * mm, card_y + 8 * mm, time_value[:16])
            canvas.setFont(fn_regular, 7.4)
            canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
            canvas.drawString(x + 4 * mm, card_y + 3 * mm, "北京时间")
        else:
            canvas.drawString(x + 4 * mm, card_y + 6 * mm, value[:18])

    # 底部保密声明 + hairline
    canvas.setStrokeColor(colors.HexColor("#" + S.REPORT_PANEL_BORDER))
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, 22 * mm, width - 20 * mm, 22 * mm)
    canvas.setFont(fn_regular, 8)
    canvas.setFillColor(colors.HexColor("#B0B8C4"))
    canvas.drawString(20 * mm, 16 * mm, "本报告由系统自动生成，仅供内部核查使用；含保密信息，未经授权不得对外披露。")
    canvas.restoreState()


def _draw_cover_signal(canvas, x, y, w, items) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    fn_regular = _font("regular")
    fn_bold = _font("bold")
    max_value = max([int(value or 0) for _, value, _ in items] + [1])
    canvas.setFont(fn_bold, 7.4)
    canvas.setFillColor(colors.HexColor("#" + S.KPMG_BLUE))
    canvas.drawString(x, y + 34 * mm, "SIGNAL STRIP")
    for idx, (label, value, color) in enumerate(items):
        row_y = y + (25 - idx * 7.2) * mm
        canvas.setFont(fn_regular, 6.8)
        canvas.setFillColor(colors.HexColor("#" + S.INK_SOFT))
        canvas.drawString(x, row_y + 1.2 * mm, str(label))
        canvas.setFillColor(colors.HexColor("#" + S.HAIRLINE))
        canvas.roundRect(x + 16 * mm, row_y, w - 24 * mm, 2.2 * mm, 1.1 * mm, fill=1, stroke=0)
        bar_w = max(2 * mm, (w - 24 * mm) * (float(value or 0) / max_value))
        canvas.setFillColor(colors.HexColor("#" + color))
        canvas.roundRect(x + 16 * mm, row_y, bar_w, 2.2 * mm, 1.1 * mm, fill=1, stroke=0)
        canvas.setFont(fn_bold, 7.0)
        canvas.setFillColor(colors.HexColor("#" + S.INK))
        canvas.drawRightString(x + w, row_y + 1.0 * mm, str(value))


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

    gen_time = S.format_beijing_datetime(job.finished_at or job.started_at)
    dur = S.format_duration(job.duration_seconds)
    meta = (
        f"项目名称：{_clean_text(job.company_name or '—')}　|　"
        f"核查模式：{S.check_mode_label(getattr(job, 'check_mode', 'ah'))}　|　"
        f"任务编号：{_clean_text(job.job_id)}　|　核查耗时：{dur}　|　生成时间：{gen_time}"
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
        ("LINEBELOW", (0, 1), (-1, 1), 0.9, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
    ]))
    return [t]


def _section_eyebrow(title: str, styles):
    """Light editorial section rail with a soft rule instead of a heavy blue bar."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    eyebrow = Paragraph(_clean_text(title), styles["section_eyebrow"])
    rail = Table([[eyebrow, ""]], colWidths=[61 * mm, 113 * mm], rowHeights=[7.5 * mm])
    rail.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#" + S.REPORT_SECTION_FILL)),
        ("LINEBEFORE", (0, 0), (0, 0), 1.2, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.65, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
        ("LEFTPADDING", (0, 0), (0, 0), 7),
        ("RIGHTPADDING", (0, 0), (0, 0), 8),
        ("LEFTPADDING", (1, 0), (1, 0), 0),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return [Spacer(1, 12), rail, Spacer(1, 7)]


def _build_dashboard(job: Job, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    summary = job.comparison_summary or {}
    bilingual = getattr(job, "check_mode", "ah") == "h_bilingual"
    real_count = summary.get("real_diff_count", sum(1 for d in job.diffs if d.triage == "real"))
    unresolved_count = summary.get("unresolved_diff_count", sum(1 for d in job.diffs if d.triage == "unresolved"))
    total_count = summary.get("total_diff_count", len(job.diffs))
    expected_count = summary.get("expected_diff_count", sum(1 for d in job.diffs if d.triage == "expected"))
    coverage_count = summary.get("coverage_count", len(job.coverage_items))
    warning_count = summary.get("warning_count", 0)
    conclusion = S.conclusion_label(int(real_count or 0), int(unresolved_count or 0))
    generated_at = S.format_beijing_datetime(job.finished_at or job.started_at)
    duration = S.format_duration(job.duration_seconds)

    pulse_label = ParagraphStyle(
        "pulse_label", fontName=_font("bold"), fontSize=8.2, leading=11,
        textColor=colors.HexColor("#" + S.KPMG_BLUE),
    )
    pulse_title = ParagraphStyle(
        "pulse_title", fontName=_font("bold"), fontSize=20, leading=26,
        textColor=colors.HexColor("#" + S.INK),
    )
    pulse_body = ParagraphStyle(
        "pulse_body", fontName=_font("regular"), fontSize=8.8, leading=13,
        textColor=colors.HexColor("#" + S.INK_SOFT),
    )
    tile_value = ParagraphStyle(
        "tile_value", fontName=_font("light"), fontSize=21, leading=24,
        textColor=colors.HexColor("#" + S.INK), alignment=1,
    )
    tile_value_alert = ParagraphStyle(
        "tile_value_alert", fontName=_font("light"), fontSize=21, leading=24,
        textColor=colors.HexColor("#" + S.ALERT), alignment=1,
    )
    tile_value_compact = ParagraphStyle(
        "tile_value_compact", fontName=_font("light"), fontSize=15, leading=18,
        textColor=colors.HexColor("#" + S.INK), alignment=1,
    )
    tile_label = ParagraphStyle(
        "tile_label", fontName=_font("regular"), fontSize=7.4, leading=10,
        textColor=colors.HexColor("#" + S.INK_SOFT), alignment=1,
    )
    chip_label = ParagraphStyle(
        "chip_label", fontName=_font("bold"), fontSize=7.3, leading=9,
        textColor=colors.HexColor("#" + S.KPMG_BLUE), alignment=1,
    )
    chip_value = ParagraphStyle(
        "chip_value", fontName=_font("regular"), fontSize=7.2, leading=9,
        textColor=colors.HexColor("#" + S.INK_SOFT), alignment=1,
    )

    def metric_tile(label, value, alert=False):
        accent = S.ALERT if alert and value else S.KPMG_BLUE
        value_text = _clean_text(str(value))
        value_style = tile_value_compact if len(value_text) >= 4 else (tile_value_alert if alert and value else tile_value)
        return Table(
            [
                [Paragraph(value_text, value_style)],
                [Paragraph(_clean_text(label), tile_label)],
            ],
            colWidths=[31 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LINEABOVE", (0, 0), (-1, 0), 1.1, colors.HexColor("#" + accent)),
                ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, 0), 5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                ("TOPPADDING", (0, 1), (-1, 1), 1),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
            ]),
        )

    pulse_left = Table(
        [
            [Paragraph("EXECUTIVE PULSE", pulse_label)],
            [Paragraph(_clean_text(conclusion), pulse_title)],
            [Paragraph(
                _clean_text(
                    f"真实差异 {real_count} 项，待判断 {unresolved_count} 项。"
                    "建议先沿证据定位复核真实差异，再处理可解释口径差异。"
                ),
                pulse_body,
            )],
            [Paragraph(_clean_text(f"生成时间 {generated_at}  |  核查耗时 {duration}"), pulse_body)],
        ],
        colWidths=[102 * mm],
    )
    pulse_left.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LINEBEFORE", (0, 0), (0, -1), 1.4, colors.HexColor("#" + (S.ALERT if real_count else S.REPORT_SECTION_RULE))),
        ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))

    pulse_metrics = Table(
        [
            [metric_tile("真实差异", real_count, True), metric_tile("待判断", unresolved_count)],
            [metric_tile("覆盖项", coverage_count), metric_tile("核查耗时", duration)],
        ],
        colWidths=[34 * mm, 34 * mm],
    )
    pulse_metrics.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    pulse_panel = Table(
        [[pulse_left, pulse_metrics]],
        colWidths=[105 * mm, 69 * mm],
        hAlign="LEFT",
    )
    pulse_panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#" + S.HERO_WASH)),
        ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))

    review_path = Table(
        [[
            Paragraph("审阅动线 · REVIEW FLOW", pulse_label),
            Paragraph("01 真实差异<br/><font color='#5A6473'>锁定需重点复核项目</font>", styles["card_value"]),
            Paragraph("02 证据定位<br/><font color='#5A6473'>追溯 A/H 页码与摘录</font>", styles["card_value"]),
            Paragraph("03 人工复核<br/><font color='#5A6473'>形成结论并更新状态</font>", styles["card_value"]),
        ]],
        colWidths=[25 * mm, 49 * mm, 49 * mm, 51 * mm],
        hAlign="LEFT",
    )
    review_path.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
        ("LINEBEFORE", (1, 0), (1, -1), 0.8, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
        ("LINEBEFORE", (2, 0), (2, -1), 0.35, colors.HexColor("#" + S.HAIRLINE)),
        ("LINEBEFORE", (3, 0), (3, -1), 0.35, colors.HexColor("#" + S.HAIRLINE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))

    chips = Table(
        [[
            Paragraph("差异总数", chip_label), Paragraph(_clean_text(str(total_count)), chip_value),
            Paragraph("预期差异", chip_label), Paragraph(_clean_text(str(expected_count)), chip_value),
            Paragraph("提取预警", chip_label), Paragraph(_clean_text(str(warning_count)), chip_value),
        ]],
        colWidths=[25 * mm, 31 * mm, 25 * mm, 31 * mm, 25 * mm, 37 * mm],
        hAlign="LEFT",
    )
    chips.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#" + S.HAIRLINE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    out = _section_eyebrow("执行摘要 · EXECUTIVE SUMMARY", styles)
    out.extend([pulse_panel, Spacer(1, 5), review_path, Spacer(1, 5), chips])

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
    from reportlab.lib import colors
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
                cells.append(_chart_card("严重度画像", _img(p, 72), styles))
        if typ:
            p = hbar_png(typ, tmp_dir / "typ.png", title="差异类型分布")
            if p:
                cells.append(_chart_card("差异类型画像", _img(p, 72), styles))
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
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    out = _section_eyebrow("分布概览 · DISTRIBUTION", styles)
    out.append(grid)
    return out


def _chart_card(title: str, image, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    card = Table(
        [
            [Paragraph(_clean_text(title), styles["card_label"])],
            [image],
        ],
        colWidths=[82 * mm],
    )
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return card


def _build_diff_table(job: Job, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if not job.diffs:
        return []

    sorted_diffs = sorted(job.diffs, key=lambda d: S.severity_rank(d.severity), reverse=True)
    shown = sorted_diffs[:_MAX_TABLE_ROWS]

    header = [Paragraph(h, styles["header"]) for h in ["风险", "主题与说明", "证据定位", "复核口径"]]
    data = [header]
    accent_rows = []  # (row_idx, severity_key)
    for i, d in enumerate(shown, start=1):
        sev_key = str(getattr(d.severity, "value", d.severity)).lower()
        accent_rows.append((i, sev_key))
        risk = (
            f"{_clean_text(str(d.diff_id))}<br/>"
            f"<font color='#{S.severity_accent(sev_key)}'>{_clean_text(S.severity_label_zh(d.severity))}</font><br/>"
            f"<font color='#{S.triage_accent(str(getattr(d.triage, 'value', d.triage)).lower())}'>{_clean_text(S.triage_label_zh(d.triage))}</font>"
        )
        topic = (
            f"<b>{_clean_text(d.topic.best())}</b><br/>"
            f"<font color='#{S.INK_SOFT}'>{_clean_text(S.diff_type_label_zh(d.diff_type))}</font><br/>"
            f"{_clean_text(_diff_explanation_text(d)[:170])}"
        )
        review_hint = ""
        if getattr(d, "diff_explanation", None) and d.diff_explanation.review_hint:
            review_hint = d.diff_explanation.review_hint
        else:
            review_hint = "复核证据定位、口径与差异值后更新审计师状态。"
        data.append([
            Paragraph(risk, styles["cell"]),
            Paragraph(topic, styles["cell"]),
            Paragraph(_clean_text(_evidence_location(d)), styles["cell"]),
            Paragraph(_clean_text(review_hint), styles["cell"]),
        ])

    col_widths = [27 * mm, 72 * mm, 29 * mm, 46 * mm]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#" + S.INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
        ("FONTNAME", (0, 0), (-1, 0), _font("bold")),
        ("FONTNAME", (0, 1), (-1, -1), _font("regular")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#" + S.HAIRLINE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#" + S.STRIPE)]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
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
    out = _section_eyebrow("重大 / 严重差异明细 · 证据链", styles)
    for d in focus[:_MAX_DETAIL_CARDS]:
        sev_key = str(getattr(d.severity, "value", d.severity)).lower()
        accent_hex = S.severity_accent(sev_key)

        header = Table(
            [[
                Paragraph(_clean_text(f"{d.diff_id} · {d.topic.best()}"), _card_title_style()),
                Paragraph(_clean_text(S.severity_label_zh(d.severity)), _sev_cell_style(sev_key)),
            ]],
            colWidths=[134 * mm, 28 * mm],
        )
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBEFORE", (0, 0), (0, 0), S.severity_border_width(sev_key), colors.HexColor("#" + accent_hex)),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#" + S.HAIRLINE)),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))

        issue = Table(
            [[
                Paragraph("问题摘要", styles["card_label"]),
                Paragraph(_clean_text(_diff_issue_text(d)), styles["card_value"]),
            ]],
            colWidths=[25 * mm, 137 * mm],
        )
        issue.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#" + S.HERO_WASH)),
            ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.HAIRLINE)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))

        evidence = Table(
            [
                [
                    Paragraph(f"{labels['A']} 侧证据", styles["card_label"]),
                    Paragraph(f"{labels['H']} 侧证据", styles["card_label"]),
                ],
                [
                    Paragraph(_clean_text(_side_location_text(d, "A", labels["A"])), styles["card_value"]),
                    Paragraph(_clean_text(_side_location_text(d, "H", labels["H"])), styles["card_value"]),
                ],
            ],
            colWidths=[81 * mm, 81 * mm],
        )
        evidence.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + S.HERO_WASH)),
            ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.HAIRLINE)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))

        delta_text = _fmt_num(d.delta) if d.delta is not None else "—"
        citation = S.standard_citation_text(d)
        hint = (d.diff_explanation.review_hint if d.diff_explanation and d.diff_explanation.review_hint else "")
        action_text = hint or "复核 A/H 定位、口径与差异值后更新审计师状态。"
        meta = Table(
            [[
                Paragraph("类型 / 分流", styles["card_label"]),
                Paragraph(_clean_text(f"{S.diff_type_label_zh(d.diff_type)} / {S.triage_label_zh(d.triage)}"), styles["card_value"]),
                Paragraph("差异值", styles["card_label"]),
                Paragraph(_clean_text(delta_text), styles["card_value"]),
            ], [
                Paragraph("复核动作", styles["card_label"]),
                Paragraph(_clean_text(action_text), styles["card_value"]),
                Paragraph("准则引用", styles["card_label"]),
                Paragraph(_clean_text(citation or "—"), styles["card_value"]),
            ]],
            colWidths=[24 * mm, 58 * mm, 24 * mm, 56 * mm],
        )
        meta.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#" + S.HERO_WASH)),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#" + S.HERO_WASH)),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#" + S.HAIRLINE)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))

        card = Table([[header], [issue], [evidence], [meta]], colWidths=[162 * mm])
        card.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _font("regular")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#" + S.REPORT_PANEL_BORDER)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
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
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.HexColor("#" + S.REPORT_SECTION_RULE)),
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
