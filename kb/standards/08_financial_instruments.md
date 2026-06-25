---
topic_key: financial_instruments
topic_zh: 金融工具分类与计量
topic_en: Financial instruments classification
cas_code: CAS 22
ifrs_code: IFRS 9
hkfrs_code: HKFRS 9
keywords:
  - 金融工具
  - financial instrument
  - SPPI
  - 业务模式
  - business model
  - 预期信用损失
  - expected credit loss
  - ECL
  - 摊余成本
  - amortised cost
  - 公允价值计量
  - fair value measurement
  - 减值准备
  - impairment allowance
expected_difference: false
severity_when_unexpected: high
---

# 差异性质

CAS 22（2017 修订）与 IFRS 9 在金融资产分类与计量上**几乎完全一致**，均采用"三分类"模型：
1. 以摊余成本计量的金融资产（Amortised cost）
2. 以公允价值计量且其变动计入其他综合收益的金融资产（FVOCI）
3. 以公允价值计量且其变动计入当期损益的金融资产（FVTPL）

分类标准均基于两个测试：
- **SPPI 测试**（合同现金流量仅为本金和利息的支付）
- **业务模式测试**（持有金融资产的目的是收取合同现金流量还是出售）

**金融资产/负债总余额、预期信用损失余额预期一致**。差异通常只在细节披露（如金融工具风险表的列示顺序、公允价值层级的详细程度）。

# CAS 条款

> 财政部《企业会计准则第 22 号 — 金融工具确认和计量》（2017 修订）

- 第 16 条：企业应当根据其管理金融资产的业务模式和金融资产的合同现金流量特征，将金融资产划分为以下三类：
  - （一）以摊余成本计量的金融资产；
  - （二）以公允价值计量且其变动计入其他综合收益的金融资产；
  - （三）以公允价值计量且其变动计入当期损益的金融资产。
- 第 17 条：金融资产同时符合下列条件的，应当分类为以摊余成本计量的金融资产：
  - （一）企业管理该金融资产的业务模式是以收取合同现金流量为目标；
  - （二）该金融资产的合同条款规定，在特定日期产生的现金流量，仅为对本金和以未偿付本金金额为基础的利息的支付。
- 第 46 条：企业应当按照本准则规定，以预期信用损失为基础，对以摊余成本计量的金融资产、以公允价值计量且其变动计入其他综合收益的金融资产进行减值会计处理并确认损失准备。
- 第 47 条：预期信用损失，是指以发生违约的风险为权重的金融工具信用损失的加权平均值。

# IFRS / HKFRS 条款

> IFRS 9 / HKFRS 9 — Financial Instruments

- Paragraph 4.1.1: Unless paragraph 4.1.5 applies, an entity shall classify financial assets as subsequently measured at amortised cost, fair value through other comprehensive income, or fair value through profit or loss on the basis of both:
  - (a) the entity's business model for managing the financial assets; and
  - (b) the contractual cash flow characteristics of the financial asset.
- Paragraph 4.1.2: A financial asset shall be measured at amortised cost if both of the following conditions are met:
  - (a) the financial asset is held within a business model whose objective is to hold financial assets in order to collect contractual cash flows; and
  - (b) the contractual terms of the financial asset give rise on specified dates to cash flows that are solely payments of principal and interest on the principal amount outstanding.
- Paragraph 5.5.1: An entity shall recognise a loss allowance for expected credit losses on a financial asset that is measured in accordance with paragraph 4.1.2, a lease receivable, a contract asset or a loan commitment and a financial guarantee contract.
- Paragraph 5.5.3: Expected credit losses are a probability-weighted estimate of credit losses.

# 是否符合预期差异

**预期差异类型**：
- 金融资产分类：A = H（应一致）
- 金融资产余额（按分类）：A = H（应一致）
- 预期信用损失余额：A = H（应一致）
- 公允价值层级披露：H 股通常更详细（Level 1/2/3 调节表）→ **披露差异，符合预期**

**判定规则**：
- 金融资产总余额差异 > 0.5% → **不符合预期**，标 HIGH
- ECL 余额差异 > 0.5% → **不符合预期**，标 HIGH
- 仅公允价值层级披露详略不同 → 标 DISCLOSURE

# 典型差异表现

- **银行 A+H 企业**（如工商银行、招商银行）：发放贷款和垫款的 ECL 模型参数（违约概率 PD、违约损失率 LGD、前瞻性调整）可能在 A/H 附注中披露详略不同，但余额应一致
- **保险 A+H 企业**：金融资产的分类判断可能涉及"修改的 SPPI"测试，需关注分类一致性
- **典型陷阱**：H 股年报 "financial assets at amortised cost" 与 A 股"以摊余成本计量的金融资产"是同一概念，但 H 股可能在 Note 中单独列示 debt securities 和 loans，A 股可能合并列示

# 检查触发条件

- canonical_key: `financial_assets_amortised_cost`、`financial_assets_fvoci`、`financial_assets_fvtpl`、`expected_credit_loss_allowance`、`debt_investments`、`equity_investments`
- 数值差异容差：0.5%
- 触发披露差异检查的关键词：金融工具分类 / financial instrument classification、SPPI、业务模式 / business model、预期信用损失 / expected credit loss、减值准备 / impairment allowance

# 参考资料

- 财政部《企业会计准则第 22 号 — 金融工具确认和计量》（2017 修订）
- IFRS 9 / HKFRS 9
- KPMG《IFRS compared to CAS》 — Financial instruments 章节
