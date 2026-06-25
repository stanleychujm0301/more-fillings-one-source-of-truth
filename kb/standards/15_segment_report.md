---
topic_key: segment_report
topic_zh: 分部报告
topic_en: Operating segments report
cas_code: CAS 35
ifrs_code: IFRS 8
hkfrs_code: HKFRS 8
keywords:
  - 分部报告
  - segment report
  - 经营分部
  - operating segment
  - 报告分部
  - reportable segment
  - 管理途径
  - management approach
  - 首席运营决策者
  - chief operating decision maker
  - CODM
  - 地区分部
  - geographical segment
  - 业务分部
  - business segment
expected_difference: true
severity_when_unexpected: medium
---

# 差异性质

CAS 35 与 IFRS 8 都采用"管理途径"识别经营分部（即按管理层内部报告结构划分），但存在以下披露差异：
1. **分部数量与名称**：两套准则均要求按 CODM（首席运营决策者）视角识别分部，理论上应一致
2. **披露颗粒度**：IFRS 8 要求披露分部利润/亏损、分部资产、分部负债、分部资本支出、折旧/摊销、减值损失等；CAS 35 要求披露分部收入、分部费用、分部利润、分部资产，**对分部负债和资本支出的披露要求较弱**
3. **地区分部划分**：IFRS 8 要求按"客户所在地"划分地区分部（revenue by destination）；CAS 35 实务中常按"资产所在地"划分（revenue by origin），可能导致地区分部收入数据不一致

# CAS 条款

> 财政部《企业会计准则第 35 号 — 分部报告》

- 第 4 条：企业披露分部信息，应当区分业务分部和地区分部。
- 第 5 条：业务分部，是指企业内可区分的、能够提供单项或一组相关产品或劳务的组成部分。
- 第 6 条：地区分部，是指企业内可区分的、能够在一个特定的经济环境内提供产品或劳务的组成部分。
- 第 14 条：分部收入、分部费用、分部利润（亏损）、分部资产和分部负债，应当与企业的对外交易收入和总资产的披露相衔接。
- 第 15 条：分部信息的主要报告形式是业务分部的，应当就次要报告形式披露下列信息：
  - （一）对外交易收入占企业对外交易收入总额 10% 或者以上的地区分部...

# IFRS / HKFRS 条款

> IFRS 8 / HKFRS 8 — Operating Segments

- Paragraph 5: An operating segment is a component of an entity: (a) that engages in business activities from which it may earn revenues and incur expenses; (b) whose operating results are regularly reviewed by the entity's chief operating decision maker...
- Paragraph 22: An entity shall report a measure of profit or loss for each reportable segment.
- Paragraph 23: An entity shall report a measure of total assets and liabilities for each reportable segment if such amounts are regularly provided to the chief operating decision maker.
- Paragraph 32: An entity shall report the revenues from external customers for each product and service, or each group of similar products and services.
- Paragraph 33: An entity shall report geographical information about revenues from external customers... revenues from external customers shall be attributed to individual countries on the basis of the geographical location of its customers.

# 是否符合预期差异

**预期差异类型**：
- 分部数量、分部名称：A = H（应一致，因均基于同一管理结构）
- 分部收入、分部营业利润：A = H（应一致）
- 分部资产：A = H（应一致）
- 分部负债：H 股通常有、A 股可能无 → **披露差异，符合预期**
- 地区分部收入：H 股按客户所在地（destination）、A 股可能按资产所在地（origin）→ **口径差异，符合预期**

**判定规则**：
- 分部数量/名称不一致 → 标 HIGH，需追问（因应基于同一管理结构）
- 分部收入/利润差异 > 0.5% → 标 HIGH，需追问
- 仅分部负债披露差异 → 标 DISCLOSURE
- 地区分部口径差异 → 标 DISCLOSURE，说明划分标准不同

# 典型差异表现

- **多元化集团 A+H 企业**（中信、中国平安、招行）：H 股年报通常披露"Segment liabilities"和"Capital expenditure by segment"，A 股可能仅披露分部收入和分部利润
- **跨国企业**：H 股地区分部按客户所在地划分（如"中国大陆客户收入"包括出口），A 股可能按资产所在地划分（如"中国大陆业务收入"仅含境内销售），导致地区分部收入数据不同
- **典型陷阱**：H 股 "reportable segments" 与 A 股"报告分部"是同一概念，但 H 股可能在 Note 中披露"reconciliation of segment profit to consolidated profit"，A 股可能省略

# 检查触发条件

- canonical_key: `segment_count`、`segment_{name}_revenue`、`segment_{name}_operating_profit`、`segment_{name}_total_assets`、`segment_{name}_total_liabilities`
- 核心指标数值容差：0.5%
- 触发披露差异检查的关键词：分部 / segment、经营分部 / operating segment、首席运营决策者 / CODM、地区分部 / geographical segment、业务分部 / business segment

# 参考资料

- 财政部《企业会计准则第 35 号 — 分部报告》
- IFRS 8 / HKFRS 8
- KPMG《IFRS compared to CAS》 — Operating segments 章节
- 证监会《上市公司执行企业会计准则监管问题解答》— 分部报告的识别与披露
