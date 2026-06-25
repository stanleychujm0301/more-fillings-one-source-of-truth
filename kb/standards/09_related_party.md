---
topic_key: related_party
topic_zh: 关联方及其交易披露
topic_en: Related party disclosures
cas_code: CAS 36
ifrs_code: IAS 24
hkfrs_code: HKAS 24
keywords:
  - 关联方
  - related party
  - 关联交易
  - related party transaction
  - 关键管理人员
  - key management personnel
  - 同一最终控制方
  - common control
  - 定价政策
  - pricing policy
expected_difference: true
severity_when_unexpected: high
---

# 差异性质

CAS 36 与 IAS 24 在关联方识别和交易披露上总体趋同，但存在以下显著差异：
1. **关联方定义范围**：CAS 36 把"国家控制企业之间"也纳入关联方（仅有限豁免），IAS 24 对政府相关实体有更宽泛的豁免。因此 A 股年报关联方/关联交易披露的项目数通常显著多于 H 股年报
2. **关键管理人员薪酬披露**：IAS 24 要求按姓名披露每位董事/高管的薪酬明细；CAS 36 仅要求汇总披露关键管理人员薪酬总额
3. **未结算余额条款披露**：IAS 24 要求披露关联方未结算余额的条款、条件和担保；CAS 36 亦有类似要求，但 H 股实务中披露通常更详细

# CAS 条款

> 财政部《企业会计准则第 36 号 — 关联方披露》

- 第 3 条：一方控制、共同控制另一方或对另一方施加重大影响，以及两方或两方以上同受一方控制、共同控制或重大影响的，构成关联方。
- 第 4 条：国家控制的企业之间不因此仅仅同受国家控制而构成关联方，但同受国家控制的企业之间如果存在控制、共同控制或重大影响关系的，仍构成关联方。
- 第 8 条：企业无论是否发生关联方交易，均应当在附注中披露与母公司和子公司有关的下列信息。
- 第 10 条：企业与关联方发生关联方交易的，应当在附注中披露该关联方关系的性质、交易类型及交易要素。
- 第 11 条：关联方交易应当分别关联方以及交易类型予以披露。类型相似的关联方交易，在不影响财务报表阅读者正确理解关联方交易对财务报表影响的情况下，可以合并披露。

# IFRS / HKFRS 条款

> IAS 24 / HKAS 24 — Related Party Disclosures

- Paragraph 9: A related party is a person or entity that is related to the entity that is preparing its financial statements.
- Paragraph 12: Management compensation in total shall be disclosed, including short-term employee benefits, post-employment benefits, other long-term benefits, termination benefits and share-based payment.
- Paragraph 13: If an entity has had related party transactions during the periods covered by the financial statements, it shall disclose the nature of the related party relationship as well as information about those transactions and outstanding balances.
- Paragraph 17: The disclosure required by paragraph 16 shall be made separately for each of the following categories: (a) the parent; (b) entities with joint control of or significant influence over the entity; (c) subsidiaries; (d) associates; (e) joint ventures... (f) key management personnel...
- Paragraph 18: An entity shall disclose key management personnel compensation in total and for each of the following categories: short-term employee benefits, post-employment benefits, other long-term benefits, termination benefits and share-based payment.

# 是否符合预期差异

**预期差异类型**：
- 关联交易金额：A = H（应一致）
- 关联方清单：A 股 ≥ H 股（因 CAS 国家控制企业定义更广）
- 关键管理人员薪酬总额：A = H（应一致）
- 关键管理人员薪酬按姓名披露：H 股有、A 股无 → **披露差异，符合预期**
- 国家控制企业间交易：A 股披露、H 股可能豁免 → **披露差异，符合预期**

**判定规则**：
- 关联交易金额不一致 → 标 HIGH，需追问
- A 股关联方数量 < H 股关联方数量 → 不符合预期（因 CAS 定义更广）
- 仅披露详略不同 → 标 DISCLOSURE

# 典型差异表现

- **央企 A+H 企业**（中石油、中石化、工行、招行）：与国资委下其他央企的交易在 A 股作为关联方披露，H 股可能因 IAS 24 豁免条款未披露
- **A+H 上市公司普遍现象**：H 股年报附注 "Directors' emoluments" 按姓名列示每位董事薪酬；A 股年报仅列示"关键管理人员薪酬"总额
- **典型陷阱**：H 股 "key management personnel" 范围可能比 A 股更广（含近亲属控制的企业），需核对关联方清单一致性

# 检查触发条件

- canonical_key: `related_party_transactions_total`、`related_party_count`、`key_management_compensation`、`trade_receivables_related_party`、`trade_payables_related_party`
- 数值差异容差：0（关联交易金额应一致）
- 触发披露差异检查的关键词：关联方 / related party、关联交易 / related party transaction、关键管理人员薪酬 / key management compensation、定价政策 / pricing policy、未结算余额 / outstanding balance

# 参考资料

- 财政部《企业会计准则第 36 号 — 关联方披露》
- IAS 24 / HKAS 24
- KPMG《IFRS compared to CAS》 — Related party disclosures 章节
