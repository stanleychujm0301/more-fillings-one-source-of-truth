from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_TSX = ROOT / "ui-new" / "src" / "App.tsx"
APP_CSS = ROOT / "ui-new" / "src" / "App.css"
VITE_CONFIG = ROOT / "ui-new" / "vite.config.ts"
API_MAIN = ROOT / "ahcc" / "api" / "main.py"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
RENDER_YAML = ROOT / "render.yaml"
START_COMPETITION = ROOT / "scripts" / "start_competition.ps1"
README = ROOT / "README.md"


def test_ui_new_exposes_hash_routes_and_user_mode_api_hooks():
    source = APP_TSX.read_text(encoding="utf-8")

    for route in ("#/cockpit", "#/history", "#/jobs/", "#/profile"):
        assert route in source

    for endpoint in (
        "/api/session/current",
        "/api/jobs/history?scope=",
        "/api/users/current",
        "/api/users/current/avatar",
    ):
        assert endpoint in source


def test_ui_new_header_keeps_kpmg_logo_and_one_source_of_truth_tagline():
    source = APP_TSX.read_text(encoding="utf-8")

    assert "kpmg-logo" in source
    assert "Chu, Stanley (SH/FS3)" in source
    assert "多重披露，一次核对" in source
    assert "MORE FILLINGS, ONE SOURCE OF TRUTH" in source


def test_ui_new_is_chinese_first_for_navigation_pages_and_statuses():
    source = APP_TSX.read_text(encoding="utf-8")

    for label in (
        "核查工作台",
        "项目历史",
        "个人资料",
        "核查详情",
        "API 已连接",
        "正在连接",
        "请上传 A 股报告 PDF",
        "核查任务已生成",
        "暂无项目历史",
        "暂无差异",
    ):
        assert label in source

    for old_primary_label in (">Cockpit<", ">History<", ">Profile<"):
        assert old_primary_label not in source


def test_ui_new_upload_history_and_profile_fields_are_chinese_first():
    source = APP_TSX.read_text(encoding="utf-8")

    for label in (
        "项目名称",
        "核查模式",
        "A 股报告",
        "H 股报告",
        "H 股中文报告",
        "H 股英文报告",
        "快速核查",
        "严格核查",
        "开始核查",
        "项目组",
        "我的",
        "核查任务",
        "核查模式",
        "提交人",
        "真实差异",
        "检查时间",
        "核查耗时",
        "当前用户",
        "姓名",
        "所属部门",
        "职位角色",
        "保存资料",
        "头像",
        "上传头像",
    ):
        assert label in source


def test_ui_new_uses_project_name_copy_while_preserving_api_field():
    source = APP_TSX.read_text(encoding="utf-8")

    assert "项目名称" in source
    assert "请输入项目名称" in source
    assert "项目名称待确认" in source
    assert "公司名称" not in source
    assert "请输入公司名称" not in source
    assert "公司/项目待确认" not in source
    assert "company_name" in source


def test_ui_new_history_uses_engagement_copy_and_expanded_metadata():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert "Engagement history" in source
    assert "Project history" not in source

    for label in ("核查模式", "检查时间", "核查耗时"):
        assert label in source

    for token in (
        "timeZone: 'Asia/Shanghai'",
        "modeLabel(item.check_mode)",
        "formatDuration(item.duration_seconds)",
        "job-row-mode",
        "modeShortLabel(item.check_mode)",
    ):
        assert token in source

    for snippet in (
        "grid-template-columns: minmax(206px, 0.55fr) minmax(168px, 0.78fr) minmax(136px, 0.62fr) minmax(62px, 0.26fr) minmax(54px, 0.18fr) minmax(108px, 0.34fr) minmax(86px, 0.27fr)",
        "column-gap: 12px",
        ".history-head {\n  padding: 0 16px 11px",
        "font-size: 14px",
        "font-weight: 880",
        ".job-row-mode",
        ".history-row .status",
        "width: fit-content",
        "min-width: 56px",
    ):
        assert snippet in css

    assert "grid-template-columns: minmax(248px, 1.04fr) minmax(140px, 0.54fr) minmax(118px, 0.44fr) minmax(58px, 0.2fr) minmax(46px, 0.14fr) minmax(94px, 0.24fr) minmax(72px, 0.18fr)" not in css
    assert "grid-template-columns: minmax(210px, 1.18fr) minmax(108px, 0.72fr) minmax(116px, 0.76fr) 92px 78px 116px 86px" not in css


