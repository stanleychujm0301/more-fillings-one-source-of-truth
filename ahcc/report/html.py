"""HTML 报告导出 — 单文件自包含，视觉参考 PDF（KPMG 风格 + 分布图），内容对齐 Excel（全量差异/
证据/披露覆盖，不设行数上限）。

不引入模板引擎：项目里 pdf.py（reportlab flowables）与 excel.py（openpyxl 单元格写入）都是过程式
拼装，这里保持一致——用 f-string 拼接 HTML 字符串 + 标准库 html.escape() 转义。图表复用
ahcc/report/_charts.py 生成的 PNG，base64 内嵌成 <img>，保证单文件可离线打开、三种报告格式风格一致。

差异对照表头按 diff_scope 分流（cross_report / a_internal / h_internal），避免 A/H 内部一致性差异
（如 rule_id=text_overlay_tamper / visual_text_layer_mismatch，比较的是同一份报告内部"可见值"与
"底层原值"）被误标成跨报告差异——这套判断规则与 cockpit 前端 EvidenceDialog 的修复保持一致。
"""

from __future__ import annotations

import base64
import html as html_lib
import shutil
from pathlib import Path

from loguru import logger

from ahcc.report import _style as S
from ahcc.report._charts import donut_png, hbar_png
from ahcc.schemas import Diff, DiffScope, Job

_DETAIL_CARD_SEVERITIES = ("critical", "high")
_APPENDIX_METRIC_PREVIEW = 50


