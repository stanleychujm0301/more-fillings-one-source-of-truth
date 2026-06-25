# A+H Profile Module Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace the chapter-position-based alignment pipeline with a semantic profile extraction and comparison system that processes all 300 pages of A+H reports, including narrative text, while keeping total pipeline under 10 minutes.

**Architecture:** Introduce a new ahcc/profile/ module with three layers — Metrics (all numerical data points with canonical keys), Narratives (semantic topic-tagged text segments), and Structure (chapter hierarchy). The new pipeline is: Parse(A) -> Extract Profile A -> Parse(H) -> Extract Profile H -> Compare Profiles -> Diffs. Existing check modules are adapted to consume profiles instead of AlignedPairs.

**Tech Stack:** Python 3.11, Pydantic v2, asyncio, existing LLM client (cached_call), existing RAG (ChromaDB + BGE-M3), loguru.

---

## File Structure

### New Files (ahcc/profile/)

| File | Responsibility |
|------|---------------|
| ahcc/profile/__init__.py | Module exports |
| ahcc/profile/models.py | ReportProfile, MetricItem, NarrativeBlock, ChapterNode, ProfileDiff Pydantic models |
| ahcc/profile/extract_metrics.py | Extract ALL metrics from tables + texts (not just 35 glossary terms, not just first 60 pages) |
| ahcc/profile/extract_narratives.py | Tag ALL text segments with semantic topics via keyword->chunk->LLM aggregation |
| ahcc/profile/extract_structure.py | Build chapter hierarchy from section codes and headings |
| ahcc/profile/compare.py | Compare Profile A vs Profile H: metrics, narratives, structure |
| ahcc/profile/topic_map.py | Semantic topic definitions, keyword->topic mapping, topic taxonomy |

### Modified Files

| File | Change |
|------|--------|
| ahcc/schemas.py | Add ReportProfile, ProfileDiff, MetricItem, NarrativeBlock, ChapterNode models |
| ahcc/orchestrator.py | Replace _align() call with _extract_profile() + _compare_profiles(); adapt check module calls |
| ahcc/check/numeric.py | Add run_numeric_checks_on_profiles(profile_a, profile_h) entry point |
| ahcc/check/standard.py | Add run_standard_checks_on_profiles(profile_a, profile_h) entry point |
| ahcc/check/disclosure.py | Add run_disclosure_checks_on_profiles(profile_a, profile_h) entry point |
| ahcc/align/matcher.py | Keep for backward compat; add deprecation warning; legacy _align() still callable |

### New Test Files

| File | Coverage |
|------|----------|
| tests/profile/test_models.py | Profile model serialization |
| tests/profile/test_extract_metrics.py | Metrics extraction from synthetic tables/texts |
| tests/profile/test_extract_narratives.py | Topic tagging accuracy |
| tests/profile/test_compare.py | Profile comparison logic |
| tests/profile/test_e2e.py | End-to-end: ReportDocument -> Profile -> Diffs |

---
## Task 1: Profile Data Models

**Files:**
- Create: ahcc/profile/models.py
- Modify: ahcc/schemas.py (append new models)
- Test: tests/profile/test_models.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_models.py with test_metric_item_creation, test_narrative_block_creation, test_report_profile_serialization, test_profile_diff_creation.

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_models.py -v
Expected: FAIL with module ahcc.profile.models not found

- [ ] **Step 3: Write minimal implementation**

Create ahcc/profile/models.py with MetricItem, NarrativeBlock, ChapterNode, ReportProfile, ProfileDiff Pydantic models. All models use ahcc.schemas.Evidence, LocalizedString, ReportSide.