def test_ui_new_user_strip_uses_role_title_and_history_scope_starts_with_mine():
    source = APP_TSX.read_text(encoding="utf-8")

    assert "session?.project_group.name || 'SH/FS3'} · 项目组共享历史" not in source
    assert "session?.user.role_title || 'Senior manager'" in source

    mine_button = "onClick={() => setScope('mine')}>我的</button>"
    project_button = "onClick={() => setScope('project')}>项目组</button>"
    assert mine_button in source
    assert project_button in source
    assert source.index(mine_button) < source.index(project_button)


def test_ui_new_page_topbar_removes_secondary_route_tags():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert "pageSecondaryLabel(route)" not in source
    assert "function pageSecondaryLabel" not in source
    assert 'className="status-stack"' not in source
    assert "A/H · H 中英" not in source
    assert "项目组 / 我的" not in source
    assert "if (route.page === 'profile') return 'Chu, Stanley'" not in source
    assert "page-context-bar" in source
    assert "page-context-label" in source
    assert "pageTitle(route)" not in source
    assert "function pageTitle" not in source
    assert "<h1>{pageTitle(route)}</h1>" not in source
    assert ".page-context-bar" in css
    assert ".page-context-label" in css
    assert ".page-context-bar:not(.has-ticker)" in css
    assert ".page-context-bar:not(.has-ticker) {\n  padding: 0;\n  border-color: transparent;\n  background: transparent;\n  box-shadow: none;" in css
    assert ".app-toolbar,\n.page-context-bar,\n.job-report-action-link" not in css
    assert ".topbar h1" not in css
    assert "font-size: clamp(34px, 5vw, 58px)" not in css


def test_ui_new_cockpit_uses_integrated_command_surface():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "command-surface",
        "command-hero-copy",
        "command-main",
        "command-history",
        "披露一致性核查",
        "项目组最近核查",
    ):
        assert token in source

    for selector in (
        ".command-surface",
        ".command-hero-copy",
        ".command-main",
        ".command-history",
        "--glass-bg",
        "--hairline",
    ):
        assert selector in css

    assert "最近任务" not in source


def test_ui_new_cockpit_uses_shared_page_track_for_toolbar_and_surface():
    css = APP_CSS.read_text(encoding="utf-8")

    shared_width = "width: min(var(--page-max-width), calc(100% - var(--page-gutter) * 2));"

    assert "--page-max-width: 1440px;" in css
    assert "--page-gutter: clamp(14px, 2.2vw, 28px);" in css
    assert css.count(shared_width) >= 2
    assert ".app-toolbar {\n  grid-template-columns" in css
    assert ".workspace {\n  width: min(var(--page-max-width), calc(100% - var(--page-gutter) * 2));\n  margin-inline: auto;\n  padding: 26px 0 42px" in css
    assert ".topbar {\n  display: flex;\n  align-items: center;\n  justify-content: flex-start;\n  gap: 0;\n  width: 100%;\n  margin-inline: 0;\n  margin-bottom: 10px;\n  padding: 0;\n}" in css
    assert ".command-surface {\n  width: 100%;\n  max-width: none;\n  margin-inline: 0;" in css
    assert ".shell {\n    --page-gutter: 9px;\n  }" in css
    assert "width: min(1440px, calc(100% - 28px))" not in css
    assert "width: calc(100% - 18px)" not in css


