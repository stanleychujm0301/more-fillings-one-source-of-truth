"""AHCC Streamlit 主入口（P5 实现）。

启动方式：
    streamlit run ui/app.py

页面结构：
- 主页（本文件）：上传 + 概览仪表盘 + 一键启动
- pages/1_差异报告.py：差异表 + PDF 证据预览
- pages/2_审计师覆盖.py：审计师 review 状态管理
- pages/3_准则知识库.py：浏览/编辑准则差异库（评委加分项）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保 ahcc 包可导入
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from ui.components.i18n import init_lang, lang_toggle_button, t
from ui.components.kpmg_theme import apply_theme, banner, stat_card


def main() -> None:
    apply_theme()
    init_lang()

    # 顶部：语言切换 + Banner
    cols = st.columns([10, 1])
    with cols[1]:
        lang_toggle_button()

    banner(t("app_title"), t("app_subtitle"))

    # 主区：上传 + 启动
    company_name = st.text_input("公司名称 / 简称", max_chars=80)
    check_mode = st.radio(
        "核查类型",
        options=["ah", "h_bilingual"],
        format_func=lambda value: "H 股中英文报告检查" if value == "h_bilingual" else "A+H 股报告检查",
        horizontal=True,
    )

    if check_mode == "h_bilingual":
        upload_a_label = "H股中文报告"
        upload_h_label = "H股英文报告"
    else:
        upload_a_label = t("upload_a")
        upload_h_label = t("upload_h")

    left, right = st.columns(2)
    with left:
        a_file = st.file_uploader(upload_a_label, type=["pdf"], key="a_uploader")
    with right:
        h_file = st.file_uploader(upload_h_label, type=["pdf", "html", "htm"], key="h_uploader")

    start_disabled = not (company_name.strip() and a_file and h_file)
    if st.button(t("btn_start"), disabled=start_disabled, type="primary"):
        _run_check(a_file, h_file, company_name, check_mode)

    # 演示模式：直接展示 demo_cache 的成果
    st.divider()
    _show_demo_dashboard()


def _run_check(a_file, h_file, company_name: str, check_mode: str) -> None:
    """触发后端任务 — 调用 FastAPI POST /api/jobs/。"""
    import httpx

    progress = st.progress(0, text="上传文件...")
    try:
        files = {
            "a_file": (a_file.name, a_file.getvalue(), "application/pdf"),
            "h_file": (h_file.name, h_file.getvalue(), "application/pdf"),
        }
        progress_text = "后端处理中（解析 → 英文翻译核对 → 报告）..." if check_mode == "h_bilingual" else "后端处理中（解析 → 画像 → 检查）..."
        progress.progress(30, text=progress_text)
        resp = httpx.post(
            "http://localhost:8000/api/jobs/",
            data={"company_name": company_name.strip(), "check_mode": check_mode},
            files=files,
            timeout=900,
        )
        resp.raise_for_status()
        job = resp.json()
        progress.progress(100, text="处理完成")
        st.session_state["current_job_id"] = job["job_id"]
        st.session_state["current_company_name"] = job.get("company_name")
        st.session_state["current_check_mode"] = job.get("check_mode", check_mode)
        st.session_state["current_diffs"] = job.get("diffs", [])
        st.session_state["current_profile_a"] = job.get("profile_a", {})
        st.session_state["current_profile_h"] = job.get("profile_h", {})
        st.session_state["current_coverage_items"] = job.get("coverage_items", [])
        st.session_state["current_comparison_summary"] = job.get("comparison_summary", {})
        st.success(f"任务 {job['job_id']} 完成，识别 {len(job.get('diffs', []))} 条差异")
    except Exception as exc:
        progress.progress(100, text="处理失败")
        st.error(f"任务失败: {exc}")


def _show_demo_dashboard() -> None:
    """演示模式的统计仪表盘 — 评委一进首页就能看到核心收益。"""
    st.subheader("效率仪表盘 · Efficiency Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        stat_card(t("stat_total_diffs"), "—")
    with c2:
        stat_card(t("stat_critical"), "—")
    with c3:
        stat_card(t("stat_processing_time"), "— s")
    with c4:
        stat_card(t("stat_time_saved"), "— h")


if __name__ == "__main__":
    main()
