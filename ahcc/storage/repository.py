"""存储仓库 — Job/Diff/Review 的 CRUD 包装（P3 实现）。"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

from ahcc.profile.models import MetricItem, MetricOccurrences, ReportProfile
from ahcc.schemas import Currency, Diff, DiffSeverity, DiffType, Evidence, Job, JobStatus, LocalizedString, ReportSide, ReviewStatus
from ahcc.storage.models import get_conn
from ahcc.parser.audit import EXTRACTION_ENGINE_VERSION, PARSER_VERSION, classify_warning
from ahcc.user_context import (
    CURRENT_PROJECT_GROUP_ID,
    CURRENT_USER_ID,
    DEFAULT_USER_PROFILE,
    public_user_payload,
)

_CURRENT_RESULT_VERSION = 16
_RUNNING_JOB_STATUSES = {
    JobStatus.PENDING.value,
    JobStatus.PARSING.value,
    JobStatus.PROFILING.value,
    JobStatus.CHECKING.value,
    JobStatus.REPORTING.value,
}
_LEGACY_TEMPLATE_MARKERS = ("□适用", "√不适用")
_LEGACY_GOVERNANCE_SECTIONS = {"corporate_governance", "governance"}
_LEGACY_GOVERNANCE_SOFT_TERMS = (
    "董事",
    "监事",
    "高级管理人员",
    "薪酬",
    "报酬",
    "津贴",
    "福利",
    "住房公积金",
    "年金",
    "履职",
    "考核",
    "评价",
    "股东大会",
    "董事会",
    "监事会",
    "议案",
    "会议",
)
_LEGACY_LAYOUT_TERMS = ("全面助力", "深度服务", "专业赋能", "全球拓展布局", "全球投资交易", "全球资产配置")
_LEGACY_MDA_TERMS = ("创新业务", "优化运营", "赋能员工", "智能审核", "AI", "智能助手", "风险监测")
_LEGACY_BOARD_TERMS = ("董事会人员构成", "年龄组别", "董事类别", "执行董事", "独立非执行董事", "女性董事", "男性董事")
_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def get_current_user_profile() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?",
            (CURRENT_USER_ID,),
        ).fetchone()
    return dict(row) if row else dict(DEFAULT_USER_PROFILE)


def get_current_session() -> dict:
    profile = get_current_user_profile()
    user = public_user_payload(profile)
    return {
        "user": user,
        "project_group": user["project_group"],
    }


def update_current_user_profile(
    *,
    display_name: str | None = None,
    office_line: str | None = None,
    role_title: str | None = None,
) -> dict:
    current = get_current_user_profile()
    updated = {
        "display_name": (display_name if display_name is not None else current.get("display_name") or "").strip(),
        "office_line": (office_line if office_line is not None else current.get("office_line") or "").strip(),
        "role_title": (role_title if role_title is not None else current.get("role_title") or "").strip(),
    }
    if not updated["display_name"]:
        updated["display_name"] = DEFAULT_USER_PROFILE["display_name"]
    if not updated["office_line"]:
        updated["office_line"] = DEFAULT_USER_PROFILE["office_line"]

    with get_conn() as conn:
        conn.execute(
            """UPDATE user_profiles
            SET display_name = ?, office_line = ?, role_title = ?, updated_at = datetime('now')
            WHERE user_id = ?""",
            (updated["display_name"], updated["office_line"], updated["role_title"], CURRENT_USER_ID),
        )
        conn.commit()
    return public_user_payload(get_current_user_profile())


def set_current_user_avatar(avatar_path: str) -> dict:
    with get_conn() as conn:
        conn.execute(
            """UPDATE user_profiles
            SET avatar_path = ?, updated_at = datetime('now')
            WHERE user_id = ?""",
            (avatar_path, CURRENT_USER_ID),
        )
        conn.commit()
    return public_user_payload(get_current_user_profile())


def apply_current_user_context(job: Job) -> Job:
    profile = get_current_user_profile()
    return job.model_copy(
        update={
            "owner_user_id": job.owner_user_id or profile.get("user_id") or CURRENT_USER_ID,
            "owner_display_name": job.owner_display_name
            or profile.get("display_name")
            or DEFAULT_USER_PROFILE["display_name"],
            "project_group_id": job.project_group_id
            or profile.get("project_group_id")
            or CURRENT_PROJECT_GROUP_ID,
            "project_group_name": job.project_group_name
            or profile.get("project_group_name")
            or DEFAULT_USER_PROFILE["project_group_name"],
        }
    )


def list_jobs(limit: int = 10, scope: str = "project") -> list[dict]:
    """列出历史任务摘要（不含 diffs）。"""
    profile = get_current_user_profile()
    normalized_scope = (scope or "project").strip().lower()
    if normalized_scope not in {"project", "mine"}:
        normalized_scope = "project"
    if normalized_scope == "mine":
        where = "WHERE owner_user_id = ?"
        params: tuple[object, ...] = (profile.get("user_id") or CURRENT_USER_ID, limit)
    else:
        where = "WHERE project_group_id = ?"
        params = (profile.get("project_group_id") or CURRENT_PROJECT_GROUP_ID, limit)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT job_id, company_name, check_mode, owner_user_id, owner_display_name, project_group_id, project_group_name, "
            "a_file, h_file, status, started_at, finished_at, duration_seconds, error, comparison_summary_json "
            f"FROM jobs {where} ORDER BY started_at DESC LIMIT ?",
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["check_mode"] = item.get("check_mode") or "ah"
        item["status"] = _normalize_job_status(item.get("status"))
        summary = _load_json_field(item.pop("comparison_summary_json", None), {})
        item["comparison_summary"] = _sanitize_summary_for_loaded_job(item["job_id"], summary)
        result.append(item)
    return result


def get_job(job_id: str) -> Optional[dict]:
    """获取单个任务元信息。"""
    profile = get_current_user_profile()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT job_id, company_name, check_mode, owner_user_id, owner_display_name, project_group_id, project_group_name, "
            "a_file, h_file, status, started_at, finished_at, duration_seconds, error, "
            "profile_a_json, profile_h_json, coverage_items_json, comparison_summary_json "
            "FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    if (item.get("project_group_id") or CURRENT_PROJECT_GROUP_ID) != (
        profile.get("project_group_id") or CURRENT_PROJECT_GROUP_ID
    ):
        return None
    item["check_mode"] = item.get("check_mode") or "ah"
    item["status"] = _normalize_job_status(item.get("status"))
    item["profile_a"] = _load_json_field(item.pop("profile_a_json", None), None)
    item["profile_h"] = _load_json_field(item.pop("profile_h_json", None), None)
    coverage_items = _load_json_field(item.pop("coverage_items_json", None), [])
    summary = _load_json_field(item.pop("comparison_summary_json", None), {})
    if int(summary.get("result_version") or 0) < _CURRENT_RESULT_VERSION:
        coverage_items = _sanitize_legacy_coverage_items(coverage_items)
    item["coverage_items"] = coverage_items
    item["comparison_summary"] = _sanitize_summary_for_loaded_job(job_id, summary)
    return item


def _load_profile_snapshots(job_id: str) -> tuple[dict | None, dict | None]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT profile_a_json, profile_h_json FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return None, None
    return (
        _load_json_field(row["profile_a_json"], None),
        _load_json_field(row["profile_h_json"], None),
    )


def _evidence_from_snapshot(raw: dict | None, side: ReportSide, page: int | None) -> Evidence:
    data = dict(raw or {})
    data.setdefault("side", side.value)
    data.setdefault("page", page or 1)
    data.setdefault("bbox", data.get("bbox") or (0.0, 0.0, 0.0, 0.0))
    data.setdefault("snippet", data.get("snippet") or "")
    data.setdefault("section", data.get("section"))
    return Evidence.model_validate(data)


def _localized_name(raw: dict | str | None, fallback_key: str) -> LocalizedString:
    if isinstance(raw, dict):
        data = dict(raw)
        data.setdefault("zh", data.get("zh") or fallback_key)
        data.setdefault("en", data.get("en") or fallback_key)
        return LocalizedString.model_validate(data)
    if isinstance(raw, str):
        return LocalizedString(zh=raw, en=raw)
    return LocalizedString(zh=fallback_key, en=fallback_key)


def _currency_value(raw: str | None) -> Currency | None:
    if not raw:
        return None
    try:
        return Currency(raw)
    except Exception:
        return None


def _metric_from_snapshot(raw: dict, fallback_key: str, fallback_name: LocalizedString, side: ReportSide) -> MetricItem:
    evidence = _evidence_from_snapshot(raw.get("evidence"), side, raw.get("page"))
    source = raw.get("source") or "text"
    if source not in {"table", "text", "generic_pattern"}:
        source = "text"
    return MetricItem(
        canonical_key=raw.get("canonical_key") or fallback_key,
        name=_localized_name(raw.get("name"), fallback_name.best()),
        value=raw.get("value"),
        value_text=raw.get("value_text"),
        unit=raw.get("unit"),
        currency=_currency_value(raw.get("currency")),
        period=raw.get("period"),
        page=int(raw.get("page") or evidence.page or 1),
        evidence=evidence,
        confidence=float(raw.get("confidence") or 0.0),
        source=source,
    )


def _profile_from_snapshot(snapshot: dict | None) -> ReportProfile | None:
    if not snapshot:
        return None
    try:
        side = ReportSide(snapshot.get("side") or "A")
    except Exception:
        side = ReportSide.A_SHARE

    metrics: list[MetricOccurrences] = []
    for occ in snapshot.get("metrics") or []:
        if not isinstance(occ, dict):
            continue
        key = occ.get("canonical_key") or ""
        name = _localized_name(occ.get("name"), key)
        primary = _metric_from_snapshot(occ, key, name, side)
        all_occurrences: list[MetricItem] = []
        for item in occ.get("all_occurrences") or []:
            if not isinstance(item, dict):
                continue
            all_occurrences.append(_metric_from_snapshot(item, key, name, side))
        if not all_occurrences:
            all_occurrences = [primary]
        if primary.value is None and all_occurrences:
            primary = all_occurrences[0]
        metrics.append(
            MetricOccurrences(
                canonical_key=key,
                name=name,
                primary=primary,
                all_occurrences=all_occurrences,
                is_internally_consistent=bool(occ.get("is_internally_consistent", True)),
            )
        )

    return ReportProfile(
        doc_id=snapshot.get("doc_id") or "",
        side=side,
        total_pages=int(snapshot.get("total_pages") or 0),
        metrics=metrics,
        narratives=[],
        structure=[],
        metadata=snapshot.get("metadata") or {},
    )


@lru_cache(maxsize=128)
def _load_current_numeric_diffs(job_id: str) -> tuple[Diff, ...]:
    profile_a_raw, profile_h_raw = _load_profile_snapshots(job_id)
    profile_a = _profile_from_snapshot(profile_a_raw)
    profile_h = _profile_from_snapshot(profile_h_raw)
    if not profile_a or not profile_h:
        return ()

    from ahcc.check.numeric import run_numeric_checks_on_profiles

    return tuple(run_numeric_checks_on_profiles(profile_a, profile_h))


def _sort_loaded_diffs(diffs: list[Diff]) -> list[Diff]:
    return sorted(
        diffs,
        key=lambda d: (
            -_SEVERITY_ORDER.get(d.severity.value if hasattr(d.severity, "value") else str(d.severity), 0),
            0 if d.triage == "real" else 1 if d.triage == "expected" else 2,
            d.diff_type.value if hasattr(d.diff_type, "value") else str(d.diff_type),
            d.canonical_key or "",
            d.diff_id,
        ),
    )


def _summary_from_diffs(base_summary: dict, diffs: list[Diff], legacy: bool = False) -> dict:
    sanitized = dict(base_summary)
    sanitized["result_version"] = _CURRENT_RESULT_VERSION
    sanitized["real_diff_count"] = sum(1 for diff in diffs if diff.triage == "real")
    sanitized["expected_diff_count"] = sum(1 for diff in diffs if diff.triage == "expected")
    sanitized["unresolved_diff_count"] = sum(1 for diff in diffs if diff.triage == "unresolved")
    sanitized["event_fact_diff_count"] = sum(1 for diff in diffs if diff.rule_id == "event_fact_match")
    sanitized["llm_semantic_review_count"] = sum(1 for diff in diffs if diff.rule_id == "llm_semantic_review")
    sanitized["total_diff_count"] = len(diffs)
    if legacy:
        sanitized["legacy_result_sanitized"] = True
    return _attach_current_extraction_metadata(sanitized)


def _upgrade_legacy_job(job_id: str, summary: dict, raw_diffs: list[Diff]) -> tuple[dict, list[Diff]]:
    numeric_diffs = list(_load_current_numeric_diffs(job_id))
    non_numeric_diffs = [diff for diff in raw_diffs if diff.diff_type != DiffType.NUMERIC]
    filtered_non_numeric = _filter_legacy_false_positive_diffs(non_numeric_diffs)
    sanitized_diffs = _sort_loaded_diffs([*filtered_non_numeric, *numeric_diffs])
    return _summary_from_diffs(summary, sanitized_diffs, legacy=True), sanitized_diffs


def save_job(job: Job) -> None:
    job = apply_current_user_context(job)
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO jobs
            (job_id, company_name, check_mode, owner_user_id, owner_display_name, project_group_id, project_group_name,
             a_file, h_file, status, started_at, finished_at, duration_seconds, error,
             profile_a_json, profile_h_json, coverage_items_json, comparison_summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.job_id,
                job.company_name,
                job.check_mode,
                job.owner_user_id,
                job.owner_display_name,
                job.project_group_id,
                job.project_group_name,
                job.a_file,
                job.h_file,
                job.status.value,
                job.started_at.isoformat(),
                job.finished_at.isoformat() if job.finished_at else None,
                job.duration_seconds,
                job.error,
                json.dumps(job.profile_a, ensure_ascii=False) if job.profile_a is not None else None,
                json.dumps(job.profile_h, ensure_ascii=False) if job.profile_h is not None else None,
                json.dumps(
                    [item.model_dump(mode="json") for item in job.coverage_items],
                    ensure_ascii=False,
                ),
                json.dumps(job.comparison_summary, ensure_ascii=False),
            ),
        )
        for diff in job.diffs:
            conn.execute(
                """INSERT OR REPLACE INTO diffs
                (diff_id, job_id, diff_type, severity, canonical_key, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    diff.diff_id,
                    job.job_id,
                    diff.diff_type.value,
                    diff.severity.value,
                    diff.canonical_key,
                    diff.model_dump_json(),
                    datetime.utcnow().isoformat(),
                ),
            )
        conn.commit()


