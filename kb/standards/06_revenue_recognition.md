---
topic_key: revenue_recognition
topic_zh: 收入确认时点
topic_en: Revenue recognition
cas_code: CAS 14
ifrs_code: IFRS 15
hkfrs_code: HKFRS 15
keywords:
  - 收入
  - revenue
  - 履约义务
  - performance obligation
  - 时段法
  - over time
  - 时点法
  - point in time
  - 控制权转移
  - transfer of control
expected_difference: false
severity_when_unexpected: high
---

# 差异性质

CAS 14（2017 修订版）与 IFRS 15 已高度趋同，均采用"五步法"收入确认模型：
1. 识别与客户订立的合同
2. 识别合同中的履约义务
3. 确定交易价格
4. 将交易价格分摊至各履约义务
5. 履行履约义务时（或履行过程中）确认收入

**总收入金额预期完全一致**。差异通常仅在：
- 主表项目排列差异（CAS 中"营业收入"vs IFRS "Revenue"行项目位置不同）
- 分部收入披露口径细节
- 特定行业（如房地产、建筑）的时段法/时点法选择判断

# CAS 条款

> 财政部《企业会计准则第 14 号 — 收入》（2017 修订）

- 第 4 条：企业应当在履行了合同中的履约义务，即在客户取得相关商品控制权时确认收入。
- 第 9 条：合同开始日，企业应当对合同进行评估，识别该合同所包含的各单项履约义务。
- 第 11 条：满足下列条件之一的，属于在某一时段内履行履约义务；否则，属于在某一时点履行履约义务：
  - （一）客户在企业履约的同时取得并消耗企业履约所带来的经济利益；
  - （二）客户能够控制企业履约过程中在建的商品；
  - （三）企业履约过程中所产出的商品具有不可替代用途，且该企业在整个合同期间内有权就累计至今已完成的履约部分收取款项。
- 第 13 条：对于在某一时点履行的履约义务，企业应当在客户取得相关商品控制权时点确认收入。

# IFRS / HKFRS 条款

> IFRS 15 / HKFRS 15 — Revenue from Contracts with Customers

- Paragraph 31: An entity shall recognise revenue when (or as) the entity satisfies a performance obligation by transferring a promised good or service to a customer.
- Paragraph 35: An entity transfers control of a good or service over time and, therefore, satisfies a performance obligation and recognises revenue over time, if one of the following criteria is met:
  - (a) the customer simultaneously receives and consumes the benefits provided by the entity's performance...
  - (b) the entity's performance creates or enhances an asset that the customer controls as the asset is created or enhanced...
  - (c) the entity's performance does not create an asset with an alternative use to the entity and the entity has an enforceable right to payment...
- Paragraph 38: If a performance obligation is not satisfied over time, an entity satisfies the performance obligation at a point in time.

# 是否符合预期差异

**预期差异类型**：
- 收入总额：A = H（必须一致）
- 履约义务划分：A = H（应当一致）
- 时段法/时点法选择：A = H（应当一致）
- 仅在披露格式和分部收入明细上可能存在差异

**判定规则**：
- 收入总额差异 > 0.5% → **不符合预期**，标 HIGH，需追问
- 收入总额差异 ≤ 0.5% → 符合预期
- 时段法/时点法选择不一致 → 标 HIGH，重大会计政策差异

# 典型差异表现

- **房地产 A+H 企业**：预售房款是否满足"时段法"第三条（不可替代用途+收款权）的判断，A 股与 H 股理论上应一致，但实务中存在判断差异
- **SaaS/互联网 A+H 企业**：订阅收入的分期确认方式应一致
- **典型陷阱**：H 股年报 "Revenue" 与 A 股"营业收入"名称不同但金额应一致；注意 H 股可能单独列示 "Other income" 而 A 股归入"其他收益"

# 检查触发条件

- canonical_key: `revenue`、`operating_revenue`、`revenue_from_contracts_with_customers`
- 数值差异容差：0.5%（非常严格）
- 触发披露差异检查的关键词：收入确认政策 / revenue recognition policy、履约义务 / performance obligation、时段法 / over time、时点法 / point in time

# 参考资料

- 财政部《企业会计准则第 14 号 — 收入》（2017 修订）
- IFRS 15 / HKFRS 15
- KPMG《IFRS compared to CAS》 — Revenue 章节
- 证监会《上市公司执行企业会计准则监管问题解答》— 新收入准则实施相关问题