def test_ui_new_competition_polish_layer_unifies_full_app_visual_system():
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "/* Competition polish layer */",
        "--surface-border: rgba(18, 38, 63, 0.095);",
        "--surface-bg: rgba(255, 255, 255, 0.82);",
        "--surface-bg-strong: rgba(255, 255, 255, 0.94);",
        "--surface-shadow: 0 18px 52px rgba(16, 34, 71, 0.095);",
        "--focus-ring: 0 0 0 4px rgba(0, 163, 224, 0.18);",
        "--motion-fast: 160ms cubic-bezier(0.2, 0, 0, 1);",
        "--status-success-bg: rgba(223, 246, 229, 0.9);",
    ):
        assert token in css

    for snippet in (
        ":where(.primary, .ghost, .segmented button, .job-report-action-link, .command-history-link, .depth-option, .file-card):focus-visible",
        ".panel,\n.command-surface,\n.profile-card,\n.bilingual-page-review,\n.review-shell,\n.dashboard-metric",
        ".status {\n  min-width: 56px;\n  max-width: 72px;",
        ".audit-project-title {\n  font-size: clamp(28px, 3.4vw, 42px);",
        ".history-row,\n.job-row,\n.diff-command-row,\n.bilingual-page-row",
        ".review-shell {\n  width: min(1420px, calc(100% - 32px));",
        ".review-grid {\n  grid-template-columns: minmax(280px, 0.78fr) minmax(420px, 1.2fr) minmax(280px, 0.82fr);",
        "@media (max-width: 720px) {\n  .competition-polish-mobile-sentinel",
        ".diff-command-row,\n  .history-row {\n    min-width: 0;",
    ):
        assert snippet in css

    assert "font-size: clamp(30px, 4.2vw, 52px)" not in css


def test_ui_new_profile_labels_and_depth_control_use_business_copy():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for label in ("姓名", "所属部门", "职位角色", "核查深度"):
        assert label in source

    for old_label in ("显示姓名", "团队线", "角色标题"):
        assert old_label not in source

    for token in (
        "bilingualLevel",
        "fast",
        "strict",
        "depth-control",
        "depth-option",
        "快速核查",
        "严格核查",
    ):
        assert token in source

    assert "<select" not in source

    for selector in (
        ".depth-control",
        ".depth-option",
        ".depth-option.selected",
        ".depth-option strong",
        ".depth-option small",
    ):
        assert selector in css


def test_ui_new_profile_actions_align_and_job_submit_has_breathing_state():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "job-submit-button",
        "busy === 'job' ? 'is-breathing'",
        "aria-busy={busy === 'job'}",
    ):
        assert token in source

    for snippet in (
        ".profile-grid {\n  align-items: stretch",
        ".profile-grid .panel {\n  display: flex",
        "flex-direction: column",
        "min-height: 100%",
        ".profile-grid .primary {\n  margin-top: auto",
        ".job-submit-button.is-breathing",
        "@keyframes ctaBreath",
        "prefers-reduced-motion: reduce",
    ):
        assert snippet in css


def test_ui_new_cockpit_required_fields_use_inline_validation_and_shake():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "type UploadErrors",
        "validateUpload",
        "uploadErrors",
        "validationPulse",
        "validationTimeoutRef",
        "showUploadErrors",
        "window.setTimeout",
        "window.clearTimeout",
        "1500",
        "clearUploadError",
        "field-invalid",
        "field-error",
        "aria-invalid",
        "aria-describedby",
        "shake-${validationPulse % 2}",
        "shake-0",
        "shake-1",
        "请输入项目名称",
        "请上传 A 股报告 PDF",
        "请上传 H 股报告 PDF",
        "请上传 H 股中文报告 PDF",
        "请上传 H 股英文报告 PDF",
    ):
        assert token in source

    assert "Demo Company" not in source
    assert "form.append('company_name', upload.companyName.trim())" in source

    for snippet in (
        ".field-invalid",
        ".field-error",
        "@keyframes fieldShake",
        ".shake-0.field-invalid",
        ".shake-1.field-invalid",
        "animation: fieldShake",
        "@keyframes fieldErrorLife",
        "animation: fieldErrorLife 1.5s",
        "prefers-reduced-motion: reduce",
    ):
        assert snippet in css


