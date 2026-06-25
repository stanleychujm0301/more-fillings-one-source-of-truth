---
topic_key: long_term_investment
topic_zh: 长期股权投资
topic_en: Long-term equity investments
cas_code: CAS 2
ifrs_code: IAS 28
hkfrs_code: HKAS 28
keywords:
  - 长期股权投资
  - long-term equity investment
  - 合营企业
  - joint venture
  - 联营企业
  - associate
  - 权益法
  - equity method
  - 成本法
  - cost method
expected_difference: true
severity_when_unexpected: high
---

# 差异性质

CAS 2 与 IAS 28 在长期股权投资核算方法上总体趋同，但存在以下披露差异：
1. **子公司核算**：CAS 2 要求母公司个别报表对子公司采用成本法；IAS 27 允许母公司个别报表对子公司采用成本法或权益法，香港实务中部分 H 股企业采用权益法
2. **合营/联营披露详尽度**：IAS 28 要求披露联营/合营企业的"汇总财务信息"（summarised financial information），包括资产、负债、收入、利润等；CAS 2 要求披露"主要财务信息"，实务中 A 股披露通常更简化
3. **减值测试**：两套准则均要求对长期股权投资进行减值测试，但 IAS 36 要求披露 CGU 层面的减值测试详情，CAS 8 要求披露资产组层面的详情

# CAS 条款

> 财政部《企业会计准则第 2 号 — 长期股权投资》

- 第 5 条：投资方能够对被投资单位实施控制的，被投资单位为其子公司。投资方对子公司的长期股权投资应当采用成本法核算。
- 第 9 条：投资方对联营企业和合营企业的长期股权投资，应当采用权益法核算。
- 第 15 条：投资方取得长期股权投资后，应当按照应享有或应分担的被投资单位实现的净损益和其他综合收益的份额，分别确认投资收益和其他综合收益。
- 第 17 条：投资方确认被投资单位发生的净亏损，应当以长期股权投资的账面价值以及其他实质上构成对被投资单位净投资的长期权益减记至零为限。

# IFRS / HKFRS 条款

> IAS 28 / HKAS 28 — Investments in Associates and Joint Ventures

- Paragraph 16: An investment in an associate or a joint venture shall be accounted for using the equity method.
- Paragraph 26: An investment in an associate or a joint venture shall be tested for impairment in accordance with IAS 36.
- Paragraph 32: An entity shall disclose the fair value of investments in associates for which there are published price quotations.
- Paragraph 37: An entity shall disclose summarised financial information for associates or joint ventures that are material to the reporting entity.

> IAS 27 / HKAS 27 — Separate Financial Statements

- Paragraph 10: In its separate financial statements, a parent shall account for investments in subsidiaries, joint ventures and associates either at cost, or in accordance with IFRS 9.

# 是否符合预期差异

**预期差异类型**：
- 对联营/合营的投资账面价值：A = H（权益法下应一致）
- 对子公司的投资账面价值（合并报表层面）：不适用（子公司已合并抵消）
- 对子公司的投资账面价值（个别报表层面）：A = H（若均采用成本法）或存在差异（若 H 股采用权益法）
- 合营/联营披露详尽度：H 股通常更详细 → **披露差异，符合预期**

**判定规则**：
- 联营/合营投资账面价值不一致 → 标 HIGH，需追问
- 子公司投资在个别报表层面方法不同 → 标 DISCLOSURE，说明方法差异
- 仅披露详尽度差异 → 标 LOW DISCLOSURE

# 典型差异表现

- **大型央企**（如中石油、中石化、招商银行）：合营/联营数量多，H 股年报通常附"Summarised financial information of associates"表格，A 股可能仅文字描述
- **典型陷阱**：H 股年报中 "interests in associates" 与 A 股"长期股权投资 — 联营企业"是同一概念，但附注详略不同

# 检查触发条件

- canonical_key: `long_term_equity_investments`、`investments_in_associates_jvs`、`investment_income_equity_method`
- 数值差异容差：0.5%
- 触发披露差异检查的关键词：联营企业 / associate、合营企业 / joint venture、权益法 / equity method、汇总财务信息 / summarised financial information

# 参考资料

- 财政部《企业会计准则第 2 号 — 长期股权投资》及应用指南
- IAS 28 / HKAS 28
- IAS 27 / HKAS 27
- KPMG《IFRS compared to CAS》 — Associates and joint ventures 章节