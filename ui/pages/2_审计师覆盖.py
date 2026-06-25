"""审计师覆盖页 — 给差异打"已审/可接受/需追问"标签 + 备注。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from ui.components.i18n import init_lang, t
from ui.components.kpmg_theme import apply_theme, banner, stat_card

apply_theme()
init_lang()
banner(t("col_review"), "Auditor Review")


def _severity_label(sev: str) -> str:
    return {
        "critical": "🔴 CRITICAL",
        "high": "🟠 HIGH",
        "medium": "🟡 MEDIUM",
        "low": "🔵 LOW",
        "info": "⚪ INFO",
    }.get(sev, sev)


# ---------- 1. 获取数据 ----------
diffs = st.session_state.get("current_diffs", [])
job_id = st.session_state.get("current_job_id", "")

if not diffs:
    st.warning("暂无差异数据。请在首页上传 A+H 年报并启动检查。")
    st.stop()

# ---------- 2. 审核进度统计 ----------
total = len(diffs)
pending = sum(1 for d in diffs if d.get("review_status") == "pending")
reviewed = sum(1 for d in diffs if d.get("review_status") == "reviewed")
accepted = sum(1 for d in diffs if d.get("review_status") == "accepted")
followup = sum(1 for d in diffs if d.get("review_status") == "followup")

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    stat_card(t("stat_total_diffs"), str(total))
with c2:
    stat_card(t("review_pending"), str(pending))
with c3:
    stat_card(t("review_reviewed"), str(reviewed))
with c4:
    stat_card(t("review_accepted"), str(accepted))
with c5:
    stat_card(t("review_followup"), str(followup))

progress_pct = (total - pending) / total if total > 0 else 0
st.progress(progress_pct, text=f"审核进度: {(total - pending)}/{total}")
st.divider()

# ---------- 3. 待审差异列表 ----------
status_options = ["pending", "reviewed", "accepted", "followup"]
status_labels = {
    "pending": t("review_pending"),
    "reviewed": t("review_reviewed"),
    "accepted": t("review_accepted"),
    "followup": t("review_followup"),
}

# 筛选器
filter_status = st.multiselect(
    "筛选状态",
    options=status_options,
    default=["pending"],
    format_func=lambda x: status_labels.get(x, x),
)

# 只显示选中的状态
filtered = [d for d in diffs if d.get("review_status", "pending") in filter_status]

if not filtered:
    st.info("当前筛选条件下无差异。")
else:
    st.markdown(f"显示 {len(filtered)} 条差异")

    # 批量操作区
    with st.expander("批量设置状态"):
        batch_status = st.selectbox(
            "将当前筛选结果全部设为",
            options=status_options,
            format_func=lambda x: status_labels.get(x, x),
            key="batch_status",
        )
        if st.button("应用批量设置"):
            for d in diffs:
                if d in filtered:
                    d["review_status"] = batch_status
            st.success(f"已批量设置 {len(filtered)} 条差异为 {status_labels.get(batch_status)}")
            st.rerun()

    st.divider()

    # 逐行审核
    for idx, d in enumerate(filtered):
        diff_id = d.get("diff_id", "")
        topic = d.get("topic", {})
        summary = d.get("summary", {})
        severity = d.get("severity", "info")

        with st.container():
            cols = st.columns([2, 1, 2, 2])

            with cols[0]:
                st.markdown(f"**{topic.get('zh', '')}**")
                st.caption(f"{summary.get('zh', '')[:80]}...")
                st.markdown(
                    f"<span style='font-size:0.75rem;color:#5f6b7a;'>"
                    f"ID: {diff_id[:8]} | {_severity_label(severity)} | {d.get('diff_type', '')}"
                    f"</span>",
                    unsafe_allow_html=True,
                )

            with cols[1]:
                a_val = d.get("a_value")
                h_val = d.get("h_value")
                if a_val is not None and h_val is not None:
                    st.markdown(f"A: `{a_val:,.2f}`")
                    st.markdown(f"H: `{h_val:,.2f}`")
                else:
                    st.markdown("—")

            with cols[2]:
                current_status = d.get("review_status", "pending")
                new_status = st.selectbox(
                    "状态",
                    options=status_options,
                    index=status_options.index(current_status) if current_status in status_options else 0,
                    format_func=lambda x: status_labels.get(x, x),
                    key=f"status_{diff_id}",
                    label_visibility="collapsed",
                )
                if new_status != current_status:
                    d["review_status"] = new_status

            with cols[3]:
                current_note = d.get("review_note", "")
                new_note = st.text_input(
                    "备注",
                    value=current_note,
                    key=f"note_{diff_id}",
                    placeholder="审计师备注...",
                    label_visibility="collapsed",
                )
                if new_note != current_note:
                    d["review_note"] = new_note

        st.divider()

    # ---------- 4. 提交审核结果 ----------
    st.subheader("提交审核结果")
    reviewer_name = st.text_input("审核人姓名", value="审计师", key="reviewer_name")

    if st.button("提交到后端", type="primary"):
        import httpx

        try:
            payload = {
                "job_id": job_id,
                "reviewed_by": reviewer_name,
                "diffs": [
                    {
                        "diff_id": d.get("diff_id"),
                        "review_status": d.get("review_status", "pending"),
                        "review_note": d.get("review_note", ""),
                    }
                    for d in diffs
                ],
            }
            resp = httpx.post(
                "http://localhost:8000/api/reviews/",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            st.success("审核结果已提交")
        except Exception as exc:
            st.error(f"提交失败: {exc}")
            st.info("提示：后端 review API 可能尚未完全接入，审核状态已保存在当前会话中。")