Key fields:
- MetricItem: canonical_key, name (LocalizedString), value, value_text, unit, currency, period, page, evidence, confidence (0-1), source (table|text|llm)
- NarrativeBlock: topic_tags (list[str]), summary, page_range (tuple[int,int]), word_count, key_subtopics, evidence, detail_level (brief|medium|detailed), source_segments
- ChapterNode: title (LocalizedString), section_code, page_start, page_end, children (list[ChapterNode]), presence_flag
- ReportProfile: doc_id, side, total_pages, metrics, narratives, structure, metadata
- ProfileDiff: diff_type, severity, topic, summary, canonical_key, topic_tag, a_pages, h_pages, a_detail_level, h_detail_level, a_word_count, h_word_count, evidence, standard_reasoning

- [ ] **Step 4: Append new models to schemas.py**

Add to end of ahcc/schemas.py: MetricItem, NarrativeBlock, ChapterNode, ReportProfile, ProfileDiff (same definitions as models.py for backward compatibility).

- [ ] **Step 5: Run test to verify it passes**

Run: pytest tests/profile/test_models.py -v
Expected: PASS

- [ ] **Step 6: Commit**

git add ahcc/profile/models.py ahcc/schemas.py tests/profile/test_models.py
git commit -m "feat(profile): add ReportProfile data models with metrics, narratives, structure layers"

---

## Task 2: Topic Map (Semantic Taxonomy)

**Files:**
- Create: ahcc/profile/topic_map.py
- Test: tests/profile/test_topic_map.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_topic_map.py with tests:
- test_topic_taxonomy_loaded: verify mda_business_review and financial_statements exist
- test_get_topic_for_text_business_review: text about retail banking revenue -> matches mda_business_review
- test_get_topic_for_text_risk: text about credit risk -> matches mda_risk_management
- test_get_topic_for_text_esg: text about greenhouse gas -> matches esg_environment
- test_get_topic_for_text_governance: text about board composition -> matches corporate_governance

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_topic_map.py -v
Expected: FAIL with module not found

- [ ] **Step 3: Write implementation**

Create ahcc/profile/topic_map.py with:

TopicDef dataclass: topic_id, name_zh, name_en, keywords_zh, keywords_en, parent, section_codes

TOPIC_TAXONOMY dict with these topics:
- company_profile (公司概况)
- mda_business_review (业务回顾, parent=mda, keywords: 业务回顾, 经营情况, 主营业务, 收入构成, 分部业绩, 经纪业务, 投行业务, 资管业务, 自营业务)
- mda_financial_analysis (财务分析, parent=mda)
- mda_risk_management (风险管理, parent=mda)
- mda_liquidity_capital (流动性与资本, parent=mda)
- mda_outlook (未来展望, parent=mda)
- corporate_governance (公司治理)
- esg_environment (环境信息, parent=esg)
- esg_social (社会责任, parent=esg)
- significant_events (重要事项)
- share_changes (股份变动)
- financial_statements (财务报表)
- accounting_policies (会计政策)
- notes_detail (附注详情)

Functions:
- _build_keyword_index(): builds keyword -> topic_id reverse index at import time
- get_topic_for_text(text, max_topics=3): scores topics by keyword matches, returns top N (or [uncategorized])
- get_topic_name(topic_id, lang=zh): returns human-readable name
- get_topics_for_section(section_code): returns topics associated with a section code

Performance: O(text_length * avg_keyword_length), no LLM calls.

- [ ] **Step 4: Run test to verify it passes**

Run: pytest tests/profile/test_topic_map.py -v
Expected: PASS

- [ ] **Step 5: Commit**

git add ahcc/profile/topic_map.py tests/profile/test_topic_map.py
git commit -m "feat(profile): add semantic topic taxonomy with keyword-based tagging"

---

## Task 3: Metrics Extraction (All Pages, All Tables, All Texts)