def test_ui_new_cockpit_ticker_bar_uses_health_and_history_context():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "type HealthPayload",
        "CockpitTickerBar",
        "loadHealth",
        "/health",
        "result_version",
        "extraction_engine_version",
        "pendingCount",
        "latestDone",
        "formatDuration(latestDone.duration_seconds)",
        "tickerItems",
        "ticker-viewport",
        "ticker-track",
        "ticker-item",
        "ticker-separator",
        "提交前检查",
        "项目名称 + 双 PDF",
        "结果规则",
        "抽取引擎",
    ):
        assert token in source

    assert "CockpitBriefingBar" not in source
    assert "briefing-card" not in source

    for snippet in (
        ".page-context-bar {\n  display: grid",
        ".ticker-viewport",
        ".ticker-track",
        ".ticker-item",
        ".ticker-separator",
        "@keyframes tickerScroll",
        "animation: tickerScroll",
        "mask-image",
        "animation-play-state: paused",
        "@media (max-width: 720px)",
        "overflow-x: auto",
        "scrollbar-width: none",
        "prefers-reduced-motion: reduce",
    ):
        assert snippet in css

    assert ".briefing-card" not in css
    assert ".cockpit-briefing" not in css


def test_ui_new_cockpit_aligns_recent_history_with_primary_actions():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "history.slice(0, 5)",
        "command-history-actions",
        "command-history-link primary",
        "查看全部项目历史",
        "开始核查",
    ):
        assert token in source

    for snippet in (
        ".command-main,\n.command-history",
        "display: flex",
        "flex-direction: column",
        ".command-history .job-list",
        ".command-history-actions",
        ".command-history-link.primary",
        ".command-stats span",
        ".command-stats span {\n    flex: 1 1 132px",
    ):
        assert snippet in css


def test_ui_new_navigation_uses_quieter_toolbar_typography():
    css = APP_CSS.read_text(encoding="utf-8")

    for snippet in (
        ".nav a span {\n  font-size: 12.5px",
        "font-weight: 680",
        ".nav a small {\n  margin-top: 2px",
        "font-size: 9.5px",
        "font-weight: 560",
        ".nav a.active,\n.nav a:hover",
        "box-shadow: 0 5px 14px rgba(0, 51, 141, 0.14)",
    ):
        assert snippet in css


def test_ui_new_evidence_review_uses_full_screen_review_layout_and_rich_diff_fields():
    source = APP_TSX.read_text(encoding="utf-8")

    for label in (
        "证据链",
        "对照视图",
        "元数据",
        "规则 ID",
        "审阅提示",
        "A/H 取值",
        "差异率",
        "准则推理",
        "引用条款",
        "图表校核",
        "定位列表",
    ):
        assert label in source

    for field in (
        "bbox",
        "diff_explanation",
        "standard_reasoning",
        "chart_cross",
        "a_value",
        "h_value",
        "delta",
        "tolerance",
        "rule_id",
        "review_status",
    ):
        assert field in source

    for token in (
        "review-shell",
        "review-grid",
        "review-chain",
        "review-focus",
        "review-meta",
    ):
        assert token in source


def test_ui_new_job_detail_highlights_audit_profiles_and_dense_review_dashboard():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for label in (
        "Audit Conclusion",
        "A 股画像",
        "H 股画像",
        "画像事实",
        "指标 Key",
        "叙述块",
        "结构节点",
        "提取预警",
        "跨页事件",
        "证据定位",
    ):
        assert label in source

    for field in (
        "profile_a",
        "profile_h",
        "metric_occurrences",
        "extraction_audit",
        "warning_count",
        "matched_event_count",
        "coverage_count",
    ):
        assert field in source

    for token in (
        "detail-dashboard",
        "audit-conclusion-strip",
        "profile-showcase",
        "profile-card",
        "profile-preview-table",
        "diff-command-table",
    ):
        assert token in source
        assert f".{token}" in css


def test_ui_new_job_detail_reframes_audit_title_and_uses_bilingual_page_review():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "audit-project-title",
        "audit-meta-row",
        "audit-result-title",
        "H 股中英文逐页核对",
        "bilingual-page-review",
    ):
        assert token in source

    assert "job.check_mode !== 'h_bilingual'" in source
    assert "H 股中文画像" not in source
    assert "H 股英文画像" not in source

    for token in (
        "audit-project-title",
        "audit-meta-row",
        "audit-result-title",
        "bilingual-page-review",
        "bilingual-page-list",
    ):
        assert f".{token}" in css


