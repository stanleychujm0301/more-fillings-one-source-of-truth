"""准则知识库浏览页 — 评委加分项：让评委直接看到 15 条 CAS↔IFRS 差异库。

P5 Day 5 实现：
- 左侧：15 条准则差异列表
- 右侧：选中后 Markdown 渲染
- 顶部："YAML 规则现场新增"小工具，30 秒演示扩展性
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from ui.components.i18n import init_lang, t
from ui.components.kpmg_theme import apply_theme, banner

apply_theme()
init_lang()
banner("准则差异知识库 · Standards KB", "CAS ↔ IFRS / HKFRS Differences")

KB_DIR = ROOT / "kb" / "standards"
md_files = sorted(p for p in KB_DIR.glob("[0-9]*.md"))

if not md_files:
    st.warning("准则库为空，请先在 kb/standards/ 填充")
else:
    selected = st.selectbox(
        "选择准则差异主题",
        options=md_files,
        format_func=lambda p: p.stem,
    )
    if selected:
        st.markdown(selected.read_text(encoding="utf-8"))
