---
topic_key: esg_disclosure
topic_zh: ESG / 可持续发展披露
topic_en: ESG / Sustainability disclosure
cas_code: "沪深交易所指引 (2024)"
ifrs_code: "ISSB / IFRS S1/S2"
hkfrs_code: "港交所附录 27 / C2"
keywords:
  - ESG
  - 可持续发展
  - sustainability
  - 碳排放
  - carbon emission
  - 范围一/二/三
  - Scope 1/2/3
  - 双重重要性
  - double materiality
  - 温室气体
  - greenhouse gas
  - 董事会独立性
  - board independence
expected_difference: true
severity_when_unexpected: low
---

# 差异性质

H 股 ESG 披露要求长期早于且严于 A 股：
1. **港交所**：2016 年起强制"不披露就解释"，2022 年起强制气候变化披露，2024 年起开始接轨 ISSB（IFRS S1/S2）
2. **A 股**：2024 年起"沪深交易所可持续发展报告指引"才正式落地，且许多内容仍是鼓励披露，非强制
3. 因此，A+H 企业的两份 ESG/可持续报告披露内容差异巨大。H 股披露项目通常多于 A 股，核心指标（碳排放、员工数据、董事会构成）若同时披露则预期一致

# CAS 条款

> 上海证券交易所/深圳证券交易所《上市公司自律监管指引 — 可持续发展报告（试行）》（2024）

- 第 7 条：上市公司应当披露环境、社会和公司治理（ESG）相关信息，鼓励披露温室气体排放数据。
- 第 14 条：上市公司应当披露报告期内公司排放的主要污染物、主要处理设施及处理能力。
- 第 21 条：上市公司应当披露报告期内员工的构成情况、员工培训、健康安全、员工权益保护等情况。
- 第 28 条：上市公司应当披露公司治理的基本情况，包括股东大会、董事会、监事会的运作情况。

# IFRS / HKFRS 条款

> 港交所《环境、社会及管治报告指引》（附录 27 / C2）

- 层面 A：环境 — 强制披露：排放物、资源使用、环境及天然资源
- 层面 B：社会 — 强制披露：雇佣、健康与安全、发展及培训、劳工准则、供应链管理、产品责任、反贪污、社区投资
- 2024 年起新增：气候相关披露（接轨 ISSB）

> IFRS S1 / IFRS S2 — ISSB 可持续披露准则

- IFRS S1: General Requirements for Disclosure of Sustainability-related Financial Information
- IFRS S2: Climate-related Disclosures — 要求披露范围一、二、三温室气体排放，气候相关风险和机遇的治理、战略、风险管理、指标和目标

# 是否符合预期差异

**预期差异类型**：
- H 股披露项目多于 A 股 → **符合预期**
- A 股披露项目多于 H 股 → 不符合预期（因 H 股要求更严）
- 核心指标（若同时披露）：
  - 碳排放（Scope 1/2/3）：A = H（应一致）
  - 员工总数：A = H（应一致）
  - 独立董事比例：A = H（应一致）

**判定规则**：
- 核心指标不一致 → 标 NUMERIC，容差 1%
- 披露范围差异（H 股有、A 股无）→ 标 DISCLOSURE，符合预期
- 披露范围差异（A 股有、H 股无）→ 标 DISCLOSURE，不符合预期

# 典型差异表现

- **银行 A+H 企业**（工行、招行）：H 股披露 Scope 1/2/3 碳排放、绿色信贷余额、气候压力测试结果；A 股可能仅披露能耗数据和绿色信贷余额
- **能源 A+H 企业**（中石油、中石化）：H 股披露详细的碳捕集与封存（CCUS）数据；A 股可能仅文字描述
- **典型陷阱**：H 股 "Scope 3 emissions" 与 A 股"范围三排放"是同一概念，但 H 股通常披露价值链上下游排放，A 股可能仅披露范围一和二

# 检查触发条件

- canonical_key: `scope1_emissions`、`scope2_emissions`、`scope3_emissions`、`employees_total`、`employees_female_pct`、`independent_directors_pct`、`green_finance_balance`
- 核心指标数值差异容差：1%
- 触发披露差异检查的关键词：碳排放 / carbon emission、范围一/二/三 / Scope 1/2/3、员工 / employees、独立董事 / independent director、可持续发展 / sustainability

# 参考资料

- 上海证券交易所/深圳证券交易所《上市公司自律监管指引 — 可持续发展报告（试行）》（2024）
- 港交所《环境、社会及管治报告指引》（附录 27 / C2）
- IFRS S1 / IFRS S2 — ISSB 可持续披露准则
- KPMG《IFRS compared to CAS》 — Sustainability reporting 章节
