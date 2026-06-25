"""Excel 报告导出（P3 实现）— openpyxl + KPMG 蓝主题样式。

包含：核查总览（指标卡 + 严重度/类型分布图）、差异清单（配色分级 + 冻结 + 筛选 +
定位与取值 + 准则引用）、真实/预期差异、披露覆盖、提取预警、A/H 画像、证据定位。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from ahcc.report import _style as S
from ahcc.schemas import Job

# 复用 _style 中的 KPMG 配色常量（保留旧名以兼容外部引用）
KPMG_BLUE = S.KPMG_BLUE
KPMG_LIGHT_BLUE = S.KPMG_LIGHT_BLUE
KPMG_PURPLE = S.KPMG_PURPLE
_ILLEGAL_EXCEL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")
_EXCEL_CELL_LIMIT = 32767
_NUMBER_FORMAT = "#,##0.00;(#,##0.00)"  # 会计括号负数，去掉刺眼 [Red]
_BODY_FONT = S.FONT_FAMILY  # 统一使用 Apple/金融风格主字体


def _clean_cell(value: Any) -> Any:
    """Remove PDF-extracted control characters that openpyxl rejects."""
    if not isinstance(value, str):
        return value
    cleaned = _ILLEGAL_EXCEL_CHAR_RE.sub("", value)
    if len(cleaned) > _EXCEL_CELL_LIMIT:
        return cleaned[: _EXCEL_CELL_LIMIT - 3] + "..."
    return cleaned


def _append_row(ws, values: list[Any]) -> None:
    ws.append([_clean_cell(value) for value in values])


def _format_export_value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return f"{value:,.0f}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _side_labels(job: Job | None = None) -> dict[str, str]:
    if job and getattr(job, "check_mode", "ah") == "h_bilingual":
        return {"A": "中文", "H": "英文"}
    return {"A": "A", "H": "H"}


def _diff_explanation_text(diff) -> str:
    explanation = getattr(diff, "diff_explanation", None)
    if not explanation:
        return diff.summary.best()
    parts = [explanation.headline, explanation.issue, explanation.location]
    return "\n".join(part for part in parts if part)


def _side_location_and_value(diff, side: str, labels: dict[str, str] | None = None) -> str:
    side_label = (labels or _side_labels()).get(side, side)
    explanation = getattr(diff, "diff_explanation", None)
    if explanation and explanation.items:
        lines: list[str] = []
        for item in explanation.items:
            page = item.a_page if side == "A" else item.h_page
            value = item.a_value if side == "A" else item.h_value
            snippet = item.a_snippet if side == "A" else item.h_snippet
            page_text = f"{side_label} 第{page}页" if page else f"{side_label} 未定位页码"
            label = item.label or "取值"
            value_text = _format_export_value(value)
            line = f"{page_text} | {label}: {value_text}"
            if snippet:
                line += f" | {snippet}"
            lines.append(line)
        return "\n".join(lines)

    evidence = [
        ev for ev in diff.evidence
        if (ev.side.value if hasattr(ev.side, "value") else ev.side) == side
    ]
    return "\n".join(
        f"{side_label} 第{ev.page}页 | {ev.snippet or ''}" if ev.page else f"{side_label} 未定位页码 | {ev.snippet or ''}"
        for ev in evidence
    )


def _review_hint(diff) -> str:
    explanation = getattr(diff, "diff_explanation", None)
    return explanation.review_hint if explanation and explanation.review_hint else ""


# ============================================================
# 样式工具
# ============================================================
def _visual_len(text: str) -> int:
    """估算显示宽度：CJK 字符按 2 计，取多行中最长一行。"""
    if not text:
        return 0
    longest = 0
    for line in str(text).split("\n"):
        width = sum(2 if ord(ch) > 0x2E80 else 1 for ch in line)
        longest = max(longest, width)
    return longest


def _auto_column_widths(ws, min_width: int = 9, max_width: int = 60, sample_rows: int = 300) -> None:
    from openpyxl.utils import get_column_letter

    for col in range(1, ws.max_column + 1):
        widest = 0
        for row in range(1, min(ws.max_row, sample_rows) + 1):
            value = ws.cell(row=row, column=col).value
            if value is not None:
                widest = max(widest, _visual_len(str(value)))
        ws.column_dimensions[get_column_letter(col)].width = min(max_width, max(min_width, widest + 2))


def _style_header(ws) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    if ws.max_row < 1:
        return
    fill = PatternFill(start_color=S.HEADER_BG, end_color=S.HEADER_BG, fill_type="solid")
    bottom = Border(bottom=Side(style="thin", color=S.HEADER_BOTTOM))
    for cell in ws[1]:
        cell.font = Font(bold=True, color=S.INK, name=_BODY_FONT, size=11)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = bottom
    ws.row_dimensions[1].height = 32


def _apply_body_style(ws) -> None:
    """正文换行 + 顶部对齐 + 近白隔行 + 横向细线（去竖线，编辑式留白）。"""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    align = Alignment(vertical="top", wrap_text=True)
    font = Font(name=_BODY_FONT, size=10, color=S.INK)
    stripe = PatternFill(start_color=S.STRIPE, end_color=S.STRIPE, fill_type="solid")
    hair = Side(style="thin", color=S.HAIRLINE)
    border = Border(bottom=hair)
    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.alignment = align
            cell.font = font
            cell.border = border
            if row % 2 == 0:
                cell.fill = stripe


def _finalize_sheet(ws, widths: dict[str, int] | None = None) -> None:
    """统一收尾：表头样式 + 正文样式 + 冻结首行 + 自动筛选 + 列宽。"""
    if ws.max_row < 1:
        return
    _style_header(ws)
    _apply_body_style(ws)
    ws.freeze_panes = "A2"
    if ws.max_row >= 1 and ws.max_column >= 1:
        ws.auto_filter.ref = ws.dimensions
    if widths:
        headers = [c.value for c in ws[1]]
        from openpyxl.utils import get_column_letter

        for idx, header in enumerate(headers, start=1):
            key = next((k for k in widths if header == k or (k.startswith("*") and str(header).endswith(k[1:]))), None)
            if key:
                ws.column_dimensions[get_column_letter(idx)].width = widths[key]
            else:
                # 未指定列回退到内容自适应
                widest = max(
                    (_visual_len(str(ws.cell(row=r, column=idx).value or "")) for r in range(1, min(ws.max_row, 300) + 1)),
                    default=0,
                )
                ws.column_dimensions[get_column_letter(idx)].width = min(45, max(9, widest + 2))
    else:
        _auto_column_widths(ws)


# ============================================================
# 主入口
# ============================================================
def export_excel(job: Job, out_path: Path) -> None:
    """导出 Excel 差异报告。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "差异清单"

    side_labels = _side_labels(job)
    diff_headers = [
        "差异ID",
        "严重度",
        "分流",
        "差异类型",
        "主题",
        "差异说明",
        f"{side_labels['A']}定位与取值",
        f"{side_labels['H']}定位与取值",
        "A 股值",
        "H 股值",
        "差异",
        "证据页码",
        "AI 解读",
        "准则引用",
        "审阅提示",
        "审计师状态",
    ]
    _write_diff_sheet(ws, job.diffs, diff_headers, side_labels)

    ws_real = wb.create_sheet("真实差异")
    _write_diff_sheet(ws_real, [d for d in job.diffs if d.triage == "real"], diff_headers, side_labels)

    ws_expected = wb.create_sheet("预期差异")
    _write_diff_sheet(ws_expected, [d for d in job.diffs if d.triage == "expected"], diff_headers, side_labels)

    ws_coverage = wb.create_sheet("披露覆盖")
    _write_coverage_sheet(ws_coverage, job.coverage_items)

    ws_warnings = wb.create_sheet("提取预警")
    _write_warning_sheet(ws_warnings, job)

    ws_a = wb.create_sheet("A画像")
    _write_profile_sheet(ws_a, job.profile_a or {})

    ws_h = wb.create_sheet("H画像")
    _write_profile_sheet(ws_h, job.profile_h or {})

    ws_ev = wb.create_sheet("证据定位")
    _write_evidence_sheet(ws_ev, job.diffs)

    # 总览 sheet 放最前；但保持「差异清单」为 active（现有测试依赖 wb.active）
    ws_overview = wb.create_sheet("核查总览", 0)
    _write_overview_sheet(ws_overview, job)
    wb.active = wb.index(ws)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    logger.info(f"Excel 报告已导出：{out_path}")


