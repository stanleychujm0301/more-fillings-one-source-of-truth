# 准则差异知识库 — 编写指南（P6 必读）

> 本目录是整个项目的**核心护城河**。技术代码 1 周可复制，但 15 条经 KPMG 高级经理校验的 CAS↔IFRS 差异条款是评委眼中"专业含金量"的直接证明。

---

## 1. 文件命名

`{两位数序号}_{topic_key}.md`，按差异主题排序：

```
01_goodwill.md                       商誉减值
02_rnd_capitalize.md                 研发支出资本化
03_investment_property.md            投资性房地产
04_government_grant.md               政府补助
05_long_term_investment.md           长期股权投资
06_revenue_recognition.md            收入确认时点
07_lease.md                          租赁
08_financial_instruments.md          金融工具分类
09_related_party.md                  关联方披露
10_common_control_merger.md          同一控制下企业合并
11_perpetual_bond.md                 永续债分类
12_esg_disclosure.md                 ESG/可持续披露
13_currency_translation.md           币种折算
14_cashflow_classification.md        现金流量表分类
15_segment_report.md                 分部报告
```

## 2. 每条 Markdown 标准格式

```markdown
---
topic_key: rnd_capitalize          # 与文件名后段一致，作为检索主键
topic_zh: 研发支出资本化
topic_en: R&D capitalization
cas_code: CAS 6                    # 中国准则编号（无则填 "—"）
ifrs_code: IAS 38                  # 国际准则编号
hkfrs_code: HKAS 38                # 香港准则编号（通常与 IFRS 一致）
keywords:                          # 触发关键词，用于 RAG 召回增强
  - 研发
  - R&D
  - 资本化
  - capitalization
  - 无形资产
  - intangible assets
expected_difference: true          # 是否预期会出现 CAS↔IFRS 差异
severity_when_unexpected: medium   # 不符合预期时的差异严重度
---

# 差异性质

简要说明这条差异是**会计处理差异**、**披露格式差异**还是**口径差异**，以及差异的本质原因（如：CAS 趋同时保留的本土特性、监管要求不同、过渡期安排差异等）。3-5 句话。

# CAS 条款

> 引用财政部《企业会计准则第 X 号》正文条款，标注条款编号。

CAS 6 第 9 条：企业内部研究开发项目研究阶段的支出，应当于发生时计入当期损益。开发阶段的支出，同时满足下列条件的，才能确认为无形资产：
（一）完成该无形资产以使其能够使用或出售在技术上具有可行性；
（二）...

# IFRS / HKFRS 条款

> 引用 IAS / IFRS 正文条款。

IAS 38 Paragraph 57: An intangible asset arising from development (or from the development phase of an internal project) shall be recognised if, and only if, an entity can demonstrate all of the following:
(a) the technical feasibility of completing the intangible asset...

# 是否符合预期差异

**预期差异类型**：CAS 与 IFRS 在条文表述上虽趋同，但实务中：
- CAS 要求"同时满足 5 个条件"才资本化，对**判断时点**更严格
- IFRS 允许更早资本化（满足开发阶段判定即可）
- 因此 **A 股年报中资本化金额通常 ≤ H 股年报中对应金额**

**判定规则**：
- 若 A 股资本化 < H 股资本化，且差异 ≤ 30% → **符合预期**
- 若 A 股资本化 > H 股资本化 → **不符合预期**，需追问
- 差异 > 30% → 标记 MEDIUM，提示审计师追问技术可行性判定时点

# 典型差异表现

举 1-2 个实际公开年报中的差异案例：
- **比亚迪（002594 / 1211）2024 年报**：A 股研发资本化 X 亿，H 股 Y 亿，差异 Z%
- **宁德时代（300750 / 3750）2024 年报**：...

# 检查触发条件（供 P3/P4 配规则）

- canonical_key: `rnd_capitalized_amount`、`intangible_assets_internal`
- 数值差异容差：单侧绝对值的 5%
- LLM 推理 prompt 中应优先引用本文件的 CAS 6 第 9 条和 IAS 38 Paragraph 57

# 参考资料

- 财政部《企业会计准则与国际财务报告准则持续趋同路线图》(2010)
- KPMG《IFRS compared to CAS》最新版 第 X 章
- 沪深交易所 2024 年信息披露监管问答 第 Y 期
```

---

## 3. 编写顺序建议

**Day 1**（必填，演示首批用）：
- 01 商誉减值
- 02 研发支出资本化（**演示主推荐**）
- 09 关联方披露

**Day 2**：04 政府补助 / 05 长期股权投资 / 06 收入确认 / 07 租赁 / 10 同一控制下合并

**Day 3**：03 投资性房地产 / 08 金融工具 / 11 永续债 / 12 ESG / 13 币种 / 14 现金流分类 / 15 分部

## 4. 质量自检清单

每条写完后，按以下检查：

- [ ] 引用了至少 1 条 CAS 准则条款（含条款编号）
- [ ] 引用了至少 1 条 IFRS/HKFRS 准则条款（含 Paragraph 编号）
- [ ] 明确说明了"何时算预期差异、何时不算"
- [ ] 给出了至少 1 个公开年报的实际案例
- [ ] keywords 至少 6 个，覆盖中英双语
- [ ] canonical_key 与 `align/glossary.py` 或 `prompts/extract_keypoints.txt` 中的键名对齐

## 5. 入库

填好后运行：

```bash
python scripts/build_kb.py
```

会自动按文件切片入 ChromaDB（`storage/chroma/`）。