def export_html(job: Job, out_path: Path) -> None:
    """导出单文件 HTML 报告。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = job.comparison_summary or {}
    side_labels = _side_labels(job, summary)
    tmp_dir = S.make_report_temp_dir(out_path.parent, "ahcc_html_chart")
    try:
        body = "".join(
            [
                _build_header(job, summary),
                _build_dashboard(summary, job),
                _build_charts(job.diffs, tmp_dir),
                _build_diff_table(job.diffs),
                _build_detail_cards(job.diffs, side_labels),
                _build_coverage(job),
                _build_appendix(job, summary),
                _build_footer(job),
            ]
        )
        html_doc = (
            "<!DOCTYPE html>\n"
            f'<html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_esc(_report_title(job))}</title>"
            f"<style>{_css()}</style></head>"
            f'<body><div class="page">{body}</div></body></html>'
        )
        out_path.write_text(html_doc, encoding="utf-8")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info(f"HTML 报告已导出：{out_path}")


# ============================================================
# 小工具
# ============================================================

def _esc(value: object) -> str:
    if value is None:
        return ""
    return html_lib.escape(str(value))


def _norm_enum(value: object) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metric(summary: dict, key: str) -> str:
    value = summary.get(key)
    if value is None:
        return "0"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_num(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _report_title(job: Job) -> str:
    if getattr(job, "check_mode", "ah") == "h_bilingual":
        return "H 股中英文报告一致性核查报告"
    return "A+H 股年报数据一致性核查报告"


def _side_labels(job: Job, summary: dict) -> dict[str, str]:
    raw = summary.get("side_labels")
    if isinstance(raw, dict) and raw.get("A") and raw.get("H"):
        return {"A": str(raw["A"]), "H": str(raw["H"])}
    if getattr(job, "check_mode", "ah") == "h_bilingual":
        return {"A": "H中文", "H": "H英文"}
    return {"A": "A 股", "H": "H 股"}


def _diff_scope(diff: Diff) -> str:
    """归一化差异来源：cross_report / a_internal / h_internal。

    与 ui-new/src/App.tsx 的 normalizedDiffScope() 保持同一套判断规则，确保 cockpit
    弹层与 HTML 报告对同一条差异给出一致的表头文案。
    """
    scope = getattr(diff, "diff_scope", DiffScope.CROSS_REPORT)
    scope_value = scope.value if isinstance(scope, DiffScope) else str(scope or DiffScope.CROSS_REPORT.value)
    if scope_value in (DiffScope.A_INTERNAL.value, DiffScope.H_INTERNAL.value):
        return scope_value
    if _norm_enum(getattr(diff, "diff_type", "")) == "internal":
        sides = {getattr(ev.side, "value", ev.side) for ev in getattr(diff, "evidence", [])}
        if sides == {"A"}:
            return DiffScope.A_INTERNAL.value
        if sides == {"H"}:
            return DiffScope.H_INTERNAL.value
    return DiffScope.CROSS_REPORT.value


def _diff_issue_text(diff: Diff) -> str:
    explanation = getattr(diff, "diff_explanation", None)
    if explanation and (explanation.issue or explanation.headline):
        return explanation.issue or explanation.headline
    return diff.summary.best()


def _evidence_location(diff: Diff) -> str:
    parts = [f"{getattr(e.side, 'value', e.side)} P.{e.page}" for e in diff.evidence if getattr(e, "page", None)]
    return " / ".join(parts) if parts else "—"


def _compare_labels(scope: str, side_labels: dict[str, str]) -> tuple[str, str, str, str]:
    """返回 (左表头, 右表头, 左卡片side-class, 右卡片side-class)。

    a_internal/h_internal 差异比较的是同一份报告内部的"可见值/底层原值"（见
    ahcc/check/text_overlay_tamper.py、ahcc/check/key_metric_tamper.py 对 a_value/h_value
    的复用），因此两张卡片统一同一侧配色，不再用蓝/浅蓝暗示两份不同报告。
    """
    if scope == DiffScope.A_INTERNAL.value:
        label = side_labels.get("A", "A 股")
        return f"{label} · 可见值", f"{label} · 底层原值", "a-side", "a-side"
    if scope == DiffScope.H_INTERNAL.value:
        label = side_labels.get("H", "H 股")
        return f"{label} · 可见值", f"{label} · 底层原值", "h-side", "h-side"
    return side_labels.get("A", "A 股"), side_labels.get("H", "H 股"), "a-side", "h-side"


def _section_eyebrow(label: str) -> str:
    return f'<div class="section-eyebrow">{_esc(label)}</div>'


# ============================================================
# 章节构建
# ============================================================

def _build_header(job: Job, summary: dict) -> str:
    title = _report_title(job)
    company = job.company_name or "项目名称待确认"
    mode = S.check_mode_label(job.check_mode)
    generated = S.format_beijing_datetime(job.finished_at or job.started_at)
    duration = S.format_duration(job.duration_seconds)
    return f"""
    <header class="hero">
      <p class="eyebrow">{_esc(S.WORDMARK)} · EXECUTIVE REPORT</p>
      <h1>{_esc(title)}</h1>
      <p class="hero-sub">{_esc(company)} · {_esc(mode)}</p>
      <div class="hero-meta">
        <span>任务编号 {_esc(job.job_id)}</span>
        <span>生成时间 {_esc(generated)}</span>
        <span>核查耗时 {_esc(duration)}</span>
      </div>
    </header>
    """


def _build_dashboard(summary: dict, job: Job) -> str:
    tamper_total = _int(summary.get("text_overlay_tamper_count")) + _int(summary.get("visual_text_layer_mismatch_count"))
    tiles = [
        (
            "画像事实",
            f"{_metric(summary, 'a_fact_count')} / {_metric(summary, 'h_fact_count')}",
            f"全量指标 {_metric(summary, 'a_metric_keys')} / {_metric(summary, 'h_metric_keys')}",
            False,
        ),
        (
            "差异",
            f"{_metric(summary, 'real_diff_count')} / {_metric(summary, 'expected_diff_count')}",
            "真实 / 预期",
            bool(_int(summary.get("real_diff_count"))),
        ),
        (
            "待人工复核",
            _metric(summary, "unresolved_diff_count"),
            "未决差异 · 需人工判定",
            bool(_int(summary.get("unresolved_diff_count"))),
        ),
        (
            "提取预警",
            f"{_metric(summary, 'blocking_warning_count')} / {_metric(summary, 'aux_warning_count')}",
            f"核心 / 辅助 · 总计 {_metric(summary, 'warning_count')}",
            bool(_int(summary.get("blocking_warning_count"))),
        ),
        (
            "耗时",
            S.format_duration(job.duration_seconds),
            f"总差异 {_metric(summary, 'total_diff_count')}",
            False,
        ),
        (
            "疑似篡改识别",
            f"{_metric(summary, 'text_overlay_tamper_count')} / {_metric(summary, 'visual_text_layer_mismatch_count')}",
            f"叠加检测 / 视觉复核 · 关键指标精确差异 {_metric(summary, 'key_metric_exact_diff_count')}",
            bool(tamper_total),
        ),
    ]
    cards = "".join(
        f"""<div class="kpi-card{' critical' if is_alert else ''}">
              <p class="kpi-label">{_esc(label)}</p>
              <p class="kpi-value">{_esc(value)}</p>
              <p class="kpi-note">{_esc(note)}</p>
            </div>"""
        for label, value, note, is_alert in tiles
    )
    return _section_eyebrow("核查仪表盘 · DASHBOARD") + f'<div class="kpi-grid">{cards}</div>'


def _build_charts(diffs: list[Diff], tmp_dir: Path) -> str:
    sev = S.severity_distribution(diffs)
    typ = S.type_distribution(diffs)
    if not sev and not typ:
        return _section_eyebrow("分布概览 · DISTRIBUTION") + '<p class="muted">本次核查未识别差异。</p>'
    cards = []
    try:
        if sev:
            path = donut_png(sev, tmp_dir / "sev.png", title="严重度分布")
            if path:
                cards.append(_chart_card("严重度画像", path))
        if typ:
            path = hbar_png(typ, tmp_dir / "typ.png", title="差异类型分布")
            if path:
                cards.append(_chart_card("差异类型画像", path))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"HTML 分布图渲染失败：{exc}")
        cards = []
    if not cards:
        return ""
    return _section_eyebrow("分布概览 · DISTRIBUTION") + f'<div class="chart-grid">{"".join(cards)}</div>'


def _chart_card(title: str, png_path: Path) -> str:
    b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    return (
        f'<div class="chart-card"><p class="card-label">{_esc(title)}</p>'
        f'<img src="data:image/png;base64,{b64}" alt="{_esc(title)}"/></div>'
    )


def _build_diff_table(diffs: list[Diff]) -> str:
    if not diffs:
        return _section_eyebrow("差异总览 · ALL DIFFERENCES") + '<p class="muted">本次核查未识别差异。</p>'
    sorted_diffs = sorted(diffs, key=lambda d: S.severity_rank(d.severity), reverse=True)
    header = "<tr><th>差异ID</th><th>严重度</th><th>分流</th><th>类型</th><th>主题与说明</th><th>证据定位</th></tr>"
    rows = []
    for diff in sorted_diffs:
        sev_key = _norm_enum(diff.severity)
        tri_key = _norm_enum(diff.triage)
        border_width = S.severity_border_width(sev_key)
        border_color = S.ALERT if S.severity_is_high(sev_key) else S.INK_SOFT
        id_style = f'style="border-left:3px solid #{border_color};padding-left:8px"' if border_width else ""
        rows.append(
            f"""<tr>
              <td {id_style}><code>{_esc(diff.diff_id)}</code></td>
              <td style="color:#{S.severity_accent(sev_key)};font-weight:{'700' if S.severity_is_high(sev_key) else '500'}">{_esc(S.severity_label_zh(sev_key))}</td>
              <td style="color:#{S.triage_accent(tri_key)};font-weight:{'700' if S.triage_is_real(tri_key) else '500'}">{_esc(S.triage_label_zh(tri_key))}</td>
              <td>{_esc(S.diff_type_label_zh(diff.diff_type))}</td>
              <td><strong>{_esc(diff.topic.best())}</strong><br/><span class="muted">{_esc(_diff_issue_text(diff))}</span></td>
              <td class="muted">{_esc(_evidence_location(diff))}</td>
            </tr>"""
        )
    return (
        _section_eyebrow(f"差异总览 · ALL DIFFERENCES（共 {len(diffs)} 条）")
        + f'<table class="diff-table">{header}{"".join(rows)}</table>'
    )


def _build_detail_cards(diffs: list[Diff], side_labels: dict[str, str]) -> str:
    detail_diffs = [d for d in diffs if _norm_enum(d.severity) in _DETAIL_CARD_SEVERITIES]
    if not detail_diffs:
        return ""
    detail_diffs = sorted(detail_diffs, key=lambda d: S.severity_rank(d.severity), reverse=True)
    cards = "".join(_detail_card(diff, side_labels) for diff in detail_diffs)
    return (
        _section_eyebrow(f"重点差异详情 · HIGH & CRITICAL（共 {len(detail_diffs)} 条）")
        + f'<div class="detail-cards">{cards}</div>'
    )


def _detail_card(diff: Diff, side_labels: dict[str, str]) -> str:
    scope = _diff_scope(diff)
    explanation = diff.diff_explanation
    left_label, right_label, left_class, right_class = _compare_labels(scope, side_labels)
    evidence_html = "".join(_evidence_item(ev) for ev in diff.evidence) or '<p class="muted">暂无证据片段</p>'
    citation_text = S.standard_citation_text(diff)
    reasoning_html = ""
    if diff.standard_reasoning:
        reasoning_html = f"""
        <div class="insight">
          <span class="insight-label">准则推理</span>
          <strong>{'符合预期差异' if diff.standard_reasoning.expected else '不符合预期差异'}</strong>
          <p>{_esc(diff.standard_reasoning.rationale)}</p>
          {f'<pre class="citation">{_esc(citation_text)}</pre>' if citation_text else ''}
        </div>
        """
    review_hint = explanation.review_hint if explanation and explanation.review_hint else ""
    headline = (explanation.headline if explanation else None) or diff.topic.best()
    return f"""
    <article class="detail-card">
      <header>
        <span class="chip severity-{_norm_enum(diff.severity)}">{_esc(S.severity_label_zh(diff.severity))}</span>
        <span class="chip triage-{_norm_enum(diff.triage)}">{_esc(S.triage_label_zh(diff.triage))}</span>
        <h3>{_esc(headline)}</h3>
        <p class="muted">{_esc(diff.diff_id)} · {_esc(_evidence_location(diff))}</p>
      </header>
      <p>{_esc(_diff_issue_text(diff))}</p>
      {f'<p class="hint">审阅提示：{_esc(review_hint)}</p>' if review_hint else ''}
      <div class="compare-grid">
        <div class="compare-card {left_class}"><span>{_esc(left_label)}</span><strong>{_fmt_num(diff.a_value)}</strong></div>
        <div class="compare-card {right_class}"><span>{_esc(right_label)}</span><strong>{_fmt_num(diff.h_value)}</strong></div>
      </div>
      <div class="evidence-chain">{evidence_html}</div>
      {reasoning_html}
    </article>
    """


def _evidence_item(ev) -> str:
    side_value = getattr(ev.side, "value", ev.side)
    side_class = "h-side" if side_value == "H" else "a-side"
    return f"""
    <div class="evidence-item {side_class}">
      <div class="evidence-top"><span>{_esc(side_value)}</span><strong>第 {ev.page or '-'} 页</strong></div>
      <small>{_esc(ev.section or '章节待确认')}</small>
      <p>{_esc(ev.snippet or '—')}</p>
    </div>
    """


def _build_coverage(job: Job) -> str:
    items = job.coverage_items or []
    if not items:
        return ""
    status_labels = {"a_only": "仅 A 披露", "h_only": "仅 H 披露", "matched": "双边匹配"}
    header = "<tr><th>状态</th><th>类别</th><th>主题</th><th>A 页码</th><th>H 页码</th><th>说明</th></tr>"
    rows = []
    for item in items:
        a_pages = ", ".join(str(p) for p in item.a_pages) or "—"
        h_pages = ", ".join(str(p) for p in item.h_pages) or "—"
        rows.append(
            f"""<tr>
              <td>{_esc(status_labels.get(item.status, item.status))}</td>
              <td>{_esc(item.category)}</td>
              <td>{_esc(item.topic.best())}</td>
              <td>{_esc(a_pages)}</td>
              <td>{_esc(h_pages)}</td>
              <td class="muted">{_esc(item.note or '')}</td>
            </tr>"""
        )
    return (
        _section_eyebrow(f"披露覆盖 · COVERAGE（共 {len(items)} 条）")
        + f'<table class="coverage-table">{header}{"".join(rows)}</table>'
    )


def _build_appendix(job: Job, summary: dict) -> str:
    sections = []

    warnings = summary.get("warnings") or []
    if warnings:
        rows = "".join(
            f"""<tr>
              <td>{_esc(w.get('side') or '—')}</td>
              <td>{_esc(w.get('flag') or '—')}</td>
              <td>{_esc(w.get('category') or '—')}</td>
              <td>{_esc(w.get('severity') or '—')}</td>
              <td class="muted">{_esc(w.get('message') or '')}</td>
            </tr>"""
            for w in warnings
        )
        sections.append(
            f"""<details class="appendix-block">
              <summary>提取预警（共 {len(warnings)} 条）</summary>
              <table class="appendix-table">
                <tr><th>侧</th><th>标识</th><th>类别</th><th>严重性</th><th>说明</th></tr>
                {rows}
              </table>
            </details>"""
        )

    for label, profile in (("A 股画像", job.profile_a), ("H 股画像", job.profile_h)):
        if not profile:
            continue
        metrics = (profile.get("metrics") or [])[:_APPENDIX_METRIC_PREVIEW]
        metric_rows = "".join(
            f"""<tr>
              <td>{_esc((m.get('name') or {}).get('zh') or m.get('canonical_key') or '')}</td>
              <td>{_esc(m.get('value_text') or _fmt_num(m.get('value')))}</td>
              <td>{_esc(m.get('unit') or '')}</td>
              <td>{_esc(m.get('page') or '')}</td>
            </tr>"""
            for m in metrics
        )
        sections.append(
            f"""<details class="appendix-block">
              <summary>{_esc(label)}（指标 {profile.get('metric_keys', 0)} 项 · 叙述 {profile.get('narrative_blocks', 0)} 段 · 预览前 {len(metrics)} 项）</summary>
              <table class="appendix-table">
                <tr><th>名称</th><th>取值</th><th>单位</th><th>页码</th></tr>
                {metric_rows}
              </table>
            </details>"""
        )

    if not sections:
        return ""
    return _section_eyebrow("附录 · APPENDIX") + "".join(sections)


def _build_footer(job: Job) -> str:
    return f'<footer class="footer">{_esc(S.WORDMARK)} · 保密 · Confidential · Job {_esc(job.job_id)}</footer>'


# ============================================================
# 样式
# ============================================================

def _css() -> str:
    return f"""
    :root {{
      --ink: #{S.INK}; --ink-soft: #{S.INK_SOFT}; --hairline: #{S.HAIRLINE};
      --stripe: #{S.STRIPE}; --panel: #{S.PANEL}; --alert: #{S.ALERT};
      --kpmg-blue: #{S.KPMG_BLUE}; --kpmg-light-blue: #{S.KPMG_LIGHT_BLUE};
      --report-surface: #{S.REPORT_SURFACE}; --report-panel: #{S.REPORT_PANEL};
      --report-panel-soft: #{S.REPORT_PANEL_SOFT}; --report-panel-border: #{S.REPORT_PANEL_BORDER};
      --footer-text: #{S.FOOTER_TEXT};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; background: var(--report-surface); color: var(--ink);
      font-family: 'Microsoft YaHei', 'Noto Sans CJK SC', 'SimHei', -apple-system, sans-serif;
      font-size: 14px; line-height: 1.6;
    }}
    .page {{ max-width: 980px; margin: 0 auto; padding: 32px 24px 64px; }}
    .hero {{
      background: var(--report-panel); border: 1px solid var(--report-panel-border);
      border-radius: 18px; padding: 32px; margin-bottom: 24px;
    }}
    .hero .eyebrow {{ color: var(--kpmg-blue); font-weight: 700; font-size: 12px; letter-spacing: .04em; margin: 0 0 8px; }}
    .hero h1 {{ font-size: 26px; font-weight: 300; margin: 0 0 8px; }}
    .hero-sub {{ color: var(--ink-soft); margin: 0 0 16px; font-size: 15px; }}
    .hero-meta {{ display: flex; gap: 18px; flex-wrap: wrap; color: var(--footer-text); font-size: 12px; }}
    .section-eyebrow {{
      color: var(--kpmg-blue); font-weight: 700; font-size: 12px; letter-spacing: .04em;
      border-bottom: 1px solid var(--hairline); padding-bottom: 8px; margin: 32px 0 16px;
    }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .kpi-card {{
      background: var(--report-panel); border: 1px solid var(--report-panel-border);
      border-radius: 14px; padding: 16px;
    }}
    .kpi-card .kpi-label {{ color: var(--footer-text); font-size: 11px; margin: 0 0 6px; }}
    .kpi-card .kpi-value {{ font-size: 24px; font-weight: 300; margin: 0 0 4px; }}
    .kpi-card.critical .kpi-value {{ color: var(--alert); }}
    .kpi-card .kpi-note {{ color: var(--ink-soft); font-size: 11.5px; margin: 0; }}
    .chart-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }}
    .chart-card {{
      background: var(--report-panel); border: 1px solid var(--report-panel-border);
      border-radius: 14px; padding: 14px;
    }}
    .chart-card img {{ width: 100%; height: auto; display: block; }}
    .chart-card .card-label {{ color: var(--ink-soft); font-size: 12px; font-weight: 700; margin: 0 0 8px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
    table th {{
      text-align: left; color: var(--ink); font-weight: 700; font-size: 11.5px;
      border-bottom: 1px solid var(--hairline); padding: 8px 10px; background: var(--panel);
    }}
    table td {{ border-bottom: 1px solid var(--hairline); padding: 8px 10px; vertical-align: top; }}
    table tr:nth-child(even) td {{ background: var(--stripe); }}
    .diff-table code {{ font-family: 'SFMono-Regular', Consolas, monospace; font-size: 11px; color: var(--ink-soft); }}
    .muted {{ color: var(--ink-soft); font-size: 11.5px; }}
    .detail-cards {{ display: grid; gap: 16px; }}
    .detail-card {{
      background: var(--report-panel); border: 1px solid var(--report-panel-border);
      border-radius: 14px; padding: 18px;
    }}
    .detail-card header h3 {{ margin: 8px 0 2px; font-size: 16px; }}
    .chip {{
      display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 11px;
      font-weight: 700; margin-right: 6px; background: var(--panel); color: var(--ink-soft);
    }}
    .chip.severity-critical, .chip.severity-high, .chip.triage-real {{ background: rgba(156,42,42,0.1); color: var(--alert); }}
    .compare-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 14px 0; }}
    .compare-card {{
      border-top: 3px solid var(--kpmg-blue); background: var(--report-panel-soft);
      border-radius: 10px; padding: 12px 14px;
    }}
    .compare-card.h-side {{ border-top-color: var(--kpmg-light-blue); }}
    .compare-card span {{ display: block; color: var(--ink-soft); font-size: 11px; font-weight: 700; margin-bottom: 6px; }}
    .compare-card strong {{ font-size: 20px; }}
    .evidence-chain {{ display: grid; gap: 8px; margin-top: 10px; }}
    .evidence-item {{
      border-left: 3px solid var(--kpmg-blue); background: var(--report-panel-soft);
      border-radius: 8px; padding: 10px 12px;
    }}
    .evidence-item.h-side {{ border-left-color: var(--kpmg-light-blue); }}
    .evidence-top {{ display: flex; justify-content: space-between; color: var(--kpmg-blue); font-size: 11.5px; font-weight: 700; }}
    .evidence-item p {{ margin: 6px 0 0; font-size: 12.5px; color: var(--ink); }}
    .insight {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--hairline); }}
    .insight-label {{ color: var(--kpmg-blue); font-size: 11.5px; font-weight: 700; }}
    .citation {{ white-space: pre-wrap; font-family: inherit; font-size: 12px; color: var(--ink-soft); }}
    .hint {{ color: var(--ink-soft); font-size: 12.5px; }}
    .appendix-block summary {{ cursor: pointer; font-weight: 700; color: var(--kpmg-blue); padding: 10px 0; }}
    .appendix-table {{ margin-bottom: 18px; }}
    .footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--hairline); color: var(--footer-text); font-size: 11px; text-align: center; }}
    @media (max-width: 720px) {{
      .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
      .chart-grid, .compare-grid {{ grid-template-columns: 1fr; }}
    }}
    """
