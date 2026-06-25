---
topic_key: lease
topic_zh: 租赁
topic_en: Leases
cas_code: CAS 21
ifrs_code: IFRS 16
hkfrs_code: HKFRS 16
keywords:
  - 租赁
  - lease
  - 使用权资产
  - right-of-use asset
  - 租赁负债
  - lease liability
  - 短期租赁
  - short-term lease
  - 低价值资产
  - low-value asset
expected_difference: true
severity_when_unexpected: medium
---

# 差异性质

CAS 21（2018 修订）与 IFRS 16 高度趋同，均采用"承租人单一资产负债表模型"（使用权资产 + 租赁负债）。差异主要在：
1. **首次执行日过渡安排**：中国分批次实施（2019 年境内外同时上市、2020 年其他境内上市企业、2021 年执行企业会计准则的企业），H 股企业通常更早采用 IFRS 16
2. **短期/低价值租赁简化处理选择**：两套准则均允许简化处理，但 A 股与 H 股可能在选择范围上不同
3. **出租人会计**：CAS 21 与 IFRS 16 在出租人会计上几乎完全一致，差异极小

# CAS 条款

> 财政部《企业会计准则第 21 号 — 租赁》（2018 修订）

- 第 14 条：在租赁期开始日，承租人应当对租赁确认使用权资产和租赁负债。
- 第 17 条：承租人应当按照成本对使用权资产进行初始计量。该成本包括：（一）租赁负债的初始计量金额；（二）在租赁期开始日或之前支付的租赁付款额...
- 第 20 条：承租人应当参照《企业会计准则第 4 号 — 固定资产》有关折旧规定，对使用权资产计提折旧。
- 第 32 条：承租人可以按照租赁资产的类别选择是否采用简化处理。短期租赁（租赁期不超过 12 个月）和低价值资产租赁可以采用简化处理。
- 第 61 条：对于首次执行日前的经营租赁，承租人在首次执行日应当根据剩余租赁付款额按首次执行日承租人增量借款利率折现的现值计量租赁负债。

# IFRS / HKFRS 条款

> IFRS 16 / HKFRS 16 — Leases

- Paragraph 22: At the commencement date, a lessee shall recognise a right-of-use asset and a lease liability.
- Paragraph 26: A lessee shall measure the right-of-use asset at cost.
- Paragraph 33: After the commencement date, a lessee shall measure the right-of-use asset applying a cost model.
- Paragraph 5: A lessee may elect not to apply the requirements in paragraphs 22–49 to: (a) short-term leases; and (b) leases for which the underlying asset is of low value.
- Paragraph C3: A lessee shall apply this Standard to its leases either retrospectively to each prior reporting period presented or retrospectively with the cumulative effect of initially applying this Standard recognised at the date of initial application.

# 是否符合预期差异

**预期差异类型**：
- 使用权资产余额：A = H（应一致）
- 租赁负债余额：A = H（应一致）
- 折旧费用：A = H（应一致）
- 利息费用：A = H（应一致）
- 简化处理范围不同 → 若 A 股对某类租赁采用简化、H 股未采用，则一侧表内一侧表外 → **结构性差异，符合预期**

**判定规则**：
- 使用权资产/租赁负债差异 ≤ 1% → 符合预期（四舍五入差异）
- 使用权资产/租赁负债差异 > 1% → 标 MEDIUM，需追问
- 简化处理选择不同 → 标 DISCLOSURE，说明方法差异

# 典型差异表现

- **零售连锁 A+H 企业**（如华润置地、九龙仓）：门店租赁数量多，若 A 股对短期续租采用简化处理而 H 股全面确认，则使用权资产差异显著
- **航空公司**：飞机租赁是核心资产，A/H 应完全一致；差异通常仅在折现率选择（增量借款利率 vs 租赁内含利率）
- **典型陷阱**：H 股年报 "lease liabilities" 分为 current 和 non-current，A 股"租赁负债"同样分一年内到期和一年以上到期，需核对分类一致性

# 检查触发条件

- canonical_key: `right_of_use_assets`、`lease_liabilities_current`、`lease_liabilities_non_current`、`depreciation_right_of_use_assets`、`interest_lease_liabilities`
- 数值差异容差：1%
- 触发披露差异检查的关键词：使用权资产 / right-of-use asset、租赁负债 / lease liability、短期租赁 / short-term lease、简化处理 / simplified approach

# 参考资料

- 财政部《企业会计准则第 21 号 — 租赁》（2018 修订）
- IFRS 16 / HKFRS 16
- KPMG《IFRS compared to CAS》 — Leases 章节
