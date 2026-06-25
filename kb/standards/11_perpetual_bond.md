---
topic_key: perpetual_bond
topic_zh: 永续债分类
topic_en: Perpetual bonds classification
cas_code: "财会〔2019〕2 号"
ifrs_code: IAS 32
hkfrs_code: HKAS 32
keywords:
  - 永续债
  - perpetual bond
  - 优先股
  - preference share
  - 权益工具
  - equity instrument
  - 金融负债
  - financial liability
  - 股债划分
  - debt vs equity
  - 利率跳升
  - interest rate step-up
expected_difference: true
severity_when_unexpected: high
---

# 差异性质

财政部 2019 年发布《永续债相关会计处理的规定》（财会〔2019〕2 号），与 IAS 32 在金融负债与权益工具划分上**原则趋同**（均基于"交付现金或其他金融资产的合同义务"判断），但实务判断仍存在差异：
1. **CAS 体系下永续债被分类为权益工具的占比偏高**：财会〔2019〕2 号对永续债分类给出了较明确的"安全港"指引，满足特定条件即可分类为权益
2. **IFRS 体系下分类为金融负债的占比较高**：IAS 32 要求更严格的"无交付现金义务"判断，利率跳升条款、股息制动机制等可能导致分类为负债
3. 因此，同一企业的同一只永续债，在 A/H 两份年报中可能出现"A 股归权益、H 股归负债"的结构性差异

# CAS 条款

> 财政部《永续债相关会计处理的规定》（财会〔2019〕2 号）

- 第 2 条：永续债发行方在确定永续债的会计分类是权益工具还是金融负债时，应当根据第 37 号准则规定同时考虑下列因素：
  - （一）到期日：永续债合同未规定固定到期日且持有方在任何情况下均无权要求发行方赎回该永续债或清算的，通常表明发行方没有交付现金或其他金融资产的合同义务。
  - （二）清偿顺序：永续债合同规定发行方清算时永续债劣后于普通债务和其他债务的，通常表明发行方没有交付现金或其他金融资产的合同义务。
  - （三）利率跳升和间接义务：永续债合同规定没有利率跳升机制，或者虽有利率跳升机制但跳升次数有限、最高利率未超过规定水平的，通常表明发行方没有交付现金或其他金融资产的合同义务。
- 第 3 条：永续债持有方应当按《企业会计准则第 22 号 — 金融工具确认和计量》的规定，将持有的永续债分类为以公允价值计量且其变动计入当期损益的金融资产，或可供出售金融资产等。

# IFRS / HKFRS 条款

> IAS 32 / HKAS 32 — Financial Instruments: Presentation

- Paragraph 11: The issuer of a financial instrument shall classify the instrument, or its component parts, on initial recognition as a financial liability, a financial asset or an equity instrument in accordance with the substance of the contractual arrangement and the definitions of a financial liability, a financial asset and an equity instrument.
- Paragraph 16: A financial instrument is an equity instrument if, and only if, both conditions (a) and (b) are met:
  - (a) the instrument includes no contractual obligation to deliver cash or another financial asset to another entity...
  - (b) if the instrument will or may be settled in the issuer's own equity instruments, it is either a non-derivative that includes no contractual obligation for the issuer to deliver a variable number of its own equity instruments, or a derivative that will be settled only by the issuer exchanging a fixed amount of cash or another financial asset for a fixed number of its own equity instruments.
- Paragraph 18: The substance of a financial instrument, rather than its legal form, governs its classification on the entity's statement of financial position.

# 是否符合预期差异

**预期差异类型**：
- 永续债余额在 A 股"其他权益工具" vs H 股"金融负债" → **结构性差异，符合预期**
- 永续债利息：A 股作为"利润分配"（不影响利润表）vs H 股作为"利息费用"（影响利润表）→ **利润表结构性差异，符合预期**
- 若 A/H 均分类为权益工具 → 应一致
- 若 A/H 均分类为金融负债 → 应一致

**判定规则**：
- 首先检查两份年报附注中永续债的会计分类描述
- 分类不同 → 标 STANDARD 差异，说明"股债划分"方法不同
- 分类相同但金额不一致 → 标 NUMERIC，容差 0

# 典型差异表现

- **大型央企 A+H 企业**（中国铁建、中国石油、中国电信）：发行的永续中票/永续债，A 股常分类为"其他权益工具"，H 股可能分类为"Bonds and notes"（负债）
- **银行 A+H 企业**：发行的永续债（TLAC 工具），A 股分类为"其他一级资本工具"（权益），H 股可能分类为" liabilities"
- **典型陷阱**：永续债利息的处理差异——A 股作为股息分配（在权益变动表中列示），H 股作为利息支出（在利润表中列示），导致净利润口径不同

# 检查触发条件

- canonical_key: `perpetual_bonds_equity`、`perpetual_bonds_liability`、`other_equity_instruments`、`interest_expense_perpetual_bonds`
- 触发披露差异检查的关键词：永续债 / perpetual bond、其他权益工具 / other equity instruments、利率跳升 / interest rate step-up、股息制动 / dividend pusher/brake

# 参考资料

- 财政部《永续债相关会计处理的规定》（财会〔2019〕2 号）
- IAS 32 / HKAS 32
- KPMG《IFRS compared to CAS》 — Financial liability vs equity 章节
- 证监会《上市公司执行企业会计准则监管问题解答》— 永续债会计处理