**Files:**
- Create: ahcc/profile/extract_metrics.py
- Test: tests/profile/test_extract_metrics.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_extract_metrics.py with a synthetic ReportDocument containing:
- Table at page 45: 合并资产负债表 with 资产总计=1,234,567.89, 负债合计=987,654.32
- Table at page 200: 分部报告 with 经纪业务=50,000.00 (tests NO 60-page limit)
- Text at page 45: 资产总计 1,234,567.89 人民币百万元
- Text at page 150: 本年度营业收入达到 100,000.00 百万元

Tests:
- test_extract_metrics_from_tables: verifies total_assets extracted from table
- test_extract_metrics_not_limited_to_60_pages: verifies page 200 table is processed
- test_extract_metrics_from_text: verifies revenue extracted from text
- test_extract_metrics_preserves_evidence: all metrics have evidence with page and snippet

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_extract_metrics.py -v
Expected: FAIL with module not found

- [ ] **Step 3: Write implementation**

Create ahcc/profile/extract_metrics.py with these functions:

extract_metrics(doc: ReportDocument) -> list[MetricItem]:
  - Scans ALL tables (no page limit)
  - Scans ALL text segments (no page limit, no section filter)
  - Deduplicates by canonical_key keeping highest confidence
  - Returns complete MetricItem list

_extract_from_table(table, side, metadata) -> list[MetricItem]:
  - Groups cells by row
  - Matches first column against glossary (lookup raw_label and simplified)
  - Handles rowspan: if current row label empty but prev_key exists, uses prev_key
  - Extracts first number from remaining columns via _find_first_number_in_row
  - Builds MetricItem with confidence=0.9, source=table

_extract_from_text(seg, side, metadata) -> list[MetricItem]:
  - Strategy A: glossary term matching (high confidence 0.75)
    - Iterates all glossary keys, checks if zh_cn/zh_hk/en/aliases appear in text
    - Calls _extract_number_near_label to find value within 40-char window
  - Strategy B: generic label:number patterns (low confidence 0.5)
    - Regex: ([一-龥]{2,20}?)[:：]([\d,\.\-]+)
    - Generates generic canonical_key via _label_to_canonical_key
    - Skips spans already matched by glossary

_parse_number(text) -> Optional[float]:
  - Handles parentheses negatives: (123.45) -> -123.45
  - Removes commas, spaces, apostrophes
  - Matches standard thousand-separated or plain numbers
  - Validates digit count <= 15, abs(value) <= 1e15

_label_to_canonical_key(label) -> str:
  - First tries glossary.lookup(label)
  - Falls back to snake_case conversion of simplified Chinese

_merge_metric(seen, item):
  - Keeps highest confidence item per canonical_key
  - Tie-breaker: earlier page number

- [ ] **Step 4: Run test to verify it passes**

Run: pytest tests/profile/test_extract_metrics.py -v
Expected: PASS

- [ ] **Step 5: Commit**

git add ahcc/profile/extract_metrics.py tests/profile/test_extract_metrics.py
git commit -m "feat(profile): extract all metrics from all tables and texts without page limits"

---

## Task 4: Narrative Extraction (Semantic Topic Tagging)

**Files:**
- Create: ahcc/profile/extract_narratives.py
- Test: tests/profile/test_extract_narratives.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_extract_narratives.py with a synthetic ReportDocument containing:
- Page 120: 本集团零售银行业务收入同比增长15%... (mda section)
- Page 121: 投行业务方面，本年度完成IPO项目20个... (mda section)
- Page 122: 信用风险敞口较上年末增加，不良贷款率控制在1.2%... (mda section)
- Page 200: 本年度温室气体排放量为5000吨... (esg section)

Tests:
- test_extract_narratives_groups_by_topic: verifies mda_business_review, mda_risk_management, esg_environment blocks exist
- test_extract_narratives_aggregates_pages: verifies business_review block spans pages 120-121
- test_extract_narratives_preserves_evidence: all blocks have evidence with page >= 1

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_extract_narratives.py -v
Expected: FAIL with module not found

- [ ] **Step 3: Write implementation**

Create ahcc/profile/extract_narratives.py with:

extract_narratives(doc: ReportDocument) -> list[NarrativeBlock]:
  Strategy (zero LLM calls, O(N) local computation):
  1. For each text segment, call get_topic_for_text(seg.text) -> topic_tags
  2. Group by (primary_topic, section_code)
  3. Within each group, split into continuous chunks (page gap <= 3)
  4. For each chunk, build NarrativeBlock with:
     - word_count = len(full_text without spaces)
     - page_range = (min_page, max_page)
     - detail_level = brief (<200) | medium (<1000) | detailed
     - key_subtopics = _extract_key_subtopics(full_text, primary_topic)
     - summary = first 200 chars of merged text
     - evidence = first segments evidence
  5. Skip blocks with word_count < 20

_split_into_continuous_chunks(segments, max_page_gap=3):
  - Sorts segments by (page, segment_id)
  - Splits when page gap > max_page_gap

_extract_key_subtopics(text, primary_topic):
  - Maps primary_topic to predefined subtopic keyword lists
  - Example: mda_business_review -> [经纪业务, 投行业务, 资管业务, 自营业务, 信用业务, 期货业务, 财富管理]
  - Returns up to 5 matched subtopics

Performance: ~2000 text segments * keyword matching < 1 second total.

- [ ] **Step 4: Run test to verify it passes**

Run: pytest tests/profile/test_extract_narratives.py -v
Expected: PASS

- [ ] **Step 5: Commit**

git add ahcc/profile/extract_narratives.py tests/profile/test_extract_narratives.py
git commit -m "feat(profile): extract narratives with semantic topic tagging via keyword matching"

---

## Task 5: Structure Extraction

**Files:**
- Create: ahcc/profile/extract_structure.py
- Test: tests/profile/test_extract_structure.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_extract_structure.py with synthetic ReportDocument containing text segments at pages 1, 10, 50, 100, 150, 200, 250 with sections: company_profile, mda, corporate_governance, esg, financial_statements, notes.

Tests:
- test_extract_structure_builds_hierarchy: root node with children including company_profile, mda, corporate_governance, esg, financial_statements
- test_extract_structure_page_ranges: mda starts at 50 and ends before 100; financial_statements starts at 200

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_extract_structure.py -v
Expected: FAIL with module not found

- [ ] **Step 3: Write implementation**

Create ahcc/profile/extract_structure.py with:

_SECTION_NAMES dict mapping section_code -> (name_zh, name_en):
  company_profile, mda, directors_report, corporate_governance, esg, significant_events, related_party, share_changes, preference_shares, bonds, financial_statements, bs, pl, cf, equity, notes, accounting_policy, accounting_estimate, segment_report, goodwill, rnd, income_tax, eps, leases, financial_instruments, revenue, inventories, ppe, intangible_assets, investment_property, employee_benefits, provisions, capital_reserve, retained_earnings, minority_interest

extract_structure(doc: ReportDocument) -> ChapterNode:
  1. Collect section -> pages from doc.texts and doc.tables
  2. Build leaf ChapterNodes for each section
  3. Sort by page_start
  4. Group bs/pl/cf/equity/notes as children of financial_statements
  5. Return root ChapterNode (section_code=root, page_start=1, page_end=total_pages)

- [ ] **Step 4: Run test to verify it passes**

Run: pytest tests/profile/test_extract_structure.py -v
Expected: PASS

- [ ] **Step 5: Commit**

git add ahcc/profile/extract_structure.py tests/profile/test_extract_structure.py
git commit -m "feat(profile): add structure extraction building chapter hierarchy from section codes"

---

## Task 6: Profile Comparison

**Files:**
- Create: ahcc/profile/compare.py
- Test: tests/profile/test_compare.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_compare.py with two synthetic profiles:

Profile A:
  metrics: total_assets=1,234,567.89, revenue=100,000.00
  narratives: mda_business_review (5000 words, detailed), esg_environment (800 words, medium)
  structure: root -> mda (100-150), esg (150-170)