# ============================================================
# 差异清单
# ============================================================
_DIFF_WIDTHS = {
    "差异ID": 11,
    "严重度": 9,
    "分流": 11,
    "差异类型": 12,
    "主题": 26,
    "差异说明": 48,
    "*定位与取值": 38,
    "A 股值": 16,
    "H 股值": 16,
    "差异": 15,
    "证据页码": 18,
    "AI 解读": 44,
    "准则引用": 32,
    "审阅提示": 32,
    "审计师状态": 12,
}


def _write_diff_sheet(ws, diffs, headers, side_labels: dict[str, str] | None = None) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    _append_row(ws, headers)

    sorted_diffs = sorted(diffs, key=lambda d: S.severity_rank(d.severity), reverse=True)
    for diff in sorted_diffs:
        pages = "; ".join(f"{e.side.value} P.{e.page}" for e in diff.evidence)
        ai_note = diff.standard_reasoning.rationale if diff.standard_reasoning else ""
        _append_row(ws, [
            diff.diff_id,
            S.severity_label_zh(diff.severity),
            S.triage_label_zh(diff.triage),
            S.diff_type_label_zh(diff.diff_type),
            diff.topic.best(),
            _diff_explanation_text(diff),
            _side_location_and_value(diff, "A", side_labels),
            _side_location_and_value(diff, "H", side_labels),
            diff.a_value,
            diff.h_value,
            diff.delta,
            pages,
            ai_note,
            S.standard_citation_text(diff),
            _review_hint(diff),
            diff.review_status.value,
        ])

    _finalize_sheet(ws, _DIFF_WIDTHS)

    # 列定位
    id_col = headers.index("差异ID") + 1
    sev_col = headers.index("严重度") + 1
    triage_col = headers.index("分流") + 1
    value_cols = [headers.index(h) + 1 for h in ("A 股值", "H 股值", "差异") if h in headers]

    from openpyxl.styles import Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import DataBarRule

    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    hair = Side(style="thin", color=S.HAIRLINE)
    for offset, diff in enumerate(sorted_diffs):
        row = offset + 2
        sev_key = str(getattr(diff.severity, "value", diff.severity)).lower()

        # 严重度：文字着色（高级别暗红加粗，其余灰）——不再整列实色块
        scell = ws.cell(row=row, column=sev_col)
        scell.font = Font(
            name=_BODY_FONT, size=10,
            bold=S.severity_is_high(sev_key),
            color=S.severity_accent(sev_key),
        )
        scell.alignment = center

        # 分流：文字着色（real 暗红，其余灰）
        tri_key = str(getattr(diff.triage, "value", diff.triage)).lower()
        tcell = ws.cell(row=row, column=triage_col)
        tcell.font = Font(
            name=_BODY_FONT, size=10,
            bold=S.triage_is_real(tri_key),
            color=S.triage_accent(tri_key),
        )
        tcell.alignment = center

        # 左侧强调条：按严重度粗细/颜色变化
        width = S.severity_border_width(sev_key)
        if width:
            color = S.ALERT if S.severity_is_high(sev_key) else S.INK_SOFT
            idcell = ws.cell(row=row, column=id_col)
            idcell.border = Border(
                left=Side(style="medium" if width >= 2.0 else "thin", color=color),
                bottom=hair,
            )

        for vcol in value_cols:
            vcell = ws.cell(row=row, column=vcol)
            if isinstance(vcell.value, (int, float)):
                vcell.number_format = _NUMBER_FORMAT
            vcell.alignment = Alignment(horizontal="right", vertical="top")

    # 「差异」列数据条：克制灰阶，突出数值量级
    if "差异" in headers and ws.max_row >= 2:
        delta_col_idx = headers.index("差异") + 1
        delta_col = get_column_letter(delta_col_idx)
        ws.conditional_formatting.add(
            f"{delta_col}2:{delta_col}{ws.max_row}",
            DataBarRule(
                start_type="min", end_type="max",
                color=S.INK_SOFT, showValue=True, minLength=None, maxLength=None,
            ),
        )