def test_ui_new_job_detail_topbar_uses_report_actions_instead_of_page_title():
    source = APP_TSX.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    for token in (
        "job-report-actions",
        "job-report-action-link",
        "下载 Excel",
        "下载 PDF",
        "返回项目历史",
    ):
        assert token in source

    assert "route.page === 'job' ? (" in source
    assert "if (route.page === 'job') return '核查详情'" not in source
    assert "if (route.page === 'job') return '差异与证据复核'" not in source
    assert "detail-actions dense-actions" not in source

    for selector in (
        ".job-topbar",
        ".job-report-actions",
        ".job-report-action-link",
    ):
        assert selector in css


def test_ui_new_download_links_bust_browser_cache_for_latest_reports():
    source = APP_TSX.read_text(encoding="utf-8")

    assert "/report.xlsx?template=latest" in source
    assert "/report.pdf?template=latest" in source


def test_ui_new_is_served_from_root_and_static_legacy_moves_to_legacy_route():
    vite_config = VITE_CONFIG.read_text(encoding="utf-8")
    api_main = API_MAIN.read_text(encoding="utf-8")

    assert "base: '/app/'" in vite_config
    assert 'UI_NEW_DIST' in api_main
    assert 'def index() -> FileResponse:\n    return _no_cache_ui_new_index()' in api_main
    assert 'def index_html() -> FileResponse:\n    return _no_cache_ui_new_index()' in api_main
    assert '@app.get("/app"' in api_main
    assert 'app.mount("/app/assets"' in api_main
    assert 'app.mount("/legacy", StaticFiles' in api_main
    assert 'app.mount("/", StaticFiles' not in api_main


def test_competition_docker_deployment_builds_react_and_serves_fastapi_same_origin():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")

    for token in (
        "FROM node:20-bookworm-slim AS ui-builder",
        "COPY ui-new/package*.json ./ui-new/",
        "RUN npm ci",
        "RUN npm run build",
        "FROM python:3.12-slim AS runtime",
        "poppler-utils",
        "ghostscript",
        "COPY --from=ui-builder /app/ui-new/dist ./ui-new/dist",
        "ENV PORT=8001",
        'CMD ["sh", "-c", "uvicorn ahcc.api.main:app --host 0.0.0.0 --port ${PORT:-8001}"]',
    ):
        assert token in dockerfile

    for token in (
        ".env",
        "storage/",
        "ui-new/node_modules/",
        "ui-new/dist/",
        ".pytest_cache/",
        "__pycache__/",
    ):
        assert token in dockerignore


def test_render_blueprint_exposes_public_full_stack_entry_with_health_check():
    source = RENDER_YAML.read_text(encoding="utf-8")

    for token in (
        "name: ahcc-competition",
        "type: web",
        "env: docker",
        "dockerfilePath: ./Dockerfile",
        "healthCheckPath: /health",
        "APP_ENV",
        "production",
        "STORAGE_DIR",
        "/var/data/storage",
        "SQLITE_PATH",
        "DEEPSEEK_API_KEY",
        "sync: false",
        "mountPath: /var/data",
    ):
        assert token in source


def test_competition_local_launcher_targets_8001_cockpit_and_builds_frontend_when_needed():
    source = START_COMPETITION.read_text(encoding="utf-8")

    for token in (
        "[int]$Port = 8001",
        "$CockpitUrl = \"http://127.0.0.1:$Port/app#/cockpit\"",
        "$HealthUrl = \"http://127.0.0.1:$Port/health\"",
        "ui-new",
        "dist",
        "index.html",
        "npm run build",
        "ahcc.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "Start-Process $CockpitUrl",
    ):
        assert token in source


def test_readme_documents_public_competition_entry_and_localhost_limitations():
    readme = README.read_text(encoding="utf-8")
    app_source = APP_TSX.read_text(encoding="utf-8")

    for token in (
        "参赛演示入口",
        "127.0.0.1 不是评委可访问地址",
        "https://<your-domain>/app#/cockpit",
        "scripts/start_competition.ps1",
        "docker build",
        "docker run",
        "/health",
        "DEEPSEEK_API_KEY",
    ):
        assert token in readme

    assert "http://localhost" not in app_source
    assert "http://127.0.0.1" not in app_source
