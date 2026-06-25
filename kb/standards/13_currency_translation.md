---
topic_key: currency_translation
topic_zh: 记账本位币与外币折算
topic_en: Functional currency and translation
cas_code: CAS 19
ifrs_code: IAS 21
hkfrs_code: HKAS 21
keywords:
  - 记账本位币
  - functional currency
  - 列报货币
  - presentation currency
  - 汇率
  - exchange rate
  - 港币
  - HKD
  - 人民币
  - CNY
  - 外币报表折算
  - foreign currency translation
expected_difference: true
severity_when_unexpected: medium
---

# 差异性质

CAS 19 与 IAS 21 在外币折算原则上**高度趋同**，但 A+H 年报实务中存在以下差异：
1. **列报货币不同**：A 股年报必须以人民币为列报货币；H 股年报常见三种实务：（1）以港币 HKD 列报（港交所传统）；（2）以人民币 CNY 列报（中资银行/能源企业普遍）；（3）双货币并列
2. **功能货币判断**：IAS 21 要求基于"主要经济环境"判断功能货币，允许功能货币与列报货币不同；CAS 19 同样要求，但 A 股实务中功能货币几乎均为人民币
3. 因此，**不同列报货币本身就会让"数值相等"的概念无效，必须先做币种归一**

# CAS 条款

> 财政部《企业会计准则第 19 号 — 外币折算》

- 第 7 条：记账本位币是指企业经营所处的主要经济环境中的货币。企业通常应选择人民币作为记账本位币。
- 第 11 条：企业发生外币交易时，应当将外币金额折算为记账本位币金额。外币交易应当在初始确认时，采用交易发生日的即期汇率将外币金额折算为记账本位币金额。
- 第 22 条：外币货币性项目，采用资产负债表日即期汇率折算。
- 第 23 条：以历史成本计量的外币非货币性项目，仍采用交易发生日的即期汇率折算，不改变其记账本位币金额。
- 第 28 条：企业对境外经营的财务报表进行折算时，应当遵循下列规定：
  - （一）资产负债表中的资产和负债项目，采用资产负债表日的即期汇率折算；
  - （二）利润表中的收入和费用项目，采用交易发生日的即期汇率折算或按照系统合理的方法确定的、与交易发生日即期汇率近似的汇率折算。

# IFRS / HKFRS 条款

> IAS 21 / HKAS 21 — The Effects of Changes in Foreign Exchange Rates

- Paragraph 9: The primary economic environment in which an entity operates is normally the one in which it primarily generates and expends cash.
- Paragraph 17: At the end of each reporting period: (a) foreign currency monetary items shall be translated using the closing rate; (b) non-monetary items that are measured in terms of historical cost in a foreign currency shall be translated using the exchange rate at the date of the transaction.
- Paragraph 39: The results and financial position of an entity whose functional currency is different from the presentation currency of a reporting entity shall be translated into the presentation currency in accordance with paragraphs 47 and 48.
- Paragraph 47: Income and expenses for each statement presenting profit or loss and other comprehensive income shall be translated at exchange rates at the dates of the transactions.

# 是否符合预期差异

**预期差异类型**：
- 列报货币不同（A 股 CNY vs H 股 HKD）→ **结构性差异，必须先做币种归一**
- 币种归一后：
  - 资产负债表项目（用期末汇率折算）：A ≈ H（汇率浮动带来 0.5-1% 差异）
  - 利润表项目（用平均汇率折算）：A ≈ H（汇率浮动带来 1-3% 差异）
  - 外币报表折算差额（OCI）：A ≈ H（应一致）

**判定规则**：
- 数值检查前必须做币种归一：
  - 资产负债表项目：用资产负债表日即期汇率折算
  - 利润表项目：用当期平均汇率折算
- 币种归一后差异 > 3% → 标 MEDIUM，需追问
- 币种归一后差异 ≤ 3% → 符合预期（汇率浮动容差）

# 典型差异表现

- **港交所港币列报企业**（如九龙仓、恒生银行系）：H 股年报以 HKD 列报，A 股以 CNY 列报，不做币种归一直接比较会导致全部数值差异
- **中资 A+H 企业**（工行、中石油）：H 股以 CNY 列报，与 A 股一致，无需币种归一
- **典型陷阱**：H 股年报封面标注"港元"或"人民币"，需在解析阶段识别列报货币；汇率数据来源（中国人民银行 vs 香港金管局）可能导致微小差异

# 检查触发条件

- 所有 canonical_key 在检查前都要走"币种归一"前置步骤
- canonical_key: `presentation_currency`、`fx_rate_period_end`、`fx_rate_average`、`foreign_currency_translation_reserve`
- 币种归一后数值容差：资产负债表 1%、利润表 3%

# 参考资料

- 财政部《企业会计准则第 19 号 — 外币折算》
- IAS 21 / HKAS 21
- KPMG《IFRS compared to CAS》 — Foreign currency translation 章节
- 中国人民银行、香港金管局公布的官方汇率