# ============================================================
# 核查总览
# ============================================================
def _write_overview_sheet(ws, job: Job) -> None:
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    summary = job.comparison_summary or {}
    bilingual = getattr(job, "check_mode", "ah") == "h_bilingual"
    title = "H 股中英文报告一致性核查报告" if bilingual else "A+H 股年报数据一致性核查报告"

    # 列宽：A 标签 / B 数值 / C 说明
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 40
    for col in ("E", "F", "G"):
        ws.column_dimensions[col].width = 16

    # 标题条（合并 A1:C1）
    ws.merge_cells("A1:C1")
    tcell = ws["A1"]
    tcell.value = title
    tcell.font = Font(name=_BODY_FONT, size=16, bold=True, color=S.INK)
    tcell.fill = PatternFill(start_color=S.HEADER_BG, end_color=S.HEADER_BG, fill_type="solid")
    tcell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    tcell.border = Border(bottom=Side(style="thin", color=S.HEADER_BOTTOM))
    ws.row_dimensions[1].height = 36

    def _ts(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    gen_time = _ts(job.finished_at) or _ts(job.started_at)
    dur = f"{job.duration_seconds:.1f} 秒" if job.duration_seconds else "—"
    meta_rows = [
        ("公司名称", job.company_name or "—"),
        ("任务编号", job.job_id),
        ("核查模式", summary.get("mode_label", "H 股中英文检查" if bilingual else "A+H 股报告检查")),
        ("核查耗时", dur),
        ("生成时间", gen_time or "—"),
    ]
    row = 2
    label_font = Font(name=_BODY_FONT, size=10, bold=True, color=S.INK_SOFT)
    value_font = Font(name=_BODY_FONT, size=10, color=S.INK)
    for label, value in meta_rows:
        ws.cell(row=row, column=1, value=label).font = label_font
        ws.cell(row=row, column=2, value=_clean_cell(str(value))).font = value_font
        row += 1

    # —— 关键指标区 ——
    row += 1
    _section_header(ws, row, "关键指标", span=3)
    row += 1
    metrics = [
        ("差异总数", summary.get("total_diff_count", len(job.diffs)), "本次识别的全部差异条数"),
        ("真实差异", summary.get("real_diff_count", sum(1 for d in job.diffs if d.triage == "real")), "需重点关注/追问的差异"),
        ("预期差异", summary.get("expected_diff_count", sum(1 for d in job.diffs if d.triage == "expected")), "CAS↔IFRS 趋同等可解释差异"),
        ("待判断", summary.get("unresolved_diff_count", sum(1 for d in job.diffs if d.triage == "unresolved")), "需人工进一步确认"),
        ("披露覆盖项", summary.get("coverage_count", len(job.coverage_items)), "披露范围匹配/单边项总数"),
        (f"{'中文' if bilingual else 'A'}事实数", summary.get("a_fact_count", 0), "抽取到的指标出现次数"),
        (f"{'英文' if bilingual else 'H'}事实数", summary.get("h_fact_count", 0), "抽取到的指标出现次数"),
        ("提取预警", summary.get("warning_count", 0), f"阻断 {summary.get('blocking_warning_count', 0)} / 辅助 {summary.get('aux_warning_count', 0)}"),
    ]

    panel_fill = PatternFill(start_color=S.DASHBOARD_CARD_BG, end_color=S.DASHBOARD_CARD_BG, fill_type="solid")
    label_font = Font(name=_BODY_FONT, size=9, color=S.INK_SOFT)
    note_font = Font(name=_BODY_FONT, size=9, color=S.INK_SOFT)
    for i, (label, value, note) in enumerate(metrics):
        r = row + i
        lcell = ws.cell(row=r, column=1, value=label)
        lcell.font = label_font
        lcell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        vcell = ws.cell(row=r, column=2, value=value)
        value_color = S.ALERT if label == "真实差异" and value else S.INK
        vcell.font = Font(name=_BODY_FONT, size=14, bold=True, color=value_color)
        vcell.alignment = Alignment(horizontal="center", vertical="center")
        ncell = ws.cell(row=r, column=3, value=_clean_cell(str(note)))
        ncell.font = note_font
        ncell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        for col in range(1, 4):
            ws.cell(row=r, column=col).fill = panel_fill
    row += len(metrics)

    # 双语模式补充指标
    if bilingual:
        row += 1
        _section_header(ws, row, "中英文核查指标", span=3)
        row += 1

        def pct(v):
            return f"{(v or 0) * 100:.1f}%"

        sem_total = summary.get("semantic_total_pairs", 0)
        sem_cell = (
            f"{pct(summary.get('semantic_coverage', 0))}（{summary.get('semantic_reviewed_pairs', 0)}/{sem_total} 对）"
            if sem_total else "未启用"
        )
        bi_rows = [
            ("翻译覆盖率", pct(summary.get("translation_coverage")), "已配对中文段落 / 中文总段落"),
            ("表格覆盖率", pct(summary.get("table_coverage")), "已配对中文表格 / 中文总表格"),
            ("LLM 翻译审查覆盖", sem_cell, "strict 模式下分批审查的段落覆盖比"),
            ("跨币种核对一致", summary.get("cross_currency_matched", 0), "人民币/港币换算后一致项"),
            ("跨币种不一致", summary.get("cross_currency_mismatch", 0), "换算后仍不一致，已降级"),
            ("币种不可识别", summary.get("currency_ambiguous", 0), "一侧币种缺失，需人工复核"),
        ]
        for label, value, note in bi_rows:
            ws.cell(row=row, column=1, value=label).font = Font(name=_BODY_FONT, size=10, bold=True)
            ws.cell(row=row, column=2, value=_clean_cell(str(value))).alignment = Alignment(horizontal="center")
            ws.cell(row=row, column=3, value=_clean_cell(str(note))).font = Font(name=_BODY_FONT, size=9, color=S.TEXT_MUTED)
            row += 1

    # —— 分布图数据区（写到 E/F 列，绑定图表） ——
    sev_dist = S.severity_distribution(job.diffs)
    type_dist = S.type_distribution(job.diffs)

    data_top = 2
    ws.cell(row=data_top, column=5, value="严重度").font = Font(name=_BODY_FONT, bold=True)
    ws.cell(row=data_top, column=6, value="数量").font = Font(name=_BODY_FONT, bold=True)
    r = data_top + 1
    for label, count in (sev_dist or {"（无差异）": 0}).items():
        ws.cell(row=r, column=5, value=label)
        ws.cell(row=r, column=6, value=count)
        r += 1
    sev_last = r - 1

    if sev_dist:
        chart = BarChart()
        chart.type = "col"
        chart.title = "严重度分布"
        chart.height = 9
        chart.width = 14
        chart.legend = None
        data = Reference(ws, min_col=6, min_row=data_top, max_row=sev_last)
        cats = Reference(ws, min_col=5, min_row=data_top + 1, max_row=sev_last)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        _style_mono_chart(chart, sev_last - data_top)
        ws.add_chart(chart, "A" + str(row + 2))

    type_top = sev_last + 3
    ws.cell(row=type_top, column=5, value="差异类型").font = Font(name=_BODY_FONT, bold=True)
    ws.cell(row=type_top, column=6, value="数量").font = Font(name=_BODY_FONT, bold=True)
    r = type_top + 1
    for label, count in (type_dist or {"（无差异）": 0}).items():
        ws.cell(row=r, column=5, value=label)
        ws.cell(row=r, column=6, value=count)
        r += 1
    type_last = r - 1

    if type_dist:
        chart2 = BarChart()
        chart2.type = "bar"
        chart2.title = "差异类型分布"
        chart2.height = 9
        chart2.width = 14
        chart2.legend = None
        data2 = Reference(ws, min_col=6, min_row=type_top, max_row=type_last)
        cats2 = Reference(ws, min_col=5, min_row=type_top + 1, max_row=type_last)
        chart2.add_data(data2, titles_from_data=True)
        chart2.set_categories(cats2)
        _style_mono_chart(chart2, type_last - type_top)
        ws.add_chart(chart2, "A" + str(row + 20))


def _style_mono_chart(chart, n_points: int) -> None:
    """把 BarChart 收敛为海军蓝单色阶：去网格线、去图例、按数据点上深浅蓝。"""
    try:
        from openpyxl.chart.series import DataPoint
        from openpyxl.chart.shapes import GraphicalProperties

        chart.varyColors = False
        chart.legend = None
        chart.gapWidth = 60
        if getattr(chart, "y_axis", None) is not None:
            chart.y_axis.majorGridlines = None
        if not chart.series:
            return
        series = chart.series[0]
        series.graphicalProperties = GraphicalProperties(solidFill=S.MONO_RAMP[0])
        for i in range(max(0, n_points)):
            pt = DataPoint(idx=i)
            pt.graphicalProperties = GraphicalProperties(solidFill=S.mono_color(i))
            series.data_points.append(pt)
    except Exception as exc:  # 图表着色失败不阻断报告
        logger.warning(f"Excel 单色图着色失败：{exc}")


def _section_header(ws, row: int, text: str, span: int = 3) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ws.merge_cells(f"A{row}:{get_column_letter(span)}{row}")
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = Font(name=_BODY_FONT, size=12, bold=True, color=S.INK)
    cell.fill = PatternFill(start_color=S.PANEL, end_color=S.PANEL, fill_type="solid")
    cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    # 浅底 + 墨色字 + 底部细线（去掉高饱和紫色块）
    underline = Side(style="thin", color=S.KPMG_BLUE)
    for col in range(1, span + 1):
        ws.cell(row=row, column=col).border = Border(bottom=underline)
    ws.row_dimensions[row].height = 22


# ============================================================
# 其余 sheet
# ============================================================
def _write_profile_sheet(ws, profile: dict) -> None:
    _append_row(ws, ["范围", "键/主题", "名称", "值/字数", "单位/层级", "页码/范围", "证据/摘要"])
    _append_row(ws, ["summary", "doc_id", profile.get("doc_id", ""), "", "", "", ""])
    _append_row(ws, ["summary", "total_pages", profile.get("total_pages", ""), "", "", "", ""])
    _append_row(ws, ["summary", "metric_occurrences", profile.get("metric_occurrences", ""), "", "", "", ""])
    _append_row(ws, ["summary", "narrative_blocks", profile.get("narrative_blocks", ""), "", "", "", ""])
    audit = profile.get("extraction_audit") or {}
    if audit:
        _append_row(ws, ["audit", "scanned_pages", len(audit.get("scanned_pages") or []), "", "", "", ""])
        _append_row(ws, ["audit", "coverage_ratio", audit.get("coverage_ratio", ""), "", "", "", ""])
        _append_row(ws, ["audit", "blank_pages", len(audit.get("blank_pages") or []), "", "", "", ""])
        _append_row(ws, ["audit", "ocr_pages", len(audit.get("ocr_pages") or []), "", "", "", ""])
        _append_row(ws, ["audit", "table_pages", len(audit.get("table_pages") or []), "", "", "", ""])
        _append_row(ws, ["audit", "warnings", " | ".join(audit.get("warnings") or []), "", "", "", ""])

    for item in profile.get("metrics", []):
        ev = item.get("evidence", {}) or {}
        name = item.get("name", {}) or {}
        _append_row(ws, [
            "metric",
            item.get("canonical_key", ""),
            name.get("zh") or name.get("en") or "",
            item.get("value"),
            item.get("unit") or item.get("currency") or "",
            item.get("page"),
            ev.get("snippet", ""),
        ])

    for item in profile.get("narratives", []):
        _append_row(ws, [
            "narrative",
            item.get("topic_key", ""),
            item.get("topic_label", ""),
            item.get("word_count"),
            item.get("detail_level", ""),
            str(item.get("page_range", "")),
            item.get("summary", ""),
        ])

    _finalize_sheet(ws, {"范围": 10, "键/主题": 22, "名称": 20, "值/字数": 14, "单位/层级": 14, "页码/范围": 14, "证据/摘要": 60})


def _write_coverage_sheet(ws, items) -> None:
    _append_row(ws, ["覆盖ID", "状态", "类别", "主题", "Key", "A页码", "H页码", "匹配置信度", "说明"])
    for item in items:
        _append_row(ws, [
            item.coverage_id,
            item.status,
            item.category,
            item.topic.best(),
            item.canonical_key or "",
            ", ".join(str(p) for p in item.a_pages),
            ", ".join(str(p) for p in item.h_pages),
            item.match_confidence,
            item.note,
        ])
    _finalize_sheet(ws, {"覆盖ID": 12, "状态": 10, "类别": 12, "主题": 26, "Key": 20, "A页码": 12, "H页码": 12, "匹配置信度": 12, "说明": 50})


def _write_warning_sheet(ws, job: Job) -> None:
    _append_row(ws, ["侧", "预警标识", "类别", "严重性", "阻断核心核查", "说明", "总页数", "扫描页数", "覆盖率", "缺失页", "空白页", "OCR页", "表格页"])
    warnings = (job.comparison_summary or {}).get("warnings") or []
    if warnings:
        for item in warnings:
            _append_row(ws, [
                item.get("side", ""),
                item.get("flag", ""),
                item.get("category", ""),
                item.get("severity", ""),
                "是" if item.get("blocking") else "否",
                item.get("message", ""),
                item.get("total_pages", 0),
                item.get("scanned_pages", 0),
                item.get("coverage_ratio", 0),
                item.get("missing_pages", 0),
                item.get("blank_pages", 0),
                item.get("ocr_pages", 0),
                item.get("table_pages", 0),
            ])
    else:
        for side, profile in (("A", job.profile_a or {}), ("H", job.profile_h or {})):
            audit = profile.get("extraction_audit") or {}
            if not audit:
                continue
            _append_row(ws, [
                side, "", "", "", "否", "No extraction warnings.",
                audit.get("total_pages", 0),
                len(audit.get("scanned_pages") or []),
                audit.get("coverage_ratio", 0),
                len(audit.get("missing_pages") or []),
                len(audit.get("blank_pages") or []),
                len(audit.get("ocr_pages") or []),
                len(audit.get("table_pages") or []),
            ])
    _finalize_sheet(ws, {"说明": 50})


def _write_evidence_sheet(ws, diffs) -> None:
    _append_row(ws, ["差异ID", "分流", "差异类型", "侧", "页码", "章节", "坐标(bbox)", "原文片段"])
    for diff in diffs:
        for ev in diff.evidence:
            _append_row(ws, [
                diff.diff_id,
                S.triage_label_zh(diff.triage),
                S.diff_type_label_zh(diff.diff_type),
                ev.side.value,
                ev.page,
                ev.section or "",
                _format_bbox(ev.bbox),
                ev.snippet,
            ])
    _finalize_sheet(ws, {"差异ID": 12, "分流": 11, "差异类型": 12, "侧": 6, "页码": 8, "章节": 22, "坐标(bbox)": 22, "原文片段": 60})


def _format_bbox(bbox) -> str:
    if not bbox:
        return ""
    try:
        return "(" + ", ".join(f"{float(v):.0f}" for v in bbox) + ")"
    except Exception:
        return str(bbox)
