---
topic_key: government_grant
topic_zh: 政府补助
topic_en: Government grants
cas_code: CAS 16
ifrs_code: IAS 20
hkfrs_code: HKAS 20
keywords:
  - 政府补助
  - government grant
  - 总额法
  - 净额法
  - gross method
  - net method
  - 递延收益
  - deferred income
expected_difference: true
severity_when_unexpected: low
---

# 差异性质

CAS 16 与 IAS 20 在政府补助确认与计量上总体趋同，但存在以下实务差异：
1. **列报位置差异**：CAS 16（2017 修订）要求与日常活动相关的政府补助计入"其他收益"（利润表单独列示），与日常活动无关的计入营业外收入；IAS 20 允许在利润表中以"其他收入"列示或冲减相关成本
2. **总额法 vs 净额法**：两套准则均允许总额法和净额法，但 CAS 实务中更倾向总额法（递延收益分期确认），IFRS 实务中净额法使用更普遍（直接冲减资产账面价值或成本）
3. 因此，同一 A+H 上市公司的政府补助金额可能一致，但**列报位置、利润表结构、递延收益余额**可能存在差异

# CAS 条款

> 财政部《企业会计准则第 16 号 — 政府补助》（2017 修订）

- 第 8 条：与资产相关的政府补助，应当冲减相关资产的账面价值或确认为递延收益。
- 第 11 条：与企业日常活动相关的政府补助，应当按照经济业务实质，计入其他收益或冲减相关成本费用。
- 第 14 条：与企业日常活动无关的政府补助，应当计入营业外收支。
- 第 16 条：企业应当在利润表中的"营业利润"项目之上单独列报"其他收益"项目，计入其他收益的政府补助在该项目中反映。

# IFRS / HKFRS 条款

> IAS 20 / HKAS 20 — Accounting for Government Grants and Disclosure of Government Assistance

- Paragraph 12: Government grants shall be recognised in profit or loss on a systematic basis over the periods in which the entity recognises as expenses the related costs for which the grants are intended to compensate.
- Paragraph 24: A government grant related to income may be reported separately as 'other income' or deducted from the related expense.
- Paragraph 29: A government grant related to assets, including non-monetary grants at fair value, shall be presented in the statement of financial position either as deferred income or by deducting the grant in arriving at the carrying amount of the asset.
- Paragraph 32: The presentation of the grant in the statement of cash flows depends on the accounting policy adopted for the grant.

# 是否符合预期差异

**预期差异类型**：
- 政府补助总额（当期确认 + 递延收益余额）：A = H（金额应一致）
- 递延收益余额：A = H（金额应一致）
- 利润表列报位置：A 股"其他收益"（营业利润之上）vs H 股"Other income"（可能归入营业利润内）→ **列报差异，符合预期**
- 总额法 vs 净额法选择不同 → 资产账面价值和费用金额会产生差异，但准则均允许

**判定规则**：
- 若补助总额一致、仅列报位置不同 → 符合预期，标 DISCLOSURE
- 若补助总额不一致 → 标 NUMERIC，容差 0（同一交易应一致）
- 若 A 股用总额法、H 股用净额法 → 符合预期，标 DISCLOSURE 说明方法差异

# 典型差异表现

- **制造业 A+H 公司**（如比亚迪、海尔智家）：常收到新能源汽车补贴、技术改造补贴，A 股列示于"其他收益"，H 股可能列示于"Other income"或冲减研发成本
- **典型陷阱**：H 股年报中 "government grants" 可能包含税费返还，而 A 股税费返还通常列示于"其他收益"下的明细，需识别口径一致性

# 检查触发条件

- canonical_key: `government_grant_income`、`deferred_income_government_grant`、`other_income`
- 数值差异容差：0（总额应一致）
- 触发披露差异检查的关键词：政府补助 / government grant、递延收益 / deferred income、其他收益 / other income

# 参考资料

- 财政部《企业会计准则第 16 号 — 政府补助》（2017 修订）
- IAS 20 / HKAS 20
- KPMG《IFRS compared to CAS》 — Government grants 章节