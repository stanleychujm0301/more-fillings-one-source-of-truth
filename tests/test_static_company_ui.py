from __future__ import annotations

import re
from pathlib import Path


def test_static_upload_ui_collects_and_posts_company_name() -> None:
    html = Path("ui/static/index.html").read_text(encoding="utf-8")

    assert 'id="companyName"' in html
    assert 'placeholder="例如：招商证券"' not in html
    assert "getCompanyName()" in html
    assert "function validateUploadInputs" in html
    assert "field-missing" in html
    assert ".shake" in html
    assert "@keyframes fieldShake" in html
    assert 'aria-disabled="true"' in html
    assert 'id="startBtn" onclick="startCheck()" disabled' not in html
    assert 'name="checkMode"' in html
    assert 'value="h_bilingual"' in html
    assert "function getCheckMode()" in html
    assert 'name="bilingualLevel"' in html
    assert 'value="fast"' in html
    assert 'value="strict"' in html
    assert "function getBilingualLevel()" in html
    assert "function applyCheckMode()" in html
    assert "function sideLabelsForJob(job)" in html
    assert "formData.append('company_name', companyName)" in html
    assert "formData.append('check_mode', getCheckMode())" in html
    assert "formData.append('bilingual_level', getBilingualLevel())" in html
    assert "H股中文报告" in html
    assert "H股英文报告" in html
    assert "history-company" in html
    history_company_line_css = re.search(r"\.hi-company-line\s*\{(?P<body>.*?)\}", html, re.S)
    assert history_company_line_css
    assert "display: flex;" in history_company_line_css.group("body")
    assert "align-items: center;" in history_company_line_css.group("body")
    assert '<div class="hi-company-line">' in html
    assert '<span class="hi-badge ${cls}"' in html
    assert "audit-company" in html
    audit_company_css = re.search(r"\.audit-company\s*\{(?P<body>.*?)\}", html, re.S)
    audit_company_strong_css = re.search(r"\.audit-company strong\s*\{(?P<body>.*?)\}", html, re.S)
    assert audit_company_css
    assert audit_company_strong_css
    assert "font-size: 0.95rem;" in audit_company_css.group("body")
    assert "font-size: inherit;" in audit_company_strong_css.group("body")
    assert "function renderAuditStrip(summary, job)" in html
    assert "renderAuditStrip(summary, job)" in html
    assert '<div class="metric-card company">' not in html
    assert "diff.diff_explanation" in html
    assert "explanationForDiff" in html
    assert "差异说明" in html
    assert "旧结果未记录结构化解释" in html
    assert "renderExplanationItems" in html


def test_streamlit_upload_posts_company_name() -> None:
    app = Path("ui/app.py").read_text(encoding="utf-8")

    assert "company_name = st.text_input" in app
    assert "check_mode = st.radio" in app
    assert '"check_mode": check_mode' in app
    assert '"h_bilingual"' in app


def test_h_bilingual_result_view_does_not_relabel_profile_tabs() -> None:
    html = Path("ui/static/index.html").read_text(encoding="utf-8")

    assert "function isBilingualJob(job)" in html
    assert "function configureResultMode(job)" in html
    assert "英文翻译问题" in html
    assert "章节排版" in html
    assert "profileATab: 'H中文画像'" not in html
    assert "profileHTab: 'H英文画像'" not in html
    assert "defaultResultViewForJob(job)" in html
    assert "return isBilingualJob(job) ? 'real-diff' : 'profile-a'" in html