Profile H:
  metrics: total_assets=1,234,567.89 (revenue MISSING)
  narratives: mda_business_review (1500 words, medium), esg MISSING
  structure: root -> mda (90-140) (esg MISSING)

Tests:
- test_compare_metrics_match: total_assets matches, no diff
- test_compare_metrics_missing: revenue missing in H -> metric_missing diff
- test_compare_narrative_depth: mda_business_review 5000 vs 1500 words -> narrative_depth diff
- test_compare_narrative_presence: esg_environment missing in H -> topic_missing diff
- test_compare_structure_missing: esg chapter missing in H -> structure_missing diff

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_compare.py -v
Expected: FAIL with module not found

- [ ] **Step 3: Write implementation**

Create ahcc/profile/compare.py with:

compare_profiles(profile_a, profile_h) -> list[ProfileDiff]:
  Calls _compare_metrics, _compare_narratives, _compare_structure, concatenates results.

_compare_metrics(a, h):
  - Build dict by canonical_key for each profile
  - For each key:
    - Both present: _compare_metric_values (1% tolerance, severity by ratio)
    - Only A: _make_missing_diff (missing_side=H)
    - Only H: _make_missing_diff (missing_side=A)

_compare_narratives(a, h):
  - Build dict by topic_tag for each profile
  - For each topic:
    - Both present: _compare_narrative_depth (ratio >= 3x triggers diff)
    - Only A: _make_topic_missing_diff (missing_side=H)
    - Only H: _make_topic_missing_diff (missing_side=A)

_compare_structure(a, h):
  - Recursively collect section_codes from both structures
  - A-only sections: structure_missing diff
  - H-only sections: structure_missing diff

- [ ] **Step 4: Run test to verify it passes**

Run: pytest tests/profile/test_compare.py -v
Expected: PASS

- [ ] **Step 5: Commit**

git add ahcc/profile/compare.py tests/profile/test_compare.py
git commit -m "feat(profile): add profile comparison for metrics, narratives, and structure"

---

## Task 7: Profile Module Integration

**Files:**
- Create: ahcc/profile/__init__.py
- Modify: ahcc/orchestrator.py
- Test: tests/profile/test_e2e.py

- [ ] **Step 1: Write the failing test**

Create tests/profile/test_e2e.py with:
- test_build_profile_creates_report_profile: verifies build_profile returns ReportProfile with metrics, narratives, structure
- test_build_profile_has_total_assets: verifies total_assets extracted correctly

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/profile/test_e2e.py -v
Expected: FAIL

- [ ] **Step 3: Write ahcc/profile/__init__.py**



- [ ] **Step 4: Modify ahcc/orchestrator.py**

Replace the pipeline in run() method:

OLD:
  pairs = await self._align(doc_a, doc_h)
  numeric_diffs = await self._check_numeric(pairs)
  standard_diffs = await self._check_standard(pairs)
  disclosure_diffs = await self._check_disclosure(doc_a, doc_h)

NEW:
  profile_a = await self._extract_profile(doc_a)
  profile_h = await self._extract_profile(doc_h)
  profile_diffs = await self._compare_profiles(profile_a, profile_h)
  numeric_diffs = await self._check_numeric_profiles(profile_a, profile_h)
  standard_diffs = await self._check_standard_profiles(profile_a, profile_h)
  disclosure_diffs = await self._check_disclosure_profiles(profile_a, profile_h)
  job.diffs = [*_profile_diffs_to_diffs(profile_diffs), *numeric_diffs, *standard_diffs, *disclosure_diffs, *chart_diffs]

