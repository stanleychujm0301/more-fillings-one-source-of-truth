---
topic_key: investment_property
topic_zh: 投资性房地产
topic_en: Investment property
cas_code: CAS 3
ifrs_code: IAS 40
hkfrs_code: HKAS 40
keywords:
  - 投资性房地产
  - investment property
  - 成本模式
  - cost model
  - 公允价值模式
  - fair value model
  - 后续计量
  - subsequent measurement
  - 转换
  - transfer
expected_difference: true
severity_when_unexpected: medium
---

# 差异性质

CAS 3 与 IAS 40 都允许"成本模式"和"公允价值模式"两种后续计量，但两地实务选择存在系统性差异：
1. **A 股实务**：绝大多数企业选用成本模式（以历史成本计量，按期计提折旧/摊销，期末进行减值测试）
2. **H 股实务**：香港上市房企及持有大量投资性房地产的企业普遍选用公允价值模式（公允价值变动计入当期损益）
3. 因此，同一 A+H 上市公司若在两份年报中选用不同计量模式，资产负债表与利润表数据将存在计量基础差异，**这不是错误，而是会计政策选择差异**

# CAS 条款

> 财政部《企业会计准则第 3 号 — 投资性房地产》

- 第 9 条：企业应当在资产负债表日采用成本模式对投资性房地产进行后续计量，但符合本准则第十条规定的，可以采用公允价值模式。
- 第 10 条：采用公允价值模式计量的，应当同时满足下列条件：（一）投资性房地产所在地有活跃的房地产交易市场；（二）企业能够从房地产交易市场上取得同类或类似房地产的市场价格及其他相关信息，从而对投资性房地产的公允价值作出合理的估计。
- 第 12 条：企业对投资性房地产的计量模式一经确定，不得随意变更。成本模式转为公允价值模式的，应当作为会计政策变更处理。
- 第 16 条：自用房地产或存货转换为采用公允价值模式计量的投资性房地产时，投资性房地产按照转换当日的公允价值计价，转换当日的公允价值小于原账面价值的，其差额计入当期损益；转换当日的公允价值大于原账面价值的，其差额计入所有者权益。

# IFRS / HKFRS 条款

> IAS 40 / HKAS 40 — Investment Property

- Paragraph 30: After initial recognition, an entity shall choose as its accounting policy either the fair value model or the cost model.
- Paragraph 33: When a property interest held by a lessee under an operating lease is classified as an investment property, the fair value model shall be applied.
- Paragraph 35: Under the fair value model, investment property is measured at fair value, with any gain or loss arising from a change in the fair value recognised in profit or loss.
- Paragraph 60: For a transfer from inventories to investment property at fair value, any difference between the fair value of the property at that date and its previous carrying amount shall be recognised in profit or loss.
- Paragraph 61: For a transfer from owner-occupied property to investment property at fair value, any difference between the fair value of the property at that date and its previous carrying amount shall be treated as a revaluation under IAS 16.

# 是否符合预期差异

**预期差异类型**：
- 若 A 股采用成本模式、H 股采用公允价值模式 → **符合预期**（常见情形）
- 若 A/H 均采用成本模式 → 账面价值应一致（容差 0）
- 若 A/H 均采用公允价值模式 → 公允价值应一致（容差 0）
- 若 A 股采用公允价值模式、H 股采用成本模式 → 较少见，但亦可能，需确认是否为会计政策变更年度

**判定规则**：
- 首先检查两份年报附注中"投资性房地产后续计量模式"的文字描述
- 模式不同 → 标 DISCLOSURE 类差异，说明计量基础不同导致数值不可直接比较
- 模式相同但数值不一致 → 标 NUMERIC 差异，容差 0

# 典型差异表现

- **万科（000002 / 2202）**：A 股对投资性房地产采用成本模式，H 股部分年份采用公允价值模式，导致两份年报"投资性房地产"账面价值差异显著
- **华润置地（01109）**：H 股采用公允价值模式，公允价值变动每年贡献利润表数亿元；若其 A 股子公司采用成本模式，则同一物业在合并层面出现不同计量
- **典型陷阱**：H 股年报使用 "fair value model"，A 股使用"公允价值模式"，但 LLM 需进一步识别模式选择是否一致

# 检查触发条件

- canonical_key: `investment_property`、`investment_property_cost`、`investment_property_fair_value`、`investment_property_fair_value_change`
- 数值差异容差：模式相同时为 0；模式不同时不做数值比较，仅触发 DISCLOSURE 提示
- 触发披露差异检查的关键词：成本模式 / cost model、公允价值模式 / fair value model、投资性房地产转换 / transfer to investment property

# 参考资料

- 财政部《企业会计准则第 3 号 — 投资性房地产》及应用指南
- IAS 40 / HKAS 40
- KPMG《IFRS compared to CAS》 — Investment property 章节
- 证监会《上市公司执行企业会计准则监管问题解答（2011 年第 1 期）》— 投资性房地产转换的会计处理
