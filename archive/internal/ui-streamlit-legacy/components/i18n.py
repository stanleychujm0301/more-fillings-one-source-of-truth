"""中英双语切换组件 — H 股审计师友好。"""

from __future__ import annotations

import streamlit as st

TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        "app_title": "A+H 股年报数据一致性核查",
        "app_subtitle": "KPMG 智能审计助手 · AI Powered",
        "upload_a": "上传 A 股年报 PDF",
        "upload_h": "上传 H 股年报 PDF / HTML",
        "btn_start": "开始检查",
        "stat_total_diffs": "总差异数",
        "stat_critical": "严重差异",
        "stat_processing_time": "处理用时",
        "stat_time_saved": "本次节省工时",
        "col_id": "差异 ID",
        "col_type": "类别",
        "col_severity": "严重度",
        "col_topic": "主题",
        "col_summary": "差异说明",
        "col_evidence": "证据页码",
        "col_ai": "AI 解读",
        "col_review": "审计师状态",
        "review_pending": "待审",
        "review_reviewed": "已审",
        "review_accepted": "可接受",
        "review_followup": "需追问",
        "btn_download_xlsx": "下载 Excel 报告",
        "btn_download_pdf": "下载 PDF 报告",
        "btn_download_docx": "下载工作底稿附件",
        "lang_toggle": "EN",
    },
    "en": {
        "app_title": "A+H Annual Report Consistency Check",
        "app_subtitle": "KPMG AI Audit Assistant",
        "upload_a": "Upload A-share Annual Report (PDF)",
        "upload_h": "Upload H-share Annual Report (PDF / HTML)",
        "btn_start": "Run Check",
        "stat_total_diffs": "Total Discrepancies",
        "stat_critical": "Critical",
        "stat_processing_time": "Processing Time",
        "stat_time_saved": "Hours Saved",
        "col_id": "Diff ID",
        "col_type": "Type",
        "col_severity": "Severity",
        "col_topic": "Topic",
        "col_summary": "Summary",
        "col_evidence": "Evidence (page)",
        "col_ai": "AI Reasoning",
        "col_review": "Auditor Status",
        "review_pending": "Pending",
        "review_reviewed": "Reviewed",
        "review_accepted": "Accepted",
        "review_followup": "Follow-up",
        "btn_download_xlsx": "Download Excel Report",
        "btn_download_pdf": "Download PDF Report",
        "btn_download_docx": "Download Working Paper",
        "lang_toggle": "中",
    },
}


def init_lang() -> str:
    """在 session_state 中初始化语言（默认中文）。"""
    if "lang" not in st.session_state:
        st.session_state.lang = "zh"
    return st.session_state.lang


def t(key: str) -> str:
    """翻译查询。"""
    lang = st.session_state.get("lang", "zh")
    return TRANSLATIONS.get(lang, TRANSLATIONS["zh"]).get(key, key)


def lang_toggle_button() -> None:
    """放在右上角的中英切换按钮。"""
    if st.button(t("lang_toggle"), key="lang_btn"):
        st.session_state.lang = "en" if st.session_state.lang == "zh" else "zh"
        st.rerun()
