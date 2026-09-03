# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added — 核查工程能力升级：表格列维度结构化（行×列二维坐标系）

把「横坐标表头」（列维度）从临时解析的字符串升级为与行维度同等的结构化一等
公民：每个数值单元格携带 (行标签, 列键) 二维坐标，列键含期间(到月日)/口径/
值种类/单位；所有比对通道按列键对齐。以 e5a15ac1（光大银行 2025 A/H）为
验收样本：

- 新增 `ahcc/table/` 模块（headers/semantics/compat/models）：表头行检测
  （免疫 is_header 关键词污染）、多级表头展开、colspan 左继承、列头语义
  归一化、`pairable` 兼容判定 —— 列键明确才硬否决，列键缺失永远宽松回退。
- A 股快速路径（文本层重建）：表头行保留（旧版注释写保留、代码整行丢弃 —
  流动性覆盖率跨期假差异的根因）；期间碎片分类（"2025 年"拆词、日期词≠
  数值词）；子表切分（多表页/多节页列几何不再互染，光大 p20 三表、p17 五节）；
  单位后缀贴回（"-0.06 个百分点"）；`_parse_number` 支持前导正号；
  监管指标页（资本充足率/流动性覆盖率等）进财务页预筛。
- 消灭五组假差异：流动性覆盖率 Q4 列 vs Q2 列（期间到月日硬门槛）、每股收益
  0.58 vs 增减%列 7.94（值种类）、负债合计⊂未折现租赁负债合计（子串防护）、
  资产总计附注页候选（同口径精确匹配优先降级留痕）、利息净收入"1.53 个
  百分点"（叙述值 kind 标记）。40 条分支机构真实检出一条不少。
- LLM 复核链路修复：e5a15ac1 全程 0 次 LLM 的三层根因（词表过窄/列键缺失
  永不触发/计数语义失真）；高危且列键不完整强制核验（预算 8 次，DeepSeek
  并发 ≤3）；payload 补列头/行标签字段（prompt 一直要求、payload 一直没给）。
- 整列错乱聚合呈现：分支表 40 条散点聚合为 1 条结构性发现（多重集相等/锚点
  稳定/数值列整体重排）+ 折叠明细，检出数不变；comparison_summary 新增
  structural_groups 与 llm_semantic_review_call_count；UI 按组折叠展开；
  Excel 增加「列口径」列。
- 通道升级：chart 取数列键驱动（期间匹配优先）；table_row_twin role 语义化
  （跨侧列序不同可配，本期/上期互换可判）+ 表头行跳过改读 header_row_indices
  （is_header 污染丢行修复）；bilingual 列表头不再丢弃（header_context 参与
  行配对，期间错配扣分）。
- 证据链：表级/单元格级 bbox 透传（pdfplumber find_tables + 快速路径词坐标，
  零额外解析成本）；Evidence 携带 table_id/cell_ref。
- 评估：注入方式新增 `period_swap`（期间列互换、表头不动），验证列键期间
  硬门槛与 twin 语义 role 的检出能力。
- 版本：result_version 19、extraction engine 2026-09-01.14、h_pdf 缓存 v4。
- 回滚开关：fast_path_header_rows / column_key_hard_gate /
  internal_substring_label_guard / key_metric_llm_review_max_calls。

### Fixed

- 恢复光大银行分支机构 40 处 A/H 不一致的检出。此前的「行错位自检」把
  「H 侧数值出现在 A 侧另一个名称下」一律判为解析错位，整表否决。现改为按
  **同行共位锚点**（机构数量 + 办公地址）判别：锚点跟着数值移动才是整行错位；
  锚点原位不动而数值跨名称命中，是数值列被整体打乱的真实差异。
- 跨规则 real 去重不再合并同一规则内部的条目。排列型篡改下每条差异的 h_value
  必然等于另一条的 a_value 且页码相邻，同规则合并会把 40 条砍成 18 条。
- `table_row_twin` 的低锚点/超量两道熔断增加排列检测逃生口 —— 两道闸都假设
  篡改稀疏，整列被打乱恰好违反该假设。
- 评估口径：新增 `KNOWN_TRUE_POSITIVES`，把已核实为样本自带的真实差异从
  FP 上界与 hard FP 中扣除并单列。此前「干净对 FP=0」是一个奖励删检出的指标。
- 整站无响应（`_NUMERIC_REBUILD_FLOOR` 没盖住实际数据）。上一版把 floor 定在
  17 并用 `list_jobs(limit=10)` 验证「历史 0.09s」，但前端 `loadHistory` 请求的
  是 `limit=30`，而那 30 条里有 16 条停在 v16 —— 正好比 floor 低一级，每次读取
  仍全量重跑数值检查。实测 `limit=30` 冷启动 188.20s；history 每 2.5 秒轮询一
  次，裸 `lru_cache` 只在算完后才写缓存、不合并并发调用，几十份同样的全量重算
  一起抢 GIL，静态文件要 1.5 秒、`/health` 28 秒、页面打不开。两道修复：
  （1）新增 `scripts/migrate_legacy_results.py`，把 < floor 的存量结果一次性
  离线升级并**写回库**（summary/diffs/coverage_items 三处），原版本号记入
  `upgraded_from_result_version` 留痕；此后读取永远命中快路径，重启不再重付。
  （2）`_load_current_numeric_diffs` 改为 per-job 锁 + 双检记忆化，同一任务
  同一时刻至多算一份。
  验证：真实库 17 个任务全部迁移，逐任务按内容键比对差异集合 **0 丢失 0 新增**
  （diff_id 每次重算都会变，不是稳定键，故用 rule_id/canonical_key/取值比对）；
  `list_jobs(limit=30)` 188.20s → 0.074s；服务 2 分钟 CPU 257s → 1.7s、
  常驻内存 628MB → 103MB、数值重算 0 次；`/app` 1.5s → 0.003s、
  `/health` 28s → 0.003s。

## [0.1.0] - 2026-06-25

### Added

- Initial open-source release of A+H Consistency Checker (AHCC).
- Core pipeline: PDF/HTML parsing, metric/narrative extraction, A/H alignment,
  numeric check, rule engine, standard reasoning with RAG, chart cross-check.
- FastAPI backend with static HTML frontend (`ui/static/index.html`).
- Report exports: Excel, PDF, Word working paper, pitch PPT.
- Knowledge base with 15 CAS/IFRS standards and bilingual glossary.
- YAML rule definitions for numeric equal, cross-check, and disclosure checks.