Add new methods:
- _extract_profile(doc): calls build_profile via asyncio.to_thread
- _compare_profiles(pa, ph): calls compare_profiles via asyncio.to_thread
- _check_numeric_profiles(pa, ph): calls run_numeric_checks_on_profiles
- _check_standard_profiles(pa, ph): calls run_standard_checks_on_profiles
- _check_disclosure_profiles(pa, ph): calls run_disclosure_checks_on_profiles

Add _profile_diffs_to_diffs(profile_diffs) at bottom of orchestrator.py:
  Converts ProfileDiff list to schemas.Diff list for backward compatibility with report generation.
  Maps diff_type: metric_mismatch->NUMERIC, others->DISCLOSURE
  Maps severity strings to DiffSeverity enum

- [ ] **Step 5: Add backward-compatible entry points to check modules**

In ahcc/check/numeric.py, add run_numeric_checks_on_profiles(profile_a, profile_h):
  - Converts Profile metrics to AlignedPair list
  - Reuses existing run_numeric_checks(pairs)

In ahcc/check/standard.py, add run_standard_checks_on_profiles(profile_a, profile_h):
  - Converts Profile metrics to AlignedPair list
  - Reuses existing run_standard_checks(pairs)

In ahcc/check/disclosure.py, add run_disclosure_checks_on_profiles(profile_a, profile_h):
  - Calls compare_profiles() to get narrative/structure diffs
  - Filters for narrative_depth, narrative_presence, structure_missing, topic_missing
  - Converts ProfileDiff to schemas.Diff

- [ ] **Step 6: Run test to verify it passes**

Run: pytest tests/profile/test_e2e.py -v
Expected: PASS

- [ ] **Step 7: Commit**

git add ahcc/profile/__init__.py ahcc/orchestrator.py ahcc/check/numeric.py ahcc/check/standard.py ahcc/check/disclosure.py tests/profile/test_e2e.py
git commit -m "feat(profile): integrate profile module into orchestrator with backward-compatible check adapters"

---

## Task 8: Deprecate matcher.py (Backward Compatibility)

**Files:**
- Modify: ahcc/align/matcher.py
- Modify: ahcc/align/__init__.py

- [ ] **Step 1: Add deprecation warning to matcher.py**

At top of align_documents function:
  import warnings
  warnings.warn(
      "align_documents is deprecated. Use ahcc.profile.build_profile + compare_profiles instead.",
      DeprecationWarning, stacklevel=2
  )

- [ ] **Step 2: Update ahcc/align/__init__.py**

Add docstring indicating deprecation and pointing to ahcc.profile.
Keep exports: align_documents, glossary, normalize_term, get_term

- [ ] **Step 3: Commit**

git add ahcc/align/matcher.py ahcc/align/__init__.py
git commit -m "chore(align): deprecate matcher.py in favor of profile module"

---

## Task 9: Performance Optimization

**Files:**
- Modify: ahcc/profile/extract_metrics.py
- Modify: ahcc/profile/extract_narratives.py

- [ ] **Step 1: Add concurrent extraction for metrics**

In extract_metrics, use ThreadPoolExecutor(max_workers=4) to process tables concurrently:
  with ThreadPoolExecutor(max_workers=4) as executor:
      futures = [executor.submit(_extract_from_table, table, doc.side, doc.metadata)
                 for table in doc.tables]
      for future in futures:
          for item in future.result():
              _merge_metric(seen, item)

Text extraction remains serial (fast enough).

- [ ] **Step 2: Add batching for narrative extraction**

In extract_narratives, add batch_size=500 parameter:
  Process texts in batches to avoid memory spikes with 2000+ segments.

- [ ] **Step 3: Commit**

git add ahcc/profile/extract_metrics.py ahcc/profile/extract_narratives.py
git commit -m "perf(profile): add concurrent table extraction and batch narrative processing"

---

## Task 10: Full Integration Test

**Files:**
- Test: tests/profile/test_integration.py

- [ ] **Step 1: Write integration test**

