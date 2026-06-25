"""差异报告页 — 表格 + 详情面板 + PDF 证据预览。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from ui.components.i18n import init_lang, t
from ui.components.kpmg_theme import apply_theme, banner, stat_card

apply_theme()
init_lang()
banner(t("col_summary"), "Diff Report")


def _severity_badge(sev: str) -> str:
    css_map = {
        "critical": "sev-critical",
        "high": "sev-high",
        "medium": "sev-medium",
        "low": "sev-low",
        "info": "sev-info",
    }
    return f"""<span class='{css_map.get(sev, "sev-info")}'>{sev.upper()}</span>"""


def _diff_rows(items: list[dict]) -> list[dict]:
    rows = []
    for d in items:
        topic = d.get("topic", {})
        summary = d.get("summary", {})
        evidence_pages = []
        for ev in d.get("evidence", []):
            p = ev.get("page")
            if p:
                evidence_pages.append(f"{ev.get('side', '')}p{p}")
        rows.append({
            "ID": d.get("diff_id", "")[:8],
            "分类": d.get("triage", "real"),
            "严重度": d.get("severity", "info"),
            "类型": d.get("diff_type", ""),
            "主题": topic.get("zh") or topic.get("en") or "",
            "差异说明": summary.get("zh") or summary.get("en") or "",
            "A值": d.get("a_value"),
            "H值": d.get("h_value"),
            "页码": ", ".join(evidence_pages),
        })
    return rows


def _render_diff_table(items: list[dict], empty_text: str) -> None:
    if not items:
        st.info(empty_text)
        return
    st.dataframe(pd.DataFrame(_diff_rows(items)), use_container_width=True, hide_index=True)


def _render_profile(profile: dict, label: str) -> None:
    if not profile:
        st.info(f"{label}画像尚未生成。")
        return
    c1, c2, c3 = st.columns(3)
    with c1:
        stat_card("指标key", str(profile.get("metric_keys", 0)))
    with c2:
        stat_card("事实出现", str(profile.get("metric_occurrences", 0)))
    with c3:
        stat_card("叙述块", str(profile.get("narrative_blocks", 0)))

    st.markdown("#### 数字事实")
    metrics = profile.get("metrics", [])
    metric_rows = [
        {
            "key": m.get("canonical_key"),
            "名称": (m.get("name") or {}).get("zh") or (m.get("name") or {}).get("en"),
            "值": m.get("value"),
            "单位": m.get("unit") or m.get("currency"),
            "页码": m.get("page"),
            "出现次数": m.get("occurrence_count"),
        }
        for m in metrics
    ]
    st.dataframe(pd.DataFrame(metric_rows), use_container_width=True, hide_index=True)

    st.markdown("#### 文字叙述")
    narratives = profile.get("narratives", [])
    narrative_rows = [
        {
            "主题": n.get("topic_label"),
            "topic_key": n.get("topic_key"),
            "页码范围": n.get("page_range"),
            "字数": n.get("word_count"),
            "详略": n.get("detail_level"),
            "摘要": n.get("summary"),
        }
        for n in narratives
    ]
    st.dataframe(pd.DataFrame(narrative_rows), use_container_width=True, hide_index=True)


def _render_evidence(items: list[dict]) -> None:
    rows = []
    for d in items:
        for ev in d.get("evidence", []):
            rows.append({
                "差异ID": d.get("diff_id", "")[:8],
                "分类": d.get("triage", "real"),
                "类型": d.get("diff_type", ""),
                "侧": ev.get("side"),
                "页码": ev.get("page"),
                "章节": ev.get("section", ""),
                "bbox": ev.get("bbox", ""),
                "原文片段": ev.get("snippet", ""),
            })
    if not rows:
        st.info("暂无证据定位。")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _coverage_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        topic = item.get("topic", {})
        a_pages = item.get("a_pages") or []
        h_pages = item.get("h_pages") or []
        rows.append({
            "ID": item.get("coverage_id", "")[:12],
            "状态": item.get("status", ""),
            "类别": item.get("category", ""),
            "主题": topic.get("zh") or topic.get("en") or item.get("canonical_key") or "",
            "Key": item.get("canonical_key") or "",
            "A页码": ", ".join(str(p) for p in a_pages),
            "H页码": ", ".join(str(p) for p in h_pages),
            "置信度": item.get("match_confidence"),
            "说明": item.get("note", ""),
        })
    return rows


def _render_coverage(items: list[dict]) -> None:
    if not items:
        st.info("暂无披露覆盖项。")
        return
    st.dataframe(pd.DataFrame(_coverage_rows(items)), use_container_width=True, hide_index=True)


# ---------- 1. 获取数据 ----------
diffs = st.session_state.get("current_diffs", [])
job_id = st.session_state.get("current_job_id", "")
profile_a = st.session_state.get("current_profile_a", {})
profile_h = st.session_state.get("current_profile_h", {})
coverage_items = st.session_state.get("current_coverage_items", [])

if not diffs and not coverage_items and not profile_a and not profile_h:
    st.warning("暂无核查数据。请在首页上传 A+H 年报并启动检查。")
    st.stop()

# ---------- 2. 统计仪表盘 ----------
critical_count = sum(1 for d in diffs if d.get("severity") == "critical")
high_count = sum(1 for d in diffs if d.get("severity") == "high")
numeric_count = sum(1 for d in diffs if d.get("diff_type") == "numeric")
standard_count = sum(1 for d in diffs if d.get("diff_type") == "standard")
disclosure_count = sum(1 for d in diffs if d.get("diff_type") == "disclosure")
chart_count = sum(1 for d in diffs if d.get("diff_type") == "chart")

c1, c2, c3, c4 = st.columns(4)
with c1:
    stat_card(t("stat_total_diffs"), str(len(diffs)))
with c2:
    stat_card(t("stat_critical"), str(critical_count + high_count))
with c3:
    stat_card("数值差异", str(numeric_count))
with c4:
    stat_card("准则/披露差异", str(standard_count + disclosure_count))

st.divider()

comparison_summary = st.session_state.get("current_comparison_summary", {})

if comparison_summary:
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        stat_card("真实差异", str(comparison_summary.get("real_diff_count", 0)))
    with s2:
        stat_card("预期差异", str(comparison_summary.get("expected_diff_count", 0)))
    with s3:
        stat_card("待判断", str(comparison_summary.get("unresolved_diff_count", 0)))
    with s4:
        stat_card("披露覆盖", str(comparison_summary.get("coverage_count", len(coverage_items))))

tabs = st.tabs(["A 画像", "H 画像", "真实差异", "预期差异", "披露覆盖", "证据定位"])
with tabs[0]:
    _render_profile(profile_a, "A股")
with tabs[1]:
    _render_profile(profile_h, "H股")
with tabs[2]:
    _render_diff_table([d for d in diffs if d.get("triage", "real") == "real"], "暂无真实差异。")
with tabs[3]:
    _render_diff_table([d for d in diffs if d.get("triage") == "expected"], "暂无预期差异。")
with tabs[4]:
    _render_coverage(coverage_items)
with tabs[5]:
    _render_evidence(diffs)

if job_id:
    c1, c2 = st.columns(2)
    with c1:
        st.link_button(t("btn_download_xlsx"), f"http://localhost:8000/api/jobs/{job_id}/report.xlsx")
    with c2:
        st.link_button(t("btn_download_pdf"), f"http://localhost:8000/api/jobs/{job_id}/report.pdf")

st.stop()

# ---------- 3. 筛选器 ----------
left, right = st.columns([1, 3])
with left:
    type_filter = st.multiselect(
        "差异类型",
        ["numeric", "standard", "disclosure", "chart"],
        default=["numeric", "standard", "disclosure", "chart"],
    )
    severity_filter = st.multiselect(
        "严重度",
        ["critical", "high", "medium", "low", "info"],
        default=["critical", "high", "medium", "low"],
    )

# 过滤
filtered = [
    d for d in diffs
    if d.get("diff_type") in type_filter and d.get("severity") in severity_filter
]

# ---------- 4. 差异表格 ----------
with right:
    if not filtered:
        st.info("当前筛选条件下无差异。")
    else:
        df_data = []
        for d in filtered:
            topic = d.get("topic", {})
            summary = d.get("summary", {})
            evidence_pages = []
            for ev in d.get("evidence", []):
                p = ev.get("page")
                if p:
                    side = "A" if ev.get("side") == "A" else "H"
                    evidence_pages.append(f"{side}p{p}")

            df_data.append({
                "ID": d.get("diff_id", "")[:8],
                "严重度": _severity_badge(d.get("severity", "info")),
                "类型": d.get("diff_type", "").upper(),
                "主题": topic.get("zh", "") or topic.get("en", ""),
                "差异说明": summary.get("zh", "") or summary.get("en", "")[:60],
                "A值": d.get("a_value"),
                "H值": d.get("h_value"),
                "页码": ", ".join(evidence_pages) if evidence_pages else "—",
            })

        df = pd.DataFrame(df_data)
        st.markdown(df.to_html(escape=False, index=False), unsafe_allow_html=True)

        # ---------- 5. 详情面板（选中某行） ----------
        st.divider()
        st.subheader("差异详情")

        selected_id = st.selectbox(
            "选择差异查看详情",
            options=[d.get("diff_id", "")[:8] for d in filtered],
            format_func=lambda x: x,
        )

        selected_diff = next(
            (d for d in filtered if d.get("diff_id", "").startswith(selected_id)),
            None,
        )

        if selected_diff:
            d = selected_diff
            c_left, c_right = st.columns([2, 1])

            with c_left:
                st.markdown(f"**差异 ID:** {d.get('diff_id', '')}")
                st.markdown(f"**类型:** `{d.get('diff_type', '')}`")
                st.markdown(f"**严重度:** `{d.get('severity', '')}`")

                topic = d.get("topic", {})
                st.markdown(f"**主题:** {topic.get('zh', '')} / {topic.get('en', '')}")

                summary = d.get("summary", {})
                st.markdown(f"**说明:** {summary.get('zh', '')}")

                a_val = d.get("a_value")
                h_val = d.get("h_value")
                if a_val is not None and h_val is not None:
                    delta = d.get("delta")
                    st.markdown(f"**A 股值:** {a_val:,.2f}")
                    st.markdown(f"**H 股值:** {h_val:,.2f}")
                    if delta:
                        st.markdown(f"**差异:** {delta:,.2f}")

                # AI 解读（准则差异）
                sr = d.get("standard_reasoning")
                if sr:
                    st.markdown("---")
                    st.markdown("### AI 准则解读")
                    expected = sr.get("expected")
                    badge = "✅ 符合预期" if expected else "⚠️ 不符合预期"
                    st.markdown(f"**{badge}** (置信度: {sr.get('confidence', 0):.0%})")
                    st.markdown(f"**理由:** {sr.get('rationale', '')}")
                    citations = sr.get("citations", [])
                    if citations:
                        st.markdown("**引用准则:**")
                        for c in citations:
                            st.markdown(
                                f"- {c.get('standard_code', '')} {c.get('clause', '')}: "
                                f"{c.get('title', '')}"
                            )

                # 图表交叉核对
                chart_cross = d.get("chart_cross")
                if chart_cross:
                    st.markdown("---")
                    st.markdown("### 图表三方核对")
                    st.markdown(f"- 图表值: {chart_cross.get('chart_value')}")
                    st.markdown(f"- 表格值: {chart_cross.get('table_value')}")
                    st.markdown(f"- 文本值: {chart_cross.get('text_value')}")
                    st.markdown(f"- 不一致数: {chart_cross.get('inconsistency_count', 0)}")

            with c_right:
                st.markdown("### 证据链")
                for ev in d.get("evidence", []):
                    side = "A 股" if ev.get("side") == "A" else "H 股"
                    page = ev.get("page", "?")
                    snippet = ev.get("snippet", "")[:200]
                    section = ev.get("section", "")
                    st.markdown(
                        f"""<div style='background:#f5f8fc;padding:10px;border-radius:4px;margin-bottom:8px;'>
                        <b>{side} P{page}</b> {f"({section})" if section else ""}<br/>
                        <span style='color:#5f6b7a;font-size:0.85rem;'>{snippet}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )

                # 审计师状态
                st.markdown("### 审计师状态")
                review_status = d.get("review_status", "pending")
                status_labels = {
                    "pending": t("review_pending"),
                    "reviewed": t("review_reviewed"),
                    "accepted": t("review_accepted"),
                    "followup": t("review_followup"),
                }
                st.markdown(f"当前: **{status_labels.get(review_status, review_status)}**")

# ---------- 6. 下载按钮 ----------
st.divider()
if job_id:
    c1, c2 = st.columns(2)
    with c1:
        st.link_button(
            t("btn_download_xlsx"),
            f"http://localhost:8000/api/jobs/{job_id}/report.xlsx",
        )
    with c2:
        st.link_button(
            t("btn_download_pdf"),
            f"http://localhost:8000/api/jobs/{job_id}/report.pdf",
        )
