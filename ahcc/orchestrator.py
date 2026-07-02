"""任务编排：上传两份 PDF → 解析 → 画像 → 三类检查 → 报告。

P1 在 Day 2 联调时把各模块的占位调用替换为真实实现。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from ahcc.config import settings
from ahcc.schemas import (
    DisclosureCoverageItem,
    DiffScope,
    Evidence,
    Job,
    JobProgress,
    JobStatus,
    LocalizedString,
    ReportDocument,
    ReportSide,
)
from ahcc.storage.repository import _CURRENT_RESULT_VERSION, running_progress_summary


class Orchestrator:
    """主任务编排器。各阶段以 async 实现，方便后续把 LLM 调用并发化。"""

    def __init__(self) -> None:
        settings.ensure_dirs()
        self._progress_callback: Callable[[Job], None] | None = None
        self._visual_ocr_status: dict[str, Any] | None = None
        self._branch_diagnostics: dict[str, Any] | None = None

    async def run(
        self,
        a_file: str,
        h_file: str,
        company_name: str | None = None,
        check_mode: str = "ah",
        bilingual_level: str = "fast",
        visual_review_mode: str = "off",
        job: Job | None = None,
        progress_callback: Callable[[Job], None] | None = None,
    ) -> Job:
        self._progress_callback = progress_callback
        job = job or Job(
            job_id=str(uuid.uuid4())[:8],
            company_name=company_name,
            check_mode=check_mode,
            a_file=a_file,
            h_file=h_file,
        )
        logger.info(f"[{job.job_id}] 启动任务: mode={check_mode} A={a_file}  H={h_file}")
        self._emit(job, JobStatus.PENDING, 0, "任务已创建")

        try:
            if check_mode == "h_bilingual":
                return await self._run_h_bilingual(job, a_file, h_file, bilingual_level=bilingual_level)

            # ---------- 阶段 1：解析（A/H 并行）----------
            self._emit(job, JobStatus.PARSING, 10, "并行解析 A/H 股年报")

            async def _parse_side(file_path: str, side: ReportSide) -> ReportDocument:
                doc = await self._parse(file_path, side)
                if not settings.demo_mode and settings.enable_chart_vlm_check:
                    doc = await self._detect_charts(doc, job.job_id)
                return doc

            doc_a, doc_h = await asyncio.gather(
                _parse_side(a_file, ReportSide.A_SHARE),
                _parse_side(h_file, ReportSide.H_SHARE),
            )
            self._emit(job, JobStatus.PARSING, 35, "年报解析完成")

            # ---------- 阶段 2：画像提取（A/H 并行）----------
            self._emit(job, JobStatus.PROFILING, 40, "并行提取 A/H 股年报画像")
            profile_a, profile_h = await asyncio.gather(
                self._build_profile(doc_a),
                self._build_profile(doc_h),
            )
            logger.info(f"[{job.job_id}] A股画像: {len(profile_a.metrics)} metrics, {len(profile_a.narratives)} narratives")
            logger.info(f"[{job.job_id}] H股画像: {len(profile_h.metrics)} metrics, {len(profile_h.narratives)} narratives")

            job.profile_a = profile_a.profile_summary
            job.profile_h = profile_h.profile_summary

            # ---------- 阶段 3：画像比对 + 检查（三类检查独立容错）----------
            # 主路径固定使用 profile pipeline。旧 pair-wise align 仅保留为内部方法，不再作为默认回退。
            # numeric/disclosure 为 CPU（各自 to_thread），standard 为 LLM async，三者相互独立可并发。
            self._emit(job, JobStatus.CHECKING, 55, "画像比对 + 数值/准则/披露并行检测")
            module_warnings: list[dict] = []
            visual_ocr_status: dict[str, Any] = {}
            self._visual_ocr_status = visual_ocr_status
            async def _empty_diffs() -> list:
                return []

            numeric_diffs, standard_diffs, disclosure_diffs, tamper_diffs, overlay_diffs, branch_diffs = await asyncio.gather(
                self._safe_check(self._check_numeric_profiles(profile_a, profile_h), job, "数值检查", [], module_warnings, 60),
                self._safe_check(self._check_standard_profiles(profile_a, profile_h), job, "准则检查（RAG）", [], module_warnings, 65)
                if settings.enable_standard_check
                else _empty_diffs(),
                self._safe_check(self._check_disclosure_profiles(profile_a, profile_h), job, "披露检查", [], module_warnings, 70),
                self._safe_check(
                    self._check_key_metric_tamper(
                        profile_a,
                        profile_h,
                        visual_review_mode=visual_review_mode,
                    ),
                    job,
                    "视觉 OCR 抽样复核",
                    [],
                    module_warnings,
                    75,
                ),
                self._safe_check(self._check_text_overlay(a_file, h_file), job, "文本层叠加篡改检测", [], module_warnings, 72)
                if settings.enable_text_overlay_check
                else _empty_diffs(),
                self._safe_check(self._check_branch(a_file, h_file), job, "分支机构核查", [], module_warnings, 73),
            )
            visual_warning = self._visual_ocr_warning(visual_ocr_status)
            if visual_warning:
                module_warnings.append(visual_warning)
            coverage_items, event_diffs = [], []
            if settings.enable_disclosure_coverage_check:
                self._emit(job, JobStatus.CHECKING, 80, "披露覆盖与跨页事件核查")
                coverage_items, event_diffs = await self._safe_check(
                    self._build_disclosure_coverage(profile_a, profile_h),
                    job,
                    "披露覆盖核查",
                    ([], []),
                    module_warnings,
                    85,
                )
            job.coverage_items = coverage_items

            chart_diffs = []
            if not settings.demo_mode and settings.enable_chart_vlm_check:
                self._emit(job, JobStatus.CHECKING, 90, "图表三方交叉核对")
                chart_diffs = await self._safe_check(self._check_chart(doc_a, doc_h), job, "图表核对（VLM）", [], module_warnings, 92)

            job.diffs = [
                *numeric_diffs,
                *tamper_diffs,
                *standard_diffs,
                *disclosure_diffs,
                *event_diffs,
                *chart_diffs,
                *overlay_diffs,
                *branch_diffs,
            ]
            job.diffs = self._dedupe_overlay_shadows(job.diffs)

            # ---------- 阶段 4：报告 ----------
            # 先结算汇总与耗时，再生成报告 —— 确保报告内「核查耗时/生成时间/提取预警」取到真实值
            self._emit(job, JobStatus.REPORTING, 95, "生成报告")
            job.comparison_summary = self._build_comparison_summary(
                job,
                profile_a,
                profile_h,
                visual_review_mode=visual_review_mode,
                module_warnings=module_warnings,
            )
            job.comparison_summary["visual_ocr_status"] = visual_ocr_status or {"mode": visual_review_mode}
            job.finished_at = datetime.utcnow()
            job.duration_seconds = (job.finished_at - job.started_at).total_seconds()

            await self._safe_check(self._build_report(job), job, "报告生成", None, module_warnings, 98)

            job.status = JobStatus.DONE
            self._emit(job, JobStatus.DONE, 100, f"完成，识别 {len(job.diffs)} 条差异")
            return job

        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{job.job_id}] 任务失败")
            job.status = JobStatus.FAILED
            job.error = str(exc)
            self._emit(job, JobStatus.FAILED, 0, f"失败：{exc}")
            return job

    async def _run_h_bilingual(
        self,
        job: Job,
        zh_file: str,
        en_file: str,
        *,
        bilingual_level: str = "fast",
    ) -> Job:
        level = (bilingual_level or "fast").strip().lower()
        if level not in {"fast", "strict"}:
            level = "fast"
        phase_timings: dict[str, float] = {}

        self._emit(job, JobStatus.PARSING, 10, "解析 H 股中文报告")
        started = time.perf_counter()
        doc_zh = await self._parse(zh_file, ReportSide.H_SHARE)
        phase_timings["parse_zh_seconds"] = round(time.perf_counter() - started, 4)

        self._emit(job, JobStatus.PARSING, 35, "解析 H 股英文报告")
        started = time.perf_counter()
        doc_en = await self._parse(en_file, ReportSide.H_SHARE)
        phase_timings["parse_en_seconds"] = round(time.perf_counter() - started, 4)

        self._emit(job, JobStatus.CHECKING, 70, "以中文为准核对英文翻译、数字与单位")
        from ahcc.check.bilingual import evaluate_semantic_with_llm, run_bilingual_checks
        from types import SimpleNamespace

        semantic_evaluator = evaluate_semantic_with_llm if level == "strict" else None
        enable_semantic = level == "strict"
        module_warnings: list[dict] = []
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                run_bilingual_checks,
                doc_zh,
                doc_en,
                semantic_evaluator=semantic_evaluator,
                enable_semantic=enable_semantic,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{job.job_id}] H股中英文核对失败")
            msg = f"H股中英文核对失败，已跳过：{exc}"
            self._emit(job, JobStatus.CHECKING, 75, msg)
            module_warnings.append({
                "flag": "h_bilingual_check_failed",
                "message": msg,
                "category": "extraction",
                "severity": "high",
                "blocking": False,
                "side": "",
            })
            result = SimpleNamespace(diffs=[], coverage_items=[], warnings=[], stats={})
        phase_timings["check_seconds"] = round(time.perf_counter() - started, 4)
        job.diffs = result.diffs
        job.coverage_items = self._normalize_bilingual_coverage_items(result.coverage_items)

        self._emit(job, JobStatus.REPORTING, 95, "生成英文翻译核对报告")
        # 先结算汇总与耗时，再生成报告；report_seconds 经 phase_timings 引用在报告后回填
        job.comparison_summary = self._build_bilingual_summary(
            job,
            doc_zh,
            doc_en,
            result,
            bilingual_level=level,
            phase_timings=phase_timings,
            module_warnings=module_warnings,
        )
        job.finished_at = datetime.utcnow()
        job.duration_seconds = (job.finished_at - job.started_at).total_seconds()

        started = time.perf_counter()
        await self._safe_check(self._build_report(job), job, "报告生成", None, module_warnings, 98)
        phase_timings["report_seconds"] = round(time.perf_counter() - started, 4)

        job.status = JobStatus.DONE
        self._emit(job, JobStatus.DONE, 100, f"完成，识别 {len(job.diffs)} 条英文翻译问题")
        return job

    # ----------------- 内部 step（Day 2-3 由各 P 替换为真实实现）-----------------

    async def _parse(self, file_path: str, side: ReportSide) -> ReportDocument:
        """占位实现 — 由 P2 在 ahcc/parser/ 完成。"""
        from ahcc.parser import parse_report  # 延迟导入避免循环
        return await asyncio.to_thread(parse_report, file_path, side)

    async def _align(self, doc_a: ReportDocument, doc_h: ReportDocument):
        """占位实现 — 由 P4 在 ahcc/align/ 完成。"""
        from ahcc.align.matcher import align_documents
        return await align_documents(doc_a, doc_h)

    async def _check_numeric(self, pairs):
        """模块 A — P3 实现。"""
        from ahcc.check.numeric import run_numeric_checks
        return await asyncio.to_thread(run_numeric_checks, pairs)

    async def _check_standard(self, pairs):
        """模块 B — P4 实现，调用 RAG。"""
        from ahcc.check.standard import run_standard_checks
        return await run_standard_checks(pairs)

    async def _check_disclosure(self, doc_a: ReportDocument, doc_h: ReportDocument):
        """披露差异检查 — P4 实现。"""
        from ahcc.check.disclosure import run_disclosure_checks
        return await run_disclosure_checks(doc_a, doc_h)

    async def _check_chart(self, doc_a: ReportDocument, doc_h: ReportDocument):
        """模块 C — P4 实现，调用 VLM。Demo 模式下最多核对 15 张图表。"""
        from ahcc.check.chart import run_chart_checks
        return await run_chart_checks(doc_a, doc_h, max_charts=15)

    async def _detect_charts(self, doc: ReportDocument, job_id: str) -> ReportDocument:
        """图表区域检测 — P2 实现。"""
        from ahcc.parser.audit import add_audit_warning
        from ahcc.parser.chart_detect import detect_charts

        try:
            import fitz  # noqa: F401
        except ImportError:
            add_audit_warning(doc, "chart_engine_unavailable", "PyMuPDF is unavailable; chart detection was skipped.")
            return doc

        out_dir = settings.storage_dir / "charts" / job_id / doc.side.value
        try:
            max_pages = max(int(getattr(settings, "chart_detection_max_pages", 60) or 60), 1)
            charts = await asyncio.to_thread(detect_charts, doc.file_path, out_dir, max_pages=max_pages)
        except Exception as exc:  # noqa: BLE001
            add_audit_warning(doc, "chart_detection_failed", f"Chart detection failed: {exc}")
            logger.warning(f"图表检测失败: {exc}")
            return doc
        doc.charts = charts
        return doc

    async def _build_profile(self, doc: ReportDocument):
        """从 ReportDocument 提取完整画像。"""
        from ahcc.profile import build_profile
        return await build_profile(doc)

    async def _compare_profiles(self, profile_a, profile_h):
        """比对 A/H 画像。"""
        from ahcc.profile import compare_profiles
        return await compare_profiles(profile_a, profile_h)

    async def _check_numeric_profiles(self, profile_a, profile_h):
        """数值差异检测（基于画像）。"""
        from ahcc.check.numeric import run_numeric_checks_on_profiles
        return await asyncio.to_thread(run_numeric_checks_on_profiles, profile_a, profile_h)

    async def _check_key_metric_tamper(
        self,
        profile_a,
        profile_h,
        *,
        visual_review_mode: str = "off",
        visual_ocr_status: dict[str, Any] | None = None,
    ):
        """指标精确差异与视觉层篡改检测，作为全量画像核查的补充。"""
        from ahcc.check.key_metric_tamper import run_key_metric_tamper_checks
        return await asyncio.to_thread(
            run_key_metric_tamper_checks,
            profile_a,
            profile_h,
            visual_review_mode=visual_review_mode,
            visual_ocr_status=visual_ocr_status if visual_ocr_status is not None else self._visual_ocr_status,
        )

    async def _check_standard_profiles(self, profile_a, profile_h):
        """准则差异智能解读（基于画像）。"""
        from ahcc.check.standard import run_standard_checks_on_profiles
        return await run_standard_checks_on_profiles(profile_a, profile_h)

    async def _check_text_overlay(self, a_file: str, h_file: str):
        """文本层叠加篡改检测：纯 fitz 扫描，不依赖 profile 解析结果，独立于 visual_review_mode。"""
        from ahcc.check.text_overlay_tamper import run_text_overlay_checks
        return await asyncio.to_thread(run_text_overlay_checks, a_file, h_file)

    async def _check_branch(self, a_file: str, h_file: str):
        """分支机构资产规模核查：轻量 fitz 文本抽取，不依赖 profile.source_doc 是否可用。"""
        from ahcc.check.branch_disclosure import run_branch_checks
        diffs, diagnostics = await asyncio.to_thread(run_branch_checks, a_file, h_file)
        self._branch_diagnostics = diagnostics
        return diffs

    @staticmethod
    def _dedupe_overlay_shadows(diffs: list) -> list:
        """去重：视觉 OCR 复核（visual_text_layer_mismatch）与 profile 内部一致性检查可能
        对同一处文本层叠加篡改重复产出 Diff。以 text_overlay_tamper 为准，剔除同侧同页、
        数值对相同（允许相差 10 的整数次幂——视觉层常把"百万元"换算成"元"）的重复项，
        避免同一处植入错误在报告里出现两遍、评估时被误算作误报。"""
        import math

        overlay_pairs: dict[tuple[str, int], list[tuple[float, float]]] = {}
        for d in diffs:
            if d.rule_id != "text_overlay_tamper" or not d.evidence:
                continue
            if d.a_value is None or d.h_value is None:
                continue
            key = (d.evidence[0].side.value, d.evidence[0].page)
            overlay_pairs.setdefault(key, []).append((d.a_value, d.h_value))

        def _scaled_equal(x: float, u: float) -> float | None:
            """x ≈ u * 10^n 时返回缩放系数 10^n，否则 None。"""
            if not x or not u:
                return None
            ratio = abs(x / u)
            if ratio <= 0:
                return None
            exponent = math.log10(ratio)
            nearest = round(exponent)
            if abs(exponent - nearest) > 1e-6:
                return None
            return 10.0 ** nearest

        def _pair_matches(x: float, y: float, u: float, v: float) -> bool:
            for a, b in ((u, v), (v, u)):
                k1 = _scaled_equal(x, a)
                k2 = _scaled_equal(y, b)
                if k1 is not None and k2 is not None and abs(k1 - k2) < 1e-9:
                    return True
            return False

        def _is_shadow(d) -> bool:
            if d.rule_id == "text_overlay_tamper":
                return False
            if d.diff_type.value != "internal":
                return False
            if not d.evidence or d.a_value is None or d.h_value is None:
                return False
            key = (d.evidence[0].side.value, d.evidence[0].page)
            return any(
                _pair_matches(d.a_value, d.h_value, u, v)
                for u, v in overlay_pairs.get(key, [])
            )

        return [d for d in diffs if not _is_shadow(d)]

    async def _check_disclosure_profiles(self, profile_a, profile_h):
        """披露差异检查（基于画像）。"""
        from ahcc.check.disclosure import run_disclosure_checks_on_profiles
        return await run_disclosure_checks_on_profiles(profile_a, profile_h)

    async def _build_disclosure_coverage(self, profile_a, profile_h):
        """单边披露覆盖与跨页事件核查。"""
        from ahcc.check.coverage import build_disclosure_coverage
        return await asyncio.to_thread(build_disclosure_coverage, profile_a, profile_h)

    async def _build_report(self, job: Job) -> None:
        """报告导出 — P3 实现。"""
        from ahcc.report.excel import export_excel
        from ahcc.report.html import export_html
        from ahcc.report.pdf import export_pdf
        out_dir = settings.storage_dir / "jobs" / job.job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(export_excel, job, out_dir / "report.xlsx")
        await asyncio.to_thread(export_pdf, job, out_dir / "report.pdf")
        await asyncio.to_thread(export_html, job, out_dir / "report.html")

    def _normalize_bilingual_coverage_items(self, items: list[Any]) -> list[DisclosureCoverageItem]:
        normalized: list[DisclosureCoverageItem] = []
        for item in items or []:
            if isinstance(item, DisclosureCoverageItem):
                normalized.append(item)
                continue
            if isinstance(item, dict):
                normalized.append(self._coverage_item_from_bilingual_dict(item))
        return normalized

    def _coverage_item_from_bilingual_dict(self, item: dict[str, Any]) -> DisclosureCoverageItem:
        raw_status = str(item.get("status") or "").strip().lower()
        has_en = bool(item.get("en_page") or item.get("h_pages") or item.get("en_text"))
        if raw_status == "matched":
            status = "matched"
        elif raw_status in {"h_only"}:
            status = "h_only"
        else:
            status = "matched" if has_en and raw_status == "ambiguous" else "a_only"

        raw_category = str(item.get("category") or "").strip().lower()
        category = {
            "financial_table": "structure",
            "table": "structure",
            "note_item": "event",
            "key_fact": "event",
            "paragraph": "narrative",
        }.get(raw_category, raw_category)
        if category not in {"metric", "narrative", "structure", "event", "location", "depth_rule"}:
            category = "narrative"

        coverage_id = str(item.get("coverage_id") or item.get("unit_id") or f"bilingual:{len(str(item))}")
        zh_text = str(item.get("zh_text") or item.get("zh_section") or coverage_id)
        en_text = str(item.get("en_text") or item.get("en_section") or coverage_id)
        zh_page = self._optional_positive_int(item.get("zh_page") or item.get("a_page"))
        en_page = self._optional_positive_int(item.get("en_page") or item.get("h_page"))
        confidence = self._bounded_confidence(item.get("match_confidence", item.get("confidence", item.get("alignment_confidence", 0.0))))

        note_parts = []
        if item.get("note"):
            note_parts.append(str(item.get("note")))
        if item.get("reason"):
            note_parts.append(str(item.get("reason")))
        if raw_status and raw_status != status:
            note_parts.append(f"original_status={raw_status}")

        return DisclosureCoverageItem(
            coverage_id=coverage_id,
            category=category,
            status=status,
            topic=LocalizedString(zh=zh_text[:120], en=en_text[:120]),
            canonical_key=str(item.get("canonical_key") or coverage_id),
            a_pages=[zh_page] if zh_page else [],
            h_pages=[en_page] if en_page else [],
            a_evidence=[
                Evidence(side=ReportSide.A_SHARE, page=zh_page, snippet=zh_text[:200], section=item.get("zh_section"))
            ]
            if zh_page
            else [],
            h_evidence=[
                Evidence(side=ReportSide.H_SHARE, page=en_page, snippet=en_text[:200], section=item.get("en_section"))
            ]
            if en_page
            else [],
            match_confidence=confidence,
            note="; ".join(note_parts),
            source="h_bilingual",
        )

    def _optional_positive_int(self, value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _bounded_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(confidence, 1.0))

    def _audit_payload(self, profile) -> dict:
        doc = getattr(profile, "source_doc", None)
        audit = getattr(doc, "extraction_audit", None)
        if audit is not None:
            return audit.model_dump(mode="json")
        metadata = getattr(profile, "metadata", {}) or {}
        raw = metadata.get("extraction_audit") or {}
        return raw if isinstance(raw, dict) else {}

    def _doc_audit_payload(self, doc: ReportDocument) -> dict:
        audit = getattr(doc, "extraction_audit", None)
        if audit is not None:
            return audit.model_dump(mode="json")
        raw = (getattr(doc, "metadata", {}) or {}).get("extraction_audit") or {}
        return raw if isinstance(raw, dict) else {}

    def _doc_parser_cache_payload(self, doc: ReportDocument | None, profile=None) -> dict:
        if doc is not None:
            raw = (getattr(doc, "metadata", {}) or {}).get("parser_cache") or {}
            if isinstance(raw, dict):
                return raw
        raw = (getattr(profile, "metadata", {}) or {}).get("parser_cache") or {}
        return raw if isinstance(raw, dict) else {}

    def _file_sha256(self, file_path: str | None) -> str | None:
        if not file_path:
            return None
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return None
        try:
            import hashlib

            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"计算文件 SHA-256 失败: {file_path}: {exc}")
            return None

    def _branch_diagnostic_warnings(
        self,
        doc_a: ReportDocument | None,
        doc_h: ReportDocument | None,
        branch_diagnostics: dict[str, object],
    ) -> list[dict]:
        warnings: list[dict] = []
        if doc_a is None:
            warnings.append({
                "side": "A",
                "flag": "branch_source_doc_missing",
                "message": "A-share source_doc is unavailable; branch disclosure check was skipped.",
                "category": "extraction",
                "severity": "medium",
                "blocking": False,
            })
        if doc_h is None:
            warnings.append({
                "side": "H",
                "flag": "branch_source_doc_missing",
                "message": "H-share source_doc is unavailable; branch disclosure check was skipped.",
                "category": "extraction",
                "severity": "medium",
                "blocking": False,
            })
        if doc_a is not None and int(branch_diagnostics.get("a_branch_count") or 0) == 0:
            warnings.append({
                "side": "A",
                "flag": "branch_table_not_extracted",
                "message": "A-share branch disclosure table was not extracted; check PDF table parsing output.",
                "category": "extraction",
                "severity": "medium",
                "blocking": False,
            })
        if doc_h is not None and int(branch_diagnostics.get("h_branch_count") or 0) == 0:
            warnings.append({
                "side": "H",
                "flag": "branch_table_not_extracted",
                "message": "H-share branch disclosure table was not extracted; check PDF table parsing output.",
                "category": "extraction",
                "severity": "medium",
                "blocking": False,
            })
        return warnings

    def _collect_doc_extraction_warnings(self, doc_a: ReportDocument, doc_h: ReportDocument) -> list[dict]:
        from ahcc.parser.audit import classify_warning

        warnings: list[dict] = []
        for side, doc in (("A", doc_a), ("H", doc_h)):
            audit = self._doc_audit_payload(doc)
            flags = list(audit.get("warning_flags") or [])
            messages = list(audit.get("warnings") or [])
            detail_map = {
                item.get("flag"): item
                for item in (audit.get("engines") or {}).get("warning_details", [])
                if isinstance(item, dict)
            }
            max_len = max(len(flags), len(messages))
            for idx in range(max_len):
                flag = flags[idx] if idx < len(flags) else ""
                message = messages[idx] if idx < len(messages) else ""
                detail = dict(detail_map.get(flag) or classify_warning(flag, message))
                warnings.append({
                    "side": side,
                    "flag": flag,
                    "message": message,
                    "category": detail.get("category", "extraction"),
                    "severity": detail.get("severity", "medium"),
                    "blocking": bool(detail.get("blocking", False)),
                    "total_pages": audit.get("total_pages", 0),
                    "scanned_pages": len(audit.get("scanned_pages") or []),
                    "missing_pages": len(audit.get("missing_pages") or []),
                    "blank_pages": len(audit.get("blank_pages") or []),
                    "ocr_pages": len(audit.get("ocr_pages") or []),
                    "table_pages": len(audit.get("table_pages") or []),
                    "coverage_ratio": audit.get("coverage_ratio", 0.0),
                })
        return warnings

    def _collect_extraction_warnings(self, profile_a, profile_h) -> list[dict]:
        from ahcc.parser.audit import classify_warning

        warnings: list[dict] = []
        for side, profile in (("A", profile_a), ("H", profile_h)):
            audit = self._audit_payload(profile)
            flags = list(audit.get("warning_flags") or [])
            messages = list(audit.get("warnings") or [])
            detail_map = {
                item.get("flag"): item
                for item in (audit.get("engines") or {}).get("warning_details", [])
                if isinstance(item, dict)
            }
            max_len = max(len(flags), len(messages))
            for idx in range(max_len):
                flag = flags[idx] if idx < len(flags) else ""
                message = messages[idx] if idx < len(messages) else ""
                detail = dict(detail_map.get(flag) or classify_warning(flag, message))
                warnings.append({
                    "side": side,
                    "flag": flag,
                    "message": message,
                    "category": detail.get("category", "extraction"),
                    "severity": detail.get("severity", "medium"),
                    "blocking": bool(detail.get("blocking", False)),
                    "total_pages": audit.get("total_pages", 0),
                    "scanned_pages": len(audit.get("scanned_pages") or []),
                    "missing_pages": len(audit.get("missing_pages") or []),
                    "blank_pages": len(audit.get("blank_pages") or []),
                    "ocr_pages": len(audit.get("ocr_pages") or []),
                    "table_pages": len(audit.get("table_pages") or []),
                    "coverage_ratio": audit.get("coverage_ratio", 0.0),
                })
        return warnings

    def _emit(self, job: Job, stage: JobStatus, percent: int, message: str) -> None:
        progress = JobProgress(stage=stage, percent=percent, message=message)
        job.progress.append(progress)
        job.status = stage
        job.comparison_summary = running_progress_summary(
            job.comparison_summary,
            stage,
            percent,
            message,
            now=progress.updated_at,
        )
        logger.info(f"[{job.job_id}] {percent:>3}% [{stage}] {message}")
        if self._progress_callback:
            try:
                self._progress_callback(job)
            except Exception:  # noqa: BLE001
                logger.exception(f"[{job.job_id}] progress persistence failed")

    async def _safe_check(
        self,
        coro,
        job: Job,
        label: str,
        default: Any = None,
        warnings: list | None = None,
        percent: int | None = None,
    ) -> Any:
        """执行可选检查步骤；失败时记录 warning 并返回 default，不整单失败。"""
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[{job.job_id}] {label} 失败")
            msg = f"{label} 失败，已跳过：{exc}"
            pct = percent or (job.progress[-1].percent if job.progress else 50)
            self._emit(job, JobStatus.CHECKING, pct, msg)
            if warnings is not None:
                warnings.append({
                    "flag": f"{label.lower().replace(' ', '_').replace('（', '').replace('）', '')}_failed",
                    "message": msg,
                    "category": "auxiliary_chart" if "图表" in label else "extraction",
                    "severity": "medium",
                    "blocking": False,
                    "side": "",
                })
            return default

    def _build_bilingual_summary(
        self,
        job: Job,
        doc_zh: ReportDocument,
        doc_en: ReportDocument,
        result,
        *,
        bilingual_level: str = "fast",
        phase_timings: dict[str, float] | None = None,
        module_warnings: list | None = None,
    ) -> dict:
        from ahcc.parser.audit import EXTRACTION_ENGINE_VERSION, PARSER_VERSION

        warnings = [
            *self._collect_doc_extraction_warnings(doc_zh, doc_en),
            *(result.warnings or []),
            *(module_warnings or []),
        ]
        blocking_warnings = [item for item in warnings if item.get("blocking")]
        auxiliary_warnings = [item for item in warnings if item.get("category") == "auxiliary_chart"]
        stats = result.stats or {}
        fact_diff_count = sum(1 for d in job.diffs if d.rule_id == "bilingual_fact_mismatch")
        semantic_diff_count = sum(1 for d in job.diffs if d.rule_id == "bilingual_semantic_mismatch")
        layout_diff_count = sum(1 for d in job.diffs if str(d.rule_id or "").startswith("bilingual_section_") or str(d.rule_id or "").startswith("bilingual_table_") or d.rule_id == "bilingual_paragraph_unpaired")
        zh_parser_cache = (getattr(doc_zh, "metadata", {}) or {}).get("parser_cache") or {}
        en_parser_cache = (getattr(doc_en, "metadata", {}) or {}).get("parser_cache") or {}
        return {
            "result_version": _CURRENT_RESULT_VERSION,
            "parser_version": PARSER_VERSION,
            "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "stale_result": False,
            "check_mode": "h_bilingual",
            "bilingual_level": bilingual_level,
            "mode_label": "H股英文翻译核对",
            "side_labels": {"A": "H中文", "H": "H英文"},
            "a_fact_count": stats.get("zh_fact_count", 0),
            "h_fact_count": stats.get("en_fact_count", 0),
            "a_metric_keys": 0,
            "h_metric_keys": 0,
            "a_narrative_blocks": stats.get("zh_blocks", 0),
            "h_narrative_blocks": stats.get("en_blocks", 0),
            "paired_translation_blocks": stats.get("paired_blocks", 0),
            "translation_coverage": stats.get("translation_coverage", 0.0),
            "table_coverage": stats.get("table_coverage", 0.0),
            "semantic_coverage": stats.get("semantic_coverage", 0.0),
            "semantic_total_pairs": stats.get("semantic_total_pairs", 0),
            "semantic_reviewed_pairs": stats.get("semantic_reviewed_pairs", 0),
            "cross_currency_matched": stats.get("cross_currency_matched", 0),
            "cross_currency_mismatch": stats.get("cross_currency_mismatch", 0),
            "currency_ambiguous": stats.get("currency_ambiguous", 0),
            "table_unit_diff_pairs": stats.get("table_unit_diff_pairs", 0),
            "unpaired_zh_blocks": stats.get("unpaired_zh_blocks", 0),
            "unpaired_en_blocks": stats.get("unpaired_en_blocks", 0),
            "section_pair_count": stats.get("section_pair_count", 0),
            "section_diff_count": stats.get("section_diff_count", 0),
            "table_row_diff_count": stats.get("table_row_diff_count", 0),
            "paragraph_unpaired_count": stats.get("paragraph_unpaired_count", 0),
            "layout_diff_count": layout_diff_count,
            "real_diff_count": sum(1 for d in job.diffs if d.triage == "real"),
            "expected_diff_count": sum(1 for d in job.diffs if d.triage == "expected"),
            "unresolved_diff_count": sum(1 for d in job.diffs if d.triage == "unresolved"),
            "translation_diff_count": semantic_diff_count,
            "numeric_diff_count": fact_diff_count,
            "semantic_diff_count": semantic_diff_count,
            "coverage_count": stats.get("section_diff_count", 0) + stats.get("table_row_diff_count", 0) + stats.get("paragraph_unpaired_count", 0),
            "matched_event_count": 0,
            "event_fact_diff_count": 0,
            "total_diff_count": len(job.diffs),
            "warning_count": len(warnings),
            "blocking_warning_count": len(blocking_warnings),
            "core_warning_count": len(blocking_warnings),
            "aux_warning_count": len(auxiliary_warnings),
            "a_warning_count": sum(1 for item in warnings if item.get("side") == "A"),
            "h_warning_count": sum(1 for item in warnings if item.get("side") == "H"),
            "parser_cache_hit": bool(zh_parser_cache.get("hit")) and bool(en_parser_cache.get("hit")),
            "parser_cache": {
                "zh": zh_parser_cache,
                "en": en_parser_cache,
            },
            "phase_timings": phase_timings or {},
            "warnings": warnings,
            "a_extraction_audit": self._doc_audit_payload(doc_zh),
            "h_extraction_audit": self._doc_audit_payload(doc_en),
        }

    @staticmethod
    def _diff_scope_counts(diffs) -> dict[str, dict[str, int]]:
        triages = ("real", "unresolved", "expected")
        scopes = (
            DiffScope.CROSS_REPORT.value,
            DiffScope.A_INTERNAL.value,
            DiffScope.H_INTERNAL.value,
        )
        counts = {triage: {scope: 0 for scope in scopes} for triage in triages}
        for diff in diffs:
            triage = diff.triage if diff.triage in counts else "unresolved"
            scope_value = Orchestrator._normalized_diff_scope_value(diff)
            if scope_value not in counts[triage]:
                scope_value = DiffScope.CROSS_REPORT.value
            counts[triage][scope_value] += 1
        return counts

    @staticmethod
    def _normalized_diff_scope_value(diff) -> str:
        scope = getattr(diff, "diff_scope", DiffScope.CROSS_REPORT)
        scope_value = scope.value if isinstance(scope, DiffScope) else str(scope or DiffScope.CROSS_REPORT.value)
        if scope_value in {DiffScope.A_INTERNAL.value, DiffScope.H_INTERNAL.value}:
            return scope_value
        diff_type = getattr(getattr(diff, "diff_type", None), "value", getattr(diff, "diff_type", ""))
        if diff_type == "internal":
            sides = {getattr(evidence.side, "value", evidence.side) for evidence in getattr(diff, "evidence", [])}
            if sides == {"A"}:
                return DiffScope.A_INTERNAL.value
            if sides == {"H"}:
                return DiffScope.H_INTERNAL.value
        return DiffScope.CROSS_REPORT.value

    def _build_comparison_summary(
        self,
        job: Job,
        profile_a,
        profile_h,
        *,
        visual_review_mode: str = "off",
        visual_ocr_status: dict | None = None,
        module_warnings: list | None = None,
    ) -> dict:
        from ahcc.parser.audit import EXTRACTION_ENGINE_VERSION, PARSER_VERSION
        from ahcc.check.branch_disclosure import branch_table_diagnostics

        a_audit = self._audit_payload(profile_a)
        h_audit = self._audit_payload(profile_h)
        doc_a = getattr(profile_a, "source_doc", None)
        doc_h = getattr(profile_h, "source_doc", None)
        a_parser_cache = self._doc_parser_cache_payload(doc_a, profile_a)
        h_parser_cache = self._doc_parser_cache_payload(doc_h, profile_h)
        # 分支机构诊断优先取主 pipeline 里 _check_branch 用轻量 fitz doc 算出的结果
        # （不依赖 profile.source_doc 是否解析成功）；该检查被禁用/失败时才退回旧路径。
        branch_diagnostics = self._branch_diagnostics or branch_table_diagnostics(doc_a, doc_h, job.diffs)
        branch_warnings = self._branch_diagnostic_warnings(doc_a, doc_h, branch_diagnostics)
        warnings = [
            *self._collect_extraction_warnings(profile_a, profile_h),
            *branch_warnings,
            *(module_warnings or []),
        ]
        blocking_warnings = [item for item in warnings if item.get("blocking")]
        auxiliary_warnings = [item for item in warnings if item.get("category") == "auxiliary_chart"]
        diff_scope_counts = self._diff_scope_counts(job.diffs)
        return {
            "result_version": _CURRENT_RESULT_VERSION,
            "parser_version": PARSER_VERSION,
            "extraction_engine_version": EXTRACTION_ENGINE_VERSION,
            "stale_result": False,
            "check_mode": "ah",
            "visual_review_mode": visual_review_mode,
            "visual_ocr_status": visual_ocr_status or {"mode": visual_review_mode},
            "mode_label": "A+H股报告检查",
            "side_labels": {"A": "A", "H": "H"},
            "a_file_sha256": self._file_sha256(job.a_file),
            "h_file_sha256": self._file_sha256(job.h_file),
            "a_fact_count": sum(len(occ.all_occurrences) for occ in profile_a.metrics),
            "h_fact_count": sum(len(occ.all_occurrences) for occ in profile_h.metrics),
            "a_metric_keys": len(profile_a.metrics),
            "h_metric_keys": len(profile_h.metrics),
            "a_narrative_blocks": len(profile_a.narratives),
            "h_narrative_blocks": len(profile_h.narratives),
            "real_diff_count": sum(1 for d in job.diffs if d.triage == "real"),
            "expected_diff_count": sum(1 for d in job.diffs if d.triage == "expected"),
            "unresolved_diff_count": sum(1 for d in job.diffs if d.triage == "unresolved"),
            "unresolved_candidate_count": sum(1 for d in job.diffs if d.rule_id == "low_confidence_candidate"),
            "currency_converted_match_count": sum(1 for d in job.diffs if d.rule_id == "currency_converted_match"),
            "context_mismatch_count": sum(1 for d in job.diffs if d.rule_id == "context_mismatch"),
            "llm_semantic_review_count": sum(1 for d in job.diffs if d.rule_id == "llm_semantic_review"),
            "internal_inconsistency_count": sum(1 for d in job.diffs if d.diff_type.value == "internal"),
            "key_metric_exact_diff_count": sum(1 for d in job.diffs if d.rule_id == "key_metric_exact_mismatch"),
            "visual_text_layer_mismatch_count": sum(1 for d in job.diffs if d.rule_id == "visual_text_layer_mismatch"),
            "text_overlay_tamper_count": sum(1 for d in job.diffs if d.rule_id == "text_overlay_tamper"),
            "internal_event_diff_count": sum(1 for d in job.diffs if d.rule_id == "event_internal_fact_match"),
            "diff_scope_counts": diff_scope_counts,
            "cross_report_diff_count": sum(
                diff_scope_counts[triage][DiffScope.CROSS_REPORT.value]
                for triage in ("real", "unresolved", "expected")
            ),
            "a_internal_diff_count": sum(
                diff_scope_counts[triage][DiffScope.A_INTERNAL.value]
                for triage in ("real", "unresolved", "expected")
            ),
            "h_internal_diff_count": sum(
                diff_scope_counts[triage][DiffScope.H_INTERNAL.value]
                for triage in ("real", "unresolved", "expected")
            ),
            "coverage_count": len(job.coverage_items),
            "matched_event_count": sum(1 for item in job.coverage_items if item.category == "event" and item.status == "matched"),
            "a_only_count": sum(1 for item in job.coverage_items if item.status == "a_only"),
            "h_only_count": sum(1 for item in job.coverage_items if item.status == "h_only"),
            "event_fact_diff_count": sum(1 for d in job.diffs if d.rule_id == "event_fact_match"),
            "total_diff_count": len(job.diffs),
            "warning_count": len(warnings),
            "blocking_warning_count": len(blocking_warnings),
            "core_warning_count": len(blocking_warnings),
            "aux_warning_count": len(auxiliary_warnings),
            "a_warning_count": sum(1 for item in warnings if item.get("side") == "A"),
            "h_warning_count": sum(1 for item in warnings if item.get("side") == "H"),
            "parser_cache_hit": bool(a_parser_cache.get("hit")) and bool(h_parser_cache.get("hit")),
            "parser_cache": {
                "a": a_parser_cache,
                "h": h_parser_cache,
            },
            **branch_diagnostics,
            "warnings": warnings,
            "a_extraction_audit": a_audit,
            "h_extraction_audit": h_audit,
        }

    def _visual_ocr_warning(self, visual_ocr_status: dict | None) -> dict | None:
        if not visual_ocr_status:
            return None
        reason = visual_ocr_status.get("skipped_reason")
        timed_out = any(
            bool(side.get("timed_out"))
            for side in (visual_ocr_status.get("sides") or {}).values()
            if isinstance(side, dict)
        )
        if not reason and not timed_out:
            return None
        if reason == "runtime_ocr_disabled":
            message = "Runtime visual OCR is disabled for completion-first review; text/table layers were still checked."
            severity = "low"
        elif reason == "easyocr_large_pdf":
            message = "Runtime visual OCR was skipped because only EasyOCR is available and the PDF is large."
            severity = "medium"
        elif timed_out:
            message = "Runtime visual OCR reached its page/time budget; partial OCR results were retained."
            severity = "medium"
        else:
            message = f"Runtime visual OCR was skipped: {reason}"
            severity = "medium"
        return {
            "side": "",
            "flag": "visual_ocr_skipped" if reason else "visual_ocr_budget_exhausted",
            "message": message,
            "category": "visual_ocr",
            "severity": severity,
            "blocking": False,
        }
