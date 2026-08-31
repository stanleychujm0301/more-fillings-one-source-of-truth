# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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

## [0.1.0] - 2026-06-25

### Added

- Initial open-source release of A+H Consistency Checker (AHCC).
- Core pipeline: PDF/HTML parsing, metric/narrative extraction, A/H alignment,
  numeric check, rule engine, standard reasoning with RAG, chart cross-check.
- FastAPI backend with static HTML frontend (`ui/static/index.html`).
- Report exports: Excel, PDF, Word working paper, pitch PPT.
- Knowledge base with 15 CAS/IFRS standards and bilingual glossary.
- YAML rule definitions for numeric equal, cross-check, and disclosure checks.
