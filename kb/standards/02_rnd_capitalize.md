---
topic_key: rnd_capitalize
topic_zh: 研发支出资本化
topic_en: R&D capitalization
cas_code: CAS 6
ifrs_code: IAS 38
hkfrs_code: HKAS 38
keywords:
  - 研发
  - R&D
  - 研发支出
  - research and development
  - 资本化
  - capitalization
  - 无形资产
  - intangible assets
  - 开发阶段
  - development phase
expected_difference: true
severity_when_unexpected: medium
---

# 差异性质

CAS 6 与 IAS 38 在研发支出"研究 vs 开发"两阶段的划分上**条文趋同**，但实务中存在差异：
1. CAS 对**开发阶段资本化时点**要求更严格，要求同时满足"技术可行性、出售/使用意图、未来经济利益、资源支持、成本可靠计量"等条件
2. IFRS 实务允许更早资本化（满足开发阶段判定即可开始资本化）
3. 因此 **A 股年报中"开发支出"或"无形资产 — 内部开发"金额通常 ≤ H 股年报中对应金额**

# CAS 条款

> 财政部《企业会计准则第 6 号 — 无形资产》

- 第 7 条：企业内部研究开发项目的支出，应当区分研究阶段支出与开发阶段支出。
- 第 8 条：研究阶段的支出，应当于发生时计入当期损益。
- 第 9 条：开发阶段的支出，同时满足下列条件的，才能确认为无形资产：
  - （一）完成该无形资产以使其能够使用或出售在技术上具有可行性；
  - （二）具有完成该无形资产并使用或出售的意图；
  - （三）无形资产产生经济利益的方式；
  - （四）有足够的技术、财务资源和其他资源支持；
  - （五）归属于该无形资产开发阶段的支出能够可靠地计量。

# IFRS / HKFRS 条款

> IAS 38 / HKAS 38 — Intangible Assets

- Paragraph 54: No intangible asset arising from research (or from the research phase of an internal project) shall be recognised. Expenditure on research shall be recognised as an expense when it is incurred.
- Paragraph 57: An intangible asset arising from development shall be recognised if, and only if, an entity can demonstrate all of the following:
  - (a) the technical feasibility of completing the intangible asset...
  - (b) its intention to complete the intangible asset and use or sell it;
  - (c) its ability to use or sell the intangible asset;
  - (d) how the intangible asset will generate probable future economic benefits;
  - (e) the availability of adequate technical, financial and other resources...
  - (f) its ability to measure reliably the expenditure...

# 是否符合预期差异

**预期差异类型**：
- A 股开发支出资本化金额 ≤ H 股开发支出资本化金额（差异通常在 0~30% 范围内）
- 当期研发费用费用化金额：A 股 ≥ H 股（与资本化此消彼长）
- 累计资本化余额（无形资产中"内部开发"明细）：A 股 ≤ H 股

**判定规则**：
- 若 A 股资本化 < H 股资本化，差异 ≤ 30% → **符合预期**（confidence ≥ 0.85）
- 若 A 股资本化 > H 股资本化 → **不符合预期**（违反一般实务方向）
- 若 A 股资本化 < H 股资本化，差异 > 30% → 标 MEDIUM，提示审计师重点关注技术可行性判定时点的差异

# 典型差异表现

- **比亚迪（002594 / 1211）**：高研发投入企业，A/H 两份年报开发支出资本化口径差异显著（H 股资本化金额历史上多 15%~25%）
- **宁德时代（300750 / 3750）**：2024 年首次 A+H 同时披露，关注两份年报中"开发支出"科目余额差异
- **典型陷阱**：CAS 体系下"开发支出"是单独科目（资产负债表"其他非流动资产"下），IFRS 体系下直接进"无形资产 — 内部开发"。LLM 对齐时需要识别这种科目位置差异

# 检查触发条件

- canonical_key: `rnd_total`、`rnd_expensed`、`rnd_capitalized`、`development_expenditure`
- 数值差异容差：5%（单侧绝对值）
- LLM 推理优先引用 CAS 6 第 9 条 + IAS 38 Paragraph 57
- 触发关键词：研发投入、研发支出资本化率、研发投入占比、R&D expenditure、capitalized development costs

# 参考资料

- 财政部《企业会计准则第 6 号 — 无形资产》及应用指南
- IAS 38 / HKAS 38
- KPMG《IFRS compared to CAS》 — Intangible assets and R&D 章节
- 证监会《关于上市公司执行企业会计准则的监管问题解答（2022 年第 1 期）》— 关于研发支出资本化的判断
