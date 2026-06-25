---
topic_key: goodwill
topic_zh: 商誉减值
topic_en: Goodwill impairment
cas_code: CAS 8
ifrs_code: IAS 36
hkfrs_code: HKAS 36
keywords:
  - 商誉
  - goodwill
  - 减值
  - impairment
  - 资产组
  - cash-generating unit
  - 减值测试
  - impairment test
expected_difference: true
severity_when_unexpected: medium
---

# 差异性质

CAS 8 与 IAS 36 在商誉初始确认与后续减值测试上**总体趋同**，但在以下三方面存在实务差异：
1. 减值测试**资产组划分粒度**（CAS 在分行业、分区域时倾向更粗的资产组）
2. **可收回金额**估算的折现率与终值假设的披露要求
3. **不允许转回**是两套准则共同点，但 A 股年报对减值原因的文字披露要求往往更详细

# CAS 条款

> 财政部《企业会计准则第 8 号 — 资产减值》

- 第 23 条：企业合并所形成的商誉，至少应当在每年年度终了进行减值测试。
- 第 26 条：因企业合并形成的商誉的账面价值，应当自购买日起按照合理的方法分摊至相关的资产组。
- 第 17 条：资产减值损失一经确认，在以后会计期间不得转回。

# IFRS / HKFRS 条款

> IAS 36 / HKAS 36 — Impairment of Assets

- Paragraph 10(b): Goodwill acquired in a business combination shall be tested for impairment at least annually.
- Paragraph 80: For the purpose of impairment testing, goodwill acquired in a business combination shall, from the acquisition date, be allocated to each of the acquirer's cash-generating units (CGUs).
- Paragraph 124: An impairment loss recognised for goodwill shall not be reversed in a subsequent period.

# 是否符合预期差异

**预期差异类型**：
- 总金额上：A 股与 H 股年报中**商誉账面余额应当一致**（同一被合并企业，初始确认必须相同）
- 减值金额上：当年减值金额**应当一致**（已减值不允许转回，规则相同）
- 披露详尽度：A 股披露通常更细（含资产组划分、关键假设敏感性分析），H 股可能合并披露

**判定规则**：
- 商誉余额 / 当年减值金额：A=H → 符合
- 商誉余额 / 当年减值金额 不一致 → **不符合预期**，标 HIGH，需要追问
- 仅披露详尽度差异（如 H 股缺敏感性表）→ 标 LOW DISCLOSURE 类差异

# 典型差异表现

- **工商银行（601398 / 1398）**：商誉规模小，两份年报余额应完全一致
- **比亚迪（002594 / 1211）**：含品牌并购商誉，关注资产组划分披露的中英文表述差异
- **典型陷阱**：H 股年报使用 "CGU"（Cash-Generating Unit），A 股使用"资产组"，本是同一概念但 LLM 对齐时易误判

# 检查触发条件

- canonical_key: `goodwill`、`goodwill_impairment_current_period`、`goodwill_accumulated_impairment`
- 数值差异容差：0（商誉余额必须一致）
- 触发披露差异检查的关键词：资产组 / CGU、敏感性分析 / sensitivity analysis、关键假设 / key assumptions

# 参考资料

- 财政部《企业会计准则第 8 号 — 资产减值》及应用指南
- IAS 36 / HKAS 36
- KPMG《IFRS compared to CAS》 — Goodwill and intangible assets 章节
- 证监会 2023 年信息披露监管问答 — 商誉减值披露的常见问题