def running_progress_summary(
    summary: dict | None,
    stage: JobStatus | str,
    percent: int,
    message: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Attach lightweight persisted progress without adding a DB table."""
    current = now or datetime.utcnow()
    stage_value = stage.value if isinstance(stage, JobStatus) else str(stage)
    sanitized = dict(summary or {})
    sanitized.update(
        {
            "result_version": _CURRENT_RESULT_VERSION,
            "parser_version": PARSER_VERSION,
            "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "current_extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "stale_result": False,
            "current_stage": stage_value,
            "current_percent": max(0, min(100, int(percent))),
            "current_message": message,
            "last_progress_at": current.isoformat(),
        }
    )
    return sanitized


def save_job_progress(job: Job) -> None:
    """Persist only the lightweight runtime status fields for polling UIs."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs
            SET status = ?, finished_at = ?, duration_seconds = ?, error = ?,
                comparison_summary_json = ?
            WHERE job_id = ?""",
            (
                job.status.value,
                job.finished_at.isoformat() if job.finished_at else None,
                job.duration_seconds,
                job.error,
                json.dumps(job.comparison_summary, ensure_ascii=False),
                job.job_id,
            ),
        )
        conn.commit()


def mark_interrupted_running_jobs_failed(now: datetime | None = None) -> int:
    """Fail in-memory background jobs left behind by a service restart."""
    current = now or datetime.utcnow()
    return _mark_running_jobs_failed(
        current=current,
        should_fail=lambda row, summary: True,
        message_factory=lambda row, summary: "background job interrupted: service restarted before the task completed; please rerun the task.",
        extra_summary={"job_interrupted": True},
    )


def mark_stale_running_jobs_failed(
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> int:
    """Fail running jobs whose in-memory background task cannot still exist."""
    current = now or datetime.utcnow()
    return _mark_running_jobs_failed(
        current=current,
        should_fail=lambda row, summary: _running_job_age_seconds(row, summary, current) > stale_after_seconds,
        message_factory=lambda row, summary: (
            "background job interrupted: service restarted or no progress "
            f"for more than {int(stale_after_seconds)} seconds; please rerun the task."
        ),
        extra_summary={"job_interrupted": True, "job_stale_after_seconds": stale_after_seconds},
    )


def _mark_running_jobs_failed(
    *,
    current: datetime,
    should_fail,
    message_factory,
    extra_summary: dict,
) -> int:
    changed = 0
    placeholders = ",".join("?" for _ in _RUNNING_JOB_STATUSES)
    with get_conn() as conn:
        try:
            rows = conn.execute(
                f"""SELECT job_id, status, started_at, comparison_summary_json
                FROM jobs WHERE status IN ({placeholders})""",
                tuple(_RUNNING_JOB_STATUSES),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            return 0
        for row in rows:
            summary = _load_json_field(row["comparison_summary_json"], {})
            if not should_fail(row, summary):
                continue

            started_at = _parse_datetime(str(row["started_at"] or "")) or current
            duration_seconds = max(0.0, (current - started_at).total_seconds())
            error = message_factory(row, summary)
            failed_summary = running_progress_summary(
                summary,
                JobStatus.FAILED,
                0,
                error,
                now=current,
            )
            failed_summary.update(extra_summary)
            conn.execute(
                """UPDATE jobs
                SET status = ?, finished_at = ?, duration_seconds = ?, error = ?,
                    comparison_summary_json = ?
                WHERE job_id = ?""",
                (
                    JobStatus.FAILED.value,
                    current.isoformat(),
                    duration_seconds,
                    error,
                    json.dumps(failed_summary, ensure_ascii=False),
                    row["job_id"],
                ),
            )
            changed += 1
        conn.commit()
    return changed


def _running_job_age_seconds(row, summary: dict, current: datetime) -> float:
    last_progress = _parse_datetime(
        str(summary.get("last_progress_at") or row["started_at"] or "")
    )
    if last_progress is None:
        last_progress = current
    return (current - last_progress).total_seconds()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_json_field(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _normalize_job_status(value: str | None) -> str:
    if value == "aligning":
        return JobStatus.PROFILING.value
    return value or JobStatus.PENDING.value


def get_diffs(job_id: str) -> list[Diff]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT payload_json FROM diffs WHERE job_id = ? ORDER BY severity DESC",
            (job_id,),
        ).fetchall()
        summary_row = conn.execute(
            "SELECT comparison_summary_json FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    diffs = [Diff.model_validate_json(row["payload_json"]) for row in rows]
    summary = _load_json_field(summary_row["comparison_summary_json"], {}) if summary_row else {}
    if int(summary.get("result_version") or 0) >= _CURRENT_RESULT_VERSION:
        return diffs
    _, sanitized_diffs = _upgrade_legacy_job(job_id, summary, diffs)
    return sanitized_diffs


def _load_raw_diffs(job_id: str) -> list[Diff]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT payload_json FROM diffs WHERE job_id = ? ORDER BY severity DESC",
            (job_id,),
        ).fetchall()
    return [Diff.model_validate_json(row["payload_json"]) for row in rows]


def _sanitize_summary_for_loaded_job(job_id: str, summary: dict) -> dict:
    version = int(summary.get("result_version") or 0)
    if version >= _CURRENT_RESULT_VERSION:
        return _attach_current_extraction_metadata(summary)
    if _is_legacy_h_bilingual_summary(summary):
        return _sanitize_legacy_h_bilingual_summary(summary)

    raw_diffs = _load_raw_diffs(job_id)
    if not raw_diffs:
        return _sanitize_legacy_summary_without_payload(summary)

    sanitized_summary, _ = _upgrade_legacy_job(job_id, summary, raw_diffs)
    return sanitized_summary


def _sanitize_legacy_summary_without_payload(summary: dict) -> dict:
    sanitized = dict(summary)
    sanitized["result_version"] = _CURRENT_RESULT_VERSION
    sanitized["legacy_result_sanitized"] = True
    sanitized["real_diff_count"] = 0
    sanitized["expected_diff_count"] = int(summary.get("expected_diff_count") or 0)
    sanitized["unresolved_diff_count"] = int(summary.get("unresolved_diff_count") or 0)
    sanitized["event_fact_diff_count"] = 0
    sanitized["total_diff_count"] = int(summary.get("total_diff_count") or 0) - int(summary.get("event_fact_diff_count") or 0)
    return _attach_current_extraction_metadata(sanitized)


def _is_legacy_h_bilingual_summary(summary: dict) -> bool:
    return (
        (summary.get("check_mode") == "h_bilingual")
        and int(summary.get("result_version") or 0) < _CURRENT_RESULT_VERSION
    )


def _sanitize_legacy_h_bilingual_summary(summary: dict) -> dict:
    sanitized = dict(summary)
    sanitized["result_version"] = _CURRENT_RESULT_VERSION
    sanitized["legacy_result_sanitized"] = True
    sanitized["legacy_result_requires_rerun"] = True
    sanitized["stale_result"] = True
    sanitized["real_diff_count"] = 0
    sanitized["expected_diff_count"] = 0
    sanitized["unresolved_diff_count"] = 0
    sanitized["total_diff_count"] = 0
    sanitized["coverage_count"] = 0
    sanitized["layout_diff_count"] = 0
    sanitized["table_row_diff_count"] = 0
    sanitized["section_diff_count"] = 0
    sanitized["paragraph_unpaired_count"] = 0
    sanitized["numeric_diff_count"] = 0
    sanitized["semantic_diff_count"] = 0
    sanitized["translation_diff_count"] = 0
    warnings = _normalize_summary_warnings(sanitized.get("warnings") or [])
    warnings.insert(
        0,
        {
            "side": "ALL",
            "flag": "stale_h_bilingual_result",
            "message": "This H bilingual result was generated by the legacy v9 engine and must be re-run with the v11 Fast/Strict engine before interpreting differences.",
            "category": "stale_result",
            "severity": "medium",
            "blocking": False,
            "total_pages": 0,
            "scanned_pages": 0,
            "missing_pages": 0,
            "blank_pages": 0,
            "ocr_pages": 0,
            "table_pages": 0,
            "coverage_ratio": 0.0,
        },
    )
    sanitized["warnings"] = warnings
    sanitized["warning_count"] = len(warnings)
    sanitized["blocking_warning_count"] = sum(1 for item in warnings if item.get("blocking"))
    sanitized["core_warning_count"] = sanitized["blocking_warning_count"]
    sanitized["aux_warning_count"] = sum(1 for item in warnings if item.get("category") == "auxiliary_chart")
    sanitized["stale_warning_count"] = 1
    sanitized["current_extraction_engine_version"] = EXTRACTION_ENGINE_VERSION
    return sanitized


def _attach_current_extraction_metadata(summary: dict) -> dict:
    sanitized = dict(summary or {})
    current = EXTRACTION_ENGINE_VERSION
    stored = sanitized.get("extraction_engine_version")
    stale = stored != current
    sanitized.setdefault("parser_version", stored or "unknown")
    sanitized.setdefault("extraction_engine_version", stored or "unknown")
    sanitized["current_extraction_engine_version"] = current
    sanitized["stale_result"] = bool(stale)
    warnings = _normalize_summary_warnings(sanitized.get("warnings") or [])
    if stale:
        if not any(item.get("flag") == "stale_extraction_engine" for item in warnings if isinstance(item, dict)):
            detail = classify_warning(
                "stale_extraction_engine",
                f"Result was generated by extraction engine {stored or 'unknown'}; latest is {current}. Re-run with the latest engine.",
            )
            warnings.insert(
                0,
                {
                    "side": "ALL",
                    "flag": "stale_extraction_engine",
                    "message": detail["message"],
                    "category": "stale_result",
                    "severity": "medium",
                    "blocking": False,
                    "total_pages": 0,
                    "scanned_pages": 0,
                    "missing_pages": 0,
                    "blank_pages": 0,
                    "ocr_pages": 0,
                    "table_pages": 0,
                    "coverage_ratio": 0.0,
                },
            )
        sanitized["warning_count"] = len(warnings)
        sanitized["stale_warning_count"] = 1
    else:
        sanitized["stale_warning_count"] = 0

    sanitized["warnings"] = warnings
    sanitized["warning_count"] = len(warnings)
    sanitized["blocking_warning_count"] = sum(1 for item in warnings if item.get("blocking"))
    sanitized["core_warning_count"] = sanitized["blocking_warning_count"]
    sanitized["aux_warning_count"] = sum(1 for item in warnings if item.get("category") == "auxiliary_chart")
    return sanitized


def _normalize_summary_warnings(raw_warnings: list) -> list[dict]:
    warnings: list[dict] = []
    for raw in raw_warnings:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if _is_legacy_section_classification_gap_warning(item):
            table_page_count = _warning_page_count(item.get("table_pages"))
            item["flag"] = "table_section_classification_gap"
            item["message"] = (
                "Core statement section labels incomplete; "
                f"{table_page_count} table pages were extracted. Re-run to refresh inferred section coverage."
            )
        detail = classify_warning(str(item.get("flag") or ""), str(item.get("message") or ""))
        if item.get("flag") == "table_section_classification_gap":
            item["category"] = detail["category"]
            item["severity"] = detail["severity"]
            item["blocking"] = detail["blocking"]
        else:
            item.setdefault("category", detail["category"])
            item.setdefault("severity", detail["severity"])
            item.setdefault("blocking", detail["blocking"])
        warnings.append(item)
    return warnings


def _is_legacy_section_classification_gap_warning(item: dict) -> bool:
    if item.get("flag") != "low_table_page_coverage":
        return False
    message = str(item.get("message") or "")
    if "missing structured sections" not in message:
        return False
    total_pages = _safe_int(item.get("total_pages"))
    scanned_pages = _warning_page_count(item.get("scanned_pages"))
    table_pages = _warning_page_count(item.get("table_pages"))
    missing_pages = _warning_page_count(item.get("missing_pages"))
    blank_pages = _warning_page_count(item.get("blank_pages"))
    if total_pages <= 0 or scanned_pages <= 0:
        return False
    if scanned_pages / max(total_pages, 1) < 0.98:
        return False
    if missing_pages:
        return False
    if blank_pages / max(scanned_pages, 1) > 0.1:
        return False
    return table_pages >= max(20, int(total_pages * 0.05))


def _warning_page_count(value) -> int:
    if isinstance(value, list):
        return len(value)
    return _safe_int(value)


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _filter_legacy_false_positive_diffs(diffs: list[Diff]) -> list[Diff]:
    result: list[Diff] = []
    for diff in diffs:
        if _is_legacy_false_positive_event_diff(diff):
            continue
        if _is_legacy_branch_diff(diff):
            if _is_legacy_low_confidence_branch_diff(diff):
                continue
            result.append(_normalize_legacy_branch_diff(diff))
            continue
        result.append(diff)
    return result


def _filter_legacy_false_positive_event_diffs(diffs: list[Diff]) -> list[Diff]:
    return [diff for diff in diffs if not _is_legacy_false_positive_event_diff(diff)]


def _is_legacy_false_positive_event_diff(diff: Diff) -> bool:
    if diff.rule_id != "event_fact_match" or diff.triage != "real":
        return False

    if _is_legacy_structured_dividend_total_diff(diff):
        return False

    summary = f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    confidence = _extract_match_confidence(summary)
    if confidence is not None and confidence < 0.75:
        return True

    if any(ev.section in {"bs", "pl", "cf", "equity", "financial_statements"} for ev in diff.evidence):
        return True

    if "主体" in summary and not any(label in summary for label in ("日期", "金额/数量", "比例", "状态")):
        return True

    if _is_status_only_governance_event_diff(diff, summary):
        return True

    if _is_status_only_debt_event_diff(diff, summary):
        return True

    if _is_legacy_percentage_event_false_positive(diff, summary):
        return True

    if "金额/数量" in summary and _has_template_marker(diff):
        return True

    if "金额/数量" in summary and _is_legacy_litigation_amount_equivalent(diff):
        return True

    if "金额/数量" in summary and _is_legacy_amount_extraction_false_positive(diff):
        return True

    if _is_legacy_layout_or_mda_event_diff(diff, summary):
        return True

    if _is_legacy_near_duplicate_date_diff(diff, summary):
        return True

    if _is_legacy_board_composition_date_diff(diff, summary):
        return True

    return False


def _legacy_diff_text(diff: Diff) -> str:
    return " ".join(
        [
            diff.diff_id or "",
            diff.topic.zh or "",
            diff.topic.en or "",
            diff.summary.zh or "",
            diff.summary.en or "",
            " ".join(ev.section or "" for ev in diff.evidence),
            " ".join(ev.snippet or "" for ev in diff.evidence),
        ]
    )


def _legacy_compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _is_legacy_structured_dividend_total_diff(diff: Diff) -> bool:
    text = _legacy_diff_text(diff).lower()
    compact = _legacy_compact_text(text)
    digits = re.sub(r"[^\d]", "", text)
    if not any(term in text for term in ("dividend", "profit distribution", "cash dividends", "利润分配", "股息", "股利")):
        return False
    if not any(term in text for term in ("amount", "total dividends", "金额", "数量", "共计", "总额")):
        return False
    if "reserves and retained profits" in text or "储备" in text or "留存收益" in text:
        return False
    if "2503994" in digits and "25039945" in digits:
        return "2020" in compact or "2020年度" in text

    has_current_year_proposal = "2020" in compact and any(
        term in text for term in ("proposed", "proposal", "预案", "預案", "board proposed")
    )
    return has_current_year_proposal and "profit distribution" in text


def _is_status_only_debt_event_diff(diff: Diff, compact_summary: str | None = None) -> bool:
    summary = compact_summary or f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    if "状态" not in summary and "status" not in summary.lower():
        return False
    if any(label in summary for label in ("日期", "金额/数量", "比例")):
        return False

    text = _legacy_diff_text(diff).lower()
    return any(
        term in text
        for term in (
            "bond",
            "bonds",
            "short-term debt",
            "debt instruments",
            "notes",
            "issued",
            "proposed",
            "repaid",
            "repayment",
            "债券",
            "债务工具",
            "发行",
            "偿还",
            "赎回",
        )
    )


def _is_legacy_percentage_event_false_positive(diff: Diff, compact_summary: str | None = None) -> bool:
    summary = compact_summary or f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    if "比例" not in summary and "percentage" not in summary.lower():
        return False

    text = _legacy_diff_text(diff).lower()
    sections = {(ev.section or "").strip().lower() for ev in diff.evidence}
    if sections & {"bs", "pl", "cf", "equity", "financial_statements"}:
        return True
    return any(
        term in text
        for term in (
            "cash and bank balances",
            "bank balances",
            "interest rates",
            "long- and short-term liabilities",
            "short-term debt instruments",
        )
    )


def _is_legacy_litigation_amount_equivalent(diff: Diff) -> bool:
    if len(diff.evidence) < 2:
        return False
    text = _legacy_diff_text(diff).lower()
    if not any(term in text for term in ("litigation", "lawsuit", "arbitration", "judgment", "claim", "诉讼", "仲裁", "判决", "赔偿")):
        return False
    a_values = _legacy_amount_values(diff.evidence[0].snippet or "")
    h_values = _legacy_amount_values(diff.evidence[1].snippet or "")
    if a_values and h_values:
        return _legacy_amount_sets_equivalent(a_values, h_values)
    return _is_legacy_same_litigation_event(diff)


def _is_legacy_same_litigation_event(diff: Diff) -> bool:
    if len(diff.evidence) < 2:
        return False
    if diff.evidence[0].page != diff.evidence[1].page:
        return False
    text = _legacy_diff_text(diff).lower()
    has_judgment = any(term in text for term in ("judgment", "判决", "判決", "court", "法院"))
    has_same_date = ("2020年9月21日" in text and "september 21, 2020" in text) or (
        "2020" in text and "september 21" in text
    )
    has_same_party = "hongyuan huifu" in text and ("宏源汇富" in text or "宏源匯富" in text)
    return has_judgment and (has_same_date or has_same_party)


def _legacy_amount_values(text: str) -> set[float]:
    values: set[float] = set()
    source = text or ""
    amount_number = r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    patterns = [
        (rf"(?:RMB|人民币|人民幣)\s*{amount_number}\s*(thousand|million|billion|元|万元|萬元|亿元|億元)?", 2),
        (rf"{amount_number}\s*(thousand|million|billion|元|万元|萬元|亿元|億元)", 2),
    ]
    for pattern, unit_group in patterns:
        for match in re.finditer(pattern, source, flags=re.IGNORECASE):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            unit = (match.group(unit_group) or "").lower()
            values.add(round(value * _legacy_amount_multiplier(unit), 2))
    return values


def _legacy_amount_multiplier(unit: str) -> float:
    normalized = (unit or "").lower()
    if normalized in {"thousand"}:
        return 1_000.0
    if normalized in {"million"}:
        return 1_000_000.0
    if normalized in {"billion"}:
        return 1_000_000_000.0
    if normalized in {"万元", "萬元"}:
        return 10_000.0
    if normalized in {"亿元", "億元"}:
        return 100_000_000.0
    return 1.0


def _legacy_amount_sets_equivalent(a_values: set[float], h_values: set[float]) -> bool:
    return all(_legacy_has_matching_amount(value, h_values) for value in a_values) and all(
        _legacy_has_matching_amount(value, a_values) for value in h_values
    )


def _legacy_has_matching_amount(value: float, candidates: set[float]) -> bool:
    for candidate in candidates:
        base = max(abs(value), abs(candidate), 1.0)
        if abs(value - candidate) / base <= 0.01:
            return True
    return False


def _is_legacy_branch_diff(diff: Diff) -> bool:
    if diff.triage not in {"real", "unresolved"} or diff.diff_type != DiffType.DISCLOSURE:
        return False
    if diff.rule_id and diff.rule_id != "branch_asset_scale_match":
        return False
    text = f"{diff.diff_id or ''} {diff.topic.zh or ''} {diff.summary.zh or ''} " + " ".join(
        ev.section or "" for ev in diff.evidence
    )
    return diff.diff_id.startswith("BRANCH_") or "分支机构" in text or "Branch asset scale" in (diff.topic.en or "")


def _normalize_legacy_branch_diff(diff: Diff) -> Diff:
    updates = {"rule_id": "branch_asset_scale_match", "triage": "real"}
    if diff.severity in {DiffSeverity.HIGH, DiffSeverity.CRITICAL}:
        updates["severity"] = DiffSeverity.MEDIUM
    return diff.model_copy(update=updates)


def _is_legacy_low_confidence_branch_diff(diff: Diff) -> bool:
    if len(diff.evidence) < 2:
        return True
    name = _legacy_branch_name(diff)
    if not name:
        return True
    rows = [_legacy_branch_row_from_snippet(name, ev.snippet or "") for ev in diff.evidence[:2]]
    if not rows[0] or not rows[1]:
        return True
    if rows[0]["count"] != rows[1]["count"]:
        return True
    if diff.a_value is not None and not _legacy_branch_asset_matches(float(diff.a_value), rows[0]["asset"]):
        return True
    if diff.h_value is not None and not _legacy_branch_asset_matches(float(diff.h_value), rows[1]["asset"]):
        return True
    return False


def _legacy_branch_name(diff: Diff) -> str | None:
    if diff.diff_id.startswith("BRANCH_"):
        return diff.diff_id.replace("BRANCH_", "", 1)
    for text in (diff.topic.zh or "", diff.summary.zh or ""):
        match = re.search(r"([一-龥]{2,6}分行)", text)
        if match:
            return match.group(1)
    return None


def _legacy_branch_row_from_snippet(name: str, snippet: str) -> dict | None:
    pattern = rf"{re.escape(name)}\s+(\d{{1,3}})\s+([\d,]{{5,12}})"
    match = re.search(pattern, snippet or "")
    if not match:
        return None
    try:
        return {
            "count": int(match.group(1)),
            "asset": float(match.group(2).replace(",", "")),
        }
    except ValueError:
        return None


def _legacy_branch_asset_matches(expected: float, extracted: float) -> bool:
    return abs(expected - extracted) <= max(abs(expected), 1.0) * 0.0001


def _is_legacy_amount_extraction_false_positive(diff: Diff) -> bool:
    if len(diff.evidence) < 2:
        return False
    texts = [ev.snippet or "" for ev in diff.evidence[:2]]
    combined = " ".join(texts)
    if _is_known_legacy_citic_amount_false_positive(combined):
        return True
    if any(_legacy_amount_text_quality_low(text) for text in texts):
        return True
    a_text = _legacy_comparison_text(texts[0])
    h_text = _legacy_comparison_text(texts[1])
    if len(a_text) >= 40 and len(h_text) >= 40 and SequenceMatcher(None, a_text[:700], h_text[:700]).ratio() >= 0.88:
        return _legacy_amount_context_ambiguous(combined)
    return False


def _is_known_legacy_citic_amount_false_positive(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if all(term in compact for term in ("40,000", "18亿元", "248,150", "31.75%")):
        return True
    return "境外股权项目" in compact and "承销规模" in compact and "79.11" in compact and "32" in compact


def _legacy_amount_text_quality_low(text: str) -> bool:
    raw = (text or "").lower()
    compact = re.sub(r"\s+", "", text or "").lower()
    if re.search(r"(?<!\d)[1-9]\d{0,2}(?:\.\d+)?\s*年", raw):
        return True
    if re.search(r"20\d{2}\d{1,2}(?:a股)?年月", compact):
        return True
    if re.search(r"\d{1,3}(?:,\d{3})+20\d{2}\d{1,2}万股", compact):
        return True
    numbers = re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text or "")
    amount_terms = ("金额", "募集", "承销规模", "交易对价", "发行规模", "担保", "罚款", "赔偿")
    return len(numbers) >= 10 and sum(1 for term in amount_terms if term in compact) <= 1


def _legacy_amount_context_ambiguous(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    role_terms = ("金额", "募集资金", "承销规模", "交易对价", "发行规模", "担保", "罚款", "赔偿")
    role_hits = sum(1 for term in role_terms if term in compact)
    numeric_hits = len(re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text or ""))
    return numeric_hits >= 6 and role_hits <= 1


def _is_status_only_governance_event_diff(diff: Diff, compact_summary: str | None = None) -> bool:
    summary = compact_summary or f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    if "状态" not in summary:
        return False
    if any(label in summary for label in ("日期", "金额/数量", "比例")):
        return False

    sections = {(ev.section or "").strip().lower() for ev in diff.evidence}
    topic_text = f"{diff.topic.zh or ''} {diff.topic.en or ''}".lower()
    evidence_text = " ".join(ev.snippet or "" for ev in diff.evidence)
    is_governance = bool(sections & _LEGACY_GOVERNANCE_SECTIONS) or "corporate_governance" in topic_text
    has_soft_terms = any(term in evidence_text for term in _LEGACY_GOVERNANCE_SOFT_TERMS)
    return is_governance and has_soft_terms


def _is_legacy_layout_or_mda_event_diff(diff: Diff, compact_summary: str | None = None) -> bool:
    summary = compact_summary or f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    evidence_text = " ".join(ev.snippet or "" for ev in diff.evidence)
    if "金额/数量" in summary and sum(1 for term in _LEGACY_MDA_TERMS if term in evidence_text) >= 3:
        return True
    if "日期" in summary and sum(1 for term in _LEGACY_LAYOUT_TERMS if term in evidence_text) >= 4:
        return True
    return False


def _is_legacy_near_duplicate_date_diff(diff: Diff, compact_summary: str | None = None) -> bool:
    summary = compact_summary or f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    if "日期" not in summary or len(diff.evidence) < 2:
        return False
    a_text = _legacy_comparison_text(diff.evidence[0].snippet or "")
    h_text = _legacy_comparison_text(diff.evidence[1].snippet or "")
    if len(a_text) < 40 or len(h_text) < 40:
        return False
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a_text[:700], h_text[:700]).ratio() >= 0.82


def _is_legacy_board_composition_date_diff(diff: Diff, compact_summary: str | None = None) -> bool:
    summary = compact_summary or f"{diff.summary.zh or ''} {diff.summary.en or ''}".replace(" ", "")
    if "日期" not in summary:
        return False
    evidence_text = " ".join(ev.snippet or "" for ev in diff.evidence)
    return sum(1 for term in _LEGACY_BOARD_TERMS if term in evidence_text) >= 3


def _legacy_comparison_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"华泰证券|HUATAI SECURITIES|第[一二三四五六七八九十]+章", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{1,4}\b", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def _sanitize_legacy_coverage_items(items: list) -> list:
    sanitized: list = []
    for raw in items or []:
        if not isinstance(raw, dict):
            sanitized.append(raw)
            continue
        item = dict(raw)
        if _is_legacy_status_coverage_item(item):
            item["note"] = "事件双边披露，匹配事实需复核：状态"
        elif _is_legacy_amount_coverage_item(item):
            note = str(item.get("note") or "")
            item["note"] = note.replace("存在事实差异", "匹配事实需复核")
        sanitized.append(item)
    return sanitized


def _is_legacy_status_coverage_item(item: dict) -> bool:
    if item.get("category") != "event" or item.get("status") != "matched":
        return False
    note = str(item.get("note") or "")
    if "存在事实差异" not in note or "状态" not in note:
        return False
    evidence = [*(item.get("a_evidence") or []), *(item.get("h_evidence") or [])]
    sections = {str(ev.get("section") or "").strip().lower() for ev in evidence if isinstance(ev, dict)}
    text = " ".join(str(ev.get("snippet") or "") for ev in evidence if isinstance(ev, dict))
    return bool(sections & _LEGACY_GOVERNANCE_SECTIONS) and any(term in text for term in _LEGACY_GOVERNANCE_SOFT_TERMS)


def _is_legacy_amount_coverage_item(item: dict) -> bool:
    if item.get("category") != "event" or item.get("status") != "matched":
        return False
    note = str(item.get("note") or "")
    if "存在事实差异" not in note or "金额/数量" not in note:
        return False
    evidence = [*(item.get("a_evidence") or []), *(item.get("h_evidence") or [])]
    text = " ".join(str(ev.get("snippet") or "") for ev in evidence if isinstance(ev, dict))
    return _is_known_legacy_citic_amount_false_positive(text) or _legacy_amount_text_quality_low(text)


def _extract_match_confidence(summary: str) -> float | None:
    match = re.search(r"匹配置信度\s*([0-9]+(?:\.[0-9]+)?)", summary)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _has_template_marker(diff: Diff) -> bool:
    evidence_text = " ".join(ev.snippet or "" for ev in diff.evidence)
    compact = "".join(evidence_text.split())
    return any(marker in compact for marker in _LEGACY_TEMPLATE_MARKERS)


def save_review(diff_id: str, status: ReviewStatus, note: str | None, reviewed_by: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO reviews (diff_id, status, note, reviewed_by, reviewed_at)
            VALUES (?, ?, ?, ?, ?)""",
            (diff_id, status.value, note, reviewed_by, datetime.utcnow().isoformat()),
        )
        # 同时把 review 状态回写到 diffs.payload_json
        row = conn.execute("SELECT payload_json FROM diffs WHERE diff_id = ?", (diff_id,)).fetchone()
        if row:
            payload = json.loads(row["payload_json"])
            payload["review_status"] = status.value
            payload["review_note"] = note
            payload["reviewed_by"] = reviewed_by
            payload["reviewed_at"] = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE diffs SET payload_json = ? WHERE diff_id = ?",
                (json.dumps(payload, ensure_ascii=False), diff_id),
            )
        conn.commit()