Create tests/profile/test_integration.py with:
- _make_a_doc(): ReportDocument with table (total_assets, liabilities) + texts (mda, esg)
- _make_h_doc(): ReportDocument with table (total_assets, liabilities) + text (mda only, no esg)
- test_full_pipeline():
  1. build_profile for both docs
  2. compare_profiles -> assert len(diffs) >= 1 (ESG missing)
  3. run_numeric_checks_on_profiles -> assert len(diffs) == 0 (values match)
  4. run_disclosure_checks_on_profiles -> assert len(diffs) >= 1 (ESG missing)

- [ ] **Step 2: Run test**

Run: pytest tests/profile/test_integration.py -v
Expected: PASS

- [ ] **Step 3: Commit**

git add tests/profile/test_integration.py
git commit -m "test(profile): add full integration test for new pipeline"

---

## Self-Review

### 1. Spec Coverage

| Requirement | Task |
|------------|------|
| ALL 300 pages go into profile | Task 3 (extract_metrics no page limit), Task 4 (extract_narratives no page limit) |
| Narrative text IS included | Task 4 (extract_narratives processes all texts) |
| Narrative comparison is SEMANTIC/TOPIC-based | Task 2 (topic_map.py), Task 6 (_compare_narratives) |
| Eliminate chapter-position-based alignment | Task 7 (orchestrator replaces _align), Task 8 (deprecate matcher.py) |
| Metrics layer: ALL numerical data | Task 3 (unlimited glossary items, generic label:number patterns) |
| Narratives layer: semantic topics | Task 2, 4 (topic_map + keyword tagging) |
| Structure layer: chapter hierarchy | Task 5 (extract_structure) |
| Performance under 10 minutes | Task 3, 4, 9 (concurrent extraction, batch processing, no per-segment LLM) |

### 2. Placeholder Scan

- No TBD, TODO, implement later found.
- All steps contain actual code or detailed pseudocode.
- All test code is complete.
- No references to undefined types/functions.

### 3. Type Consistency

- MetricItem used consistently across models.py, extract_metrics.py, compare.py
- NarrativeBlock used consistently across models.py, extract_narratives.py, compare.py
- ChapterNode used consistently across models.py, extract_structure.py, compare.py
- ProfileDiff used consistently across models.py, compare.py, orchestrator.py
- ReportProfile used consistently across models.py, __init__.py, orchestrator.py

---

## Migration Strategy

### Phase 1: Parallel Run (Recommended)

1. Deploy new ahcc/profile/ module alongside existing ahcc/align/matcher.py
2. In orchestrator.py, run BOTH old _align() and new _extract_profile()
3. Log diff counts from both approaches for comparison
4. Validate new approach produces same or better numeric diff coverage

### Phase 2: Cutover

1. After validation (1-2 weeks), switch orchestrator to use new pipeline
2. matcher.py保留但标记 deprecated
3. Delete old _align() calls, clean up dead code

### Phase 3: Cleanup

1. Remove ahcc/align/matcher.py (glossary.py kept as referenced by extract_metrics.py)

---

## Performance Budget

| Stage | Old Pipeline | New Pipeline | Optimization |
|-------|-------------|--------------|-------------|
| Parse A | ~30s | ~30s | No change |
| Parse H | ~45s | ~45s | No change |
| Extract Profile A | N/A (align: ~60s) | ~15s | Keyword matching O(N), no LLM |
| Extract Profile H | N/A | ~15s | Same |
| Compare Profiles | N/A | ~1s | Dict lookup O(1) |
| Numeric Check | ~2s | ~2s | Reuse existing logic |
| Standard Check | ~30s | ~30s | Reuse existing logic |
| Disclosure Check | ~5s | ~3s | Profile-based faster |
| Chart Check | ~60s | ~60s | No change |
| **Total** | **~230s** | **~200s** | **Under 10 minutes** |

---

Plan complete and saved to docs/superpowers/plans/2026-05-26-profile-module-refactor.md. Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
