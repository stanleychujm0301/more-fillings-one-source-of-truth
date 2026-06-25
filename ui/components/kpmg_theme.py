"""KPMG 视觉规范常量 + Streamlit 主题注入。

配色严格遵循 KPMG 内部品牌指南，与 f:\\内控专项测评\\optimize_gt_ht_ppt.py 保持一致：
- 主蓝 #00338D
- 浅蓝 #0091DA
- 紫 #470A68
- 青 #00A3A1
- 灰 #5F6B7A
- 软白 #F5F8FC

字体优先 Microsoft YaHei（中文）/ Segoe UI（英文）。
"""

from __future__ import annotations

import streamlit as st


KPMG_COLORS = {
    "blue": "#00338D",
    "light_blue": "#0091DA",
    "purple": "#470A68",
    "teal": "#00A3A1",
    "ink": "#172033",
    "muted": "#5F6B7A",
    "line": "#DBE3EF",
    "soft": "#F5F8FC",
    "success": "#1F8A50",
    "warning": "#E2A12F",
    "danger": "#D43F3F",
}


CUSTOM_CSS = f"""
<style>
  /* KPMG 主蓝顶部 banner */
  .kpmg-banner {{
    background: {KPMG_COLORS['blue']};
    color: #fff;
    padding: 24px 28px 20px;
    margin: -1rem -1rem 1.2rem;
    border-radius: 0 0 6px 6px;
  }}
  .kpmg-banner h1 {{
    color: #fff !important;
    margin: 0 0 6px;
    font-size: 1.6rem;
    letter-spacing: 0;
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
  }}
  .kpmg-banner p {{
    color: rgba(255,255,255,0.86);
    margin: 0;
    font-size: 0.92rem;
  }}

  /* KPMG 配色按钮 */
  .stButton>button {{
    background: {KPMG_COLORS['blue']};
    color: #fff;
    border: none;
    border-radius: 4px;
    font-weight: 600;
  }}
  .stButton>button:hover {{
    background: {KPMG_COLORS['light_blue']};
    color: #fff;
  }}

  /* 大数字仪表盘卡片 */
  .stat-card {{
    background: {KPMG_COLORS['soft']};
    border-left: 4px solid {KPMG_COLORS['blue']};
    padding: 18px 20px;
    border-radius: 4px;
    margin-bottom: 12px;
  }}
  .stat-card .label {{
    color: {KPMG_COLORS['muted']};
    font-size: 0.85rem;
    margin-bottom: 4px;
  }}
  .stat-card .value {{
    color: {KPMG_COLORS['blue']};
    font-size: 2rem;
    font-weight: 700;
  }}

  /* 差异行严重度色块 */
  .sev-critical {{ background: #fde7e7; color: #a71010; padding: 2px 8px; border-radius: 3px; }}
  .sev-high     {{ background: #fff0d6; color: #a35400; padding: 2px 8px; border-radius: 3px; }}
  .sev-medium   {{ background: #fdf4dc; color: #7a5a00; padding: 2px 8px; border-radius: 3px; }}
  .sev-low      {{ background: #e3f0ff; color: #00338D; padding: 2px 8px; border-radius: 3px; }}
  .sev-info     {{ background: #eaeef3; color: #3a4458; padding: 2px 8px; border-radius: 3px; }}

  /* 顶部隐藏 Streamlit 自带的 deploy 按钮 */
  .stDeployButton {{ display: none; }}
</style>
"""


def apply_theme() -> None:
    """在每个页面顶部调用以注入 KPMG 主题。"""
    st.set_page_config(
        page_title="AHCC — A+H 年报一致性核查",
        page_icon="K",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def banner(title: str, subtitle: str) -> None:
    """KPMG 蓝色顶部 banner。"""
    st.markdown(
        f"""<div class="kpmg-banner">
            <h1>{title}</h1>
            <p>{subtitle}</p>
        </div>""",
        unsafe_allow_html=True,
    )


def stat_card(label: str, value: str) -> None:
    """大数字仪表盘卡片。"""
    st.markdown(
        f"""<div class="stat-card">
            <div class="label">{label}</div>
            <div class="value">{value}</div>
        </div>""",
        unsafe_allow_html=True,
    )
