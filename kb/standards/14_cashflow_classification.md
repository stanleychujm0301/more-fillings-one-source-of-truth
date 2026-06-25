---
topic_key: cashflow_classification
topic_zh: 现金流量表分类
topic_en: Cash flow statement classification
cas_code: CAS 31
ifrs_code: IAS 7
hkfrs_code: HKAS 7
keywords:
  - 现金流量表
  - cash flow statement
  - 经营活动
  - operating activities
  - 投资活动
  - investing activities
  - 筹资活动
  - financing activities
  - 利息
  - interest
  - 股利
  - dividends
expected_difference: true
severity_when_unexpected: low
---

# 差异性质

CAS 31 与 IAS 7 在现金流量表编制上总体趋同，但 IAS 7 在**利息和股利的分类**上允许更大自由度：
1. **利息收付**：
   - CAS 31：利息支付 → 筹资活动；利息收取 → 经营活动
   - IAS 7：利息支付/收取可归类为经营活动、投资活动或筹资活动（须一致使用并披露）
2. **股利收付**：
   - CAS 31：股利支付 → 筹资活动；股利收取 → 经营活动
   - IAS 7：股利支付可归类为筹资活动或经营活动；股利收取可归类为经营活动或投资活动
3. 因此，**A/H 两份现金流量表三大类小计可能不一致**，但期末现金及现金等价物余额必须一致

# CAS 条款

> 财政部《企业会计准则第 31 号 — 现金流量表》

- 第 11 条：经营活动，是指企业投资活动和筹资活动以外的所有交易和事项。
- 第 12 条：投资活动，是指企业长期资产的购建和不包括在现金等价物范围内的投资及其处置活动。
- 第 13 条：筹资活动，是指导致企业资本及债务规模和构成发生变化的活动。
- 第 18 条：企业支付的利息，属于筹资活动现金流量。
- 第 19 条：企业收到的利息，属于经营活动现金流量。
- 第 20 条：企业支付的现金股利，属于筹资活动现金流量。
- 第 21 条：企业收到的现金股利，属于经营活动现金流量。

# IFRS / HKFRS 条款

> IAS 7 / HKAS 7 — Statement of Cash Flows

- Paragraph 31: Interest paid and interest and dividends received are usually classified as operating cash flows for a financial institution.
- Paragraph 33: Interest paid and interest and dividends received may be classified as operating cash flows because they enter into the determination of profit or loss.
- Paragraph 34: Alternatively, interest paid and interest and dividends received may be classified as financing cash flows and investing cash flows respectively, because they are costs of obtaining financial resources or returns on investments.
- Paragraph 35: Dividends paid may be classified as a financing cash flow because they are a cost of obtaining financial resources.
- Paragraph 36: Alternatively, dividends paid may be classified as a component of cash flows from operating activities in order to assist users to determine the ability of an entity to pay dividends out of operating cash flows.

# 是否符合预期差异

**预期差异类型**：
- 经营活动现金流量净额：A 与 H 可能不一致（因利息/股利分类不同）→ **结构性差异，符合预期**
- 投资活动现金流量净额：A 与 H 可能不一致 → **结构性差异，符合预期**
- 筹资活动现金流量净额：A 与 H 可能不一致 → **结构性差异，符合预期**
- **期末现金及现金等价物余额：A = H（必须一致）**

**判定规则**：
- 三大类小计差异 → 标 DISCLOSURE，说明分类方法差异
- 期末现金余额差异 > 0 → 标 HIGH，需追问
- 利息/股利分类不同但总额一致 → 符合预期

# 典型差异表现

- **银行 A+H 企业**（工行、招行）：利息流入流出体量巨大，H 股可能将利息收付均归入经营活动，A 股将利息支出归入筹资活动，导致 CFO 和 CFF 差异显著
- **大型央企**：股利收付金额大，H 股可能将股利支付归入经营活动（展示"经营现金流可覆盖分红"），A 股归入筹资活动
- **典型陷阱**：H 股 "Net cash from operating activities" 与 A 股"经营活动现金流量净额"名称相同但口径可能不同；必须核对附注中利息/股利分类说明

# 检查触发条件

- canonical_key: `cfo_net`、`cfi_net`、`cff_net`、`cash_and_equivalents_end`、`interest_received`、`interest_paid`、`dividends_received`、`dividends_paid`
- 期末现金余额差异容差：0（必须一致）
- 三大类小计差异：不做硬约束，仅触发 DISCLOSURE 说明分类差异
- 触发披露差异检查的关键词：利息 / interest、股利 / dividends、经营活动 / operating activities、筹资活动 / financing activities

# 参考资料

- 财政部《企业会计准则第 31 号 — 现金流量表》
- IAS 7 / HKAS 7
- KPMG《IFRS compared to CAS》 — Cash flow statement 章节
