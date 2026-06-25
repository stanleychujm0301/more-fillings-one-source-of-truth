---
topic_key: common_control_merger
topic_zh: 同一控制下企业合并
topic_en: Business combinations under common control
cas_code: CAS 20
ifrs_code: "—"
hkfrs_code: "—"
keywords:
  - 同一控制
  - common control
  - 企业合并
  - business combination
  - 权益结合法
  - pooling of interests
  - 购买法
  - acquisition method
  - 比较数据
  - comparative data
expected_difference: true
severity_when_unexpected: high
---

# 差异性质

**这是 CAS↔IFRS 体系最大的会计政策差异之一**。CAS 20 把同一控制下企业合并单独规定，使用**权益结合法**（账面价值合并，不产生商誉，比较期数据追溯重述）。IFRS 没有对应单独准则，IFRS 3 排除同一控制下合并，实务中多数 H 股企业对同一控制下合并使用**前任账面价值法**（predecessor carrying amount）或参照购买法处理。两套报表的"合并报表期初数 / 比较期数据 / 净资产"可能出现实质性差异。

# CAS 条款

> 财政部《企业会计准则第 20 号 — 企业合并》

- 第 5 条：参与合并的企业在合并前后均受同一方或相同的多方最终控制且该控制并非暂时性的，为同一控制下的企业合并。
- 第 6 条：合并方在企业合并中取得的资产和负债，应当按照合并日在被合并方的账面价值计量。
- 第 9 条：合并方取得的净资产账面价值与支付的合并对价账面价值（或发行股份面值总额）的差额，应当调整资本公积；资本公积不足冲减的，调整留存收益。
- 第 16 条：企业合并形成母子公司关系的，母公司应当编制合并日的合并资产负债表、合并利润表和合并现金流量表。合并利润表应当包括参与合并各方自合并当期期初至合并日所发生的收入、费用和利润。

# IFRS / HKFRS 条款

> IFRS 3 / HKFRS 3 — Business Combinations

- Paragraph 2(c): This Standard does not apply to a business combination of entities or businesses under common control.
- Paragraph B1: A business combination involving entities or businesses under common control is a business combination in which all of the combining entities or businesses are ultimately controlled by the same party or parties both before and after the business combination.

> HKFRS 实务惯例

- 香港实务中，同一控制下合并通常采用以下两种方法之一：
  1. **前任账面价值法**（Predecessor carrying amounts）：按被合并方原账面价值合并，不产生商誉
  2. **购买法**（Acquisition method）：参照 IFRS 3 购买法处理，按公允价值计量，可能产生商誉
- 多数 H 股企业采用前任账面价值法，与 CAS 权益结合法结果相近，但比较期追溯调整的范围可能不同

# 是否符合预期差异

**预期差异类型**：
- 涉及同一控制下合并的年度：**合并报表净资产、比较期数据预期不一致**
- 这是**结构性差异，符合预期**，但需在差异报告中明确说明
- 若 H 股采用购买法、A 股采用权益结合法 → 商誉、净资产、比较期数据均会不同
- 若 H 股采用前任账面价值法、A 股采用权益结合法 → 结果通常相近，但比较期追溯调整范围可能不同

**判定规则**：
- 首先检查两份年报附注"企业合并"章节，确认是否涉及同一控制下合并
- 涉及同一控制下合并的年度：净资产差异 → 符合预期，标 STANDARD 差异，说明方法不同
- 不涉及同一控制下合并的年度：净资产应一致（容差 0.5%）

# 典型差异表现

- **央企集团内重组**（中信集团、中粮集团、招商局集团）下的 A+H 上市公司：集团内部股权划转导致同一控制下合并，A 股追溯重述比较期数据，H 股可能不追溯或追溯范围不同
- **大型国企 A+H 企业**（中石油、中石化、中国电信）：历史上多次集团内重组，比较期数据差异可能持续多年
- **典型陷阱**：同一控制下合并导致的差异容易被误判为"数据错误"，需在差异报告中明确标注"同一控制下合并导致的结构性差异"

# 检查触发条件

- canonical_key: `goodwill_from_business_combination`、`equity_at_period_start`、`comparative_revenue`、`comparative_net_profit`
- 优先检查报表附注中"重要会计政策 — 企业合并"章节的文字描述差异
- 若附注明确提及同一控制下合并，则相关年度净资产差异标为"符合预期"

# 参考资料

- 财政部《企业会计准则第 20 号 — 企业合并》
- IFRS 3 / HKFRS 3
- KPMG IFRS Handbook — Business combinations under common control
- 证监会《上市公司执行企业会计准则监管问题解答》— 同一控制下企业合并的比较期追溯调整