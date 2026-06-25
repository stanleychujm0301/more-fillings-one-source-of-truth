"""中英繁体术语对照表 + 简繁转换工具。

设计要点：
1. 每个术语覆盖三种形式：简体中文(zh_cn)、香港繁体(zh_hk)、英文(en)
2. 对齐器在匹配前，自动将 H 股提取的繁体文本转为简体，再与 glossary 匹配
3. 保留原有 CSV 加载接口作为扩展入口
4. 提供模糊匹配支持（如"應收賬款"和"應收帳款"的用字差异）
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class TermEntry:
    """单条术语的三语对照。"""
    canonical_key: str           # 规范化主键，如 "total_assets"
    zh_cn: str                   # 简体中文（A股主要语言）
    zh_hk: str                   # 香港繁体（H股繁体年报用）
    en: str                      # 英文（H股英文年报用）
    aliases: tuple[str, ...] = ()      # 常见别名，如 "总资产" 的别名 "资产总计"


# ============================================================
# 核心财务术语表（35 项）— 内置硬编码，确保对齐器不依赖外部文件也能工作
# ============================================================

CORE_TERMS: list[TermEntry] = [
    TermEntry("total_assets", "资产总计", "資產總計", "Total assets", ("总资产", "資產總額", "资产总额", "資產總額")),
    TermEntry("current_assets", "流动资产合计", "流動資產合計", "Current assets", ("流动资产", "流動資產")),
    TermEntry("non_current_assets", "非流动资产合计", "非流動資產合計", "Non-current assets", ("非流动资产", "非流動資產")),
    TermEntry("total_liabilities", "负债合计", "負債合計", "Total liabilities", ("负债总计", "負債總計", "负债总额", "負債總額")),
    TermEntry("current_liabilities", "流动负债合计", "流動負債合計", "Current liabilities", ("流动负债", "流動負債")),
    TermEntry("non_current_liabilities", "非流动负债合计", "非流動負債合計", "Non-current liabilities", ("非流动负债", "非流動負債")),
    TermEntry("short_term_borrowings", "短期借款", "短期借款", "Short-term borrowings", ("短期贷款", "短期貸款")),
    TermEntry("long_term_borrowings", "长期借款", "長期借款", "Long-term borrowings", ("长期贷款", "長期貸款")),
    TermEntry("equity", "所有者权益合计", "所有者權益合計", "Total equity", ("股东权益", "股東權益", "股东权益合计", "股東權益合計", "股东权益总额", "股東權益總額", "净资产", "淨資產", "权益总额", "權益總額")),
    TermEntry("share_capital", "股本", "股本", "Share capital", ("实收资本", "實收資本", "注册资本", "註冊資本")),
    TermEntry("capital_reserve", "资本公积", "資本公積", "Capital reserve", ("资本公积金", "資本公積金")),
    TermEntry("retained_earnings", "未分配利润", "未分配利潤", "Retained earnings", ("留存收益", "保留盈餘")),
    TermEntry("revenue", "营业收入", "營業收入", "Revenue", ("营业总收入", "營業總收入", "总收入", "總收入", "经营收入", "經營收入", "Turnover", "Operating income", "Total revenue", "Revenue from operations")),
    TermEntry("cost_of_sales", "营业成本", "營業成本", "Cost of sales", ("主营业务成本", "主營業務成本", "销售成本", "銷售成本", "Cost of revenue")),
    TermEntry("gross_profit", "营业毛利", "營業毛利", "Gross profit", ("毛利", "毛利润", "毛利潤")),
    TermEntry("selling_expenses", "销售费用", "銷售費用", "Selling expenses", ("销售及分销费用", "銷售及分銷費用", "Selling and distribution expenses")),
    TermEntry("admin_expenses", "管理费用", "管理費用", "Administrative expenses", ("一般及行政费用", "一般及行政費用", "行政费用", "行政費用", "General and administrative expenses")),
    TermEntry("rd_expenses", "研发费用", "研發費用", "Research and development expenses", ("研究开发费用", "研究開發費用")),
    TermEntry("rnd_capitalized", "研发资本化金额", "研發資本化金額", "R&D capitalized", ("资本化研发支出", "資本化研發支出")),
    TermEntry("rnd_expensed", "研发费用化金额", "研發費用化金額", "R&D expensed", ("费用化研发支出", "費用化研發支出")),
    TermEntry("rnd_total", "研发投入合计", "研發投入合計", "Total R&D expenditure", ("研发支出合计", "研發支出合計", "研发投入总计", "研發投入總計")),
    TermEntry("finance_costs", "财务费用", "財務費用", "Finance costs", ("财务成本", "財務成本", "利息费用", "利息費用", "Finance expenses", "Interest expenses")),
    TermEntry("total_profit", "利润总额", "利潤總額", "Total profit", ("税前利润", "稅前利潤", "除税前溢利", "Profit before tax", "Profit before taxation", "Profit for the year", "Profit attributable to"),),
    TermEntry("income_tax", "所得税费用", "所得稅費用", "Income tax expense", ("所得稅", "税项", "稅項", "Taxation", "Income tax")),
    TermEntry("net_profit", "净利润", "淨利潤", "Net profit", ("归属于母公司股东的净利润", "歸屬於母公司股東的淨利潤", "股东应占溢利", "股東應佔溢利", "Profit for the year", "Net profit for the year")),
    TermEntry("net_profit_attributable", "归属于母公司股东的净利润", "歸屬於母公司股東的淨利潤", "Net profit attributable to parent", ("归母净利润", "歸母淨利潤", "股东应占溢利", "股東應佔溢利", "Profit attributable to shareholders", "Profit attributable to equity holders", "Profit attributable to owners of the parent")),
    TermEntry("operating_profit", "营业利润", "營業利潤", "Operating profit", ("经营利润", "經營利潤")),
    TermEntry("eps_basic", "基本每股收益", "基本每股盈利", "Basic earnings per share", ("每股盈利", "每股盈余", "每股盈餘")),
    TermEntry("eps_diluted", "稀释每股收益", "攤薄每股盈利", "Diluted earnings per share", ("稀释每股盈利", "攤薄每股盈利")),
    TermEntry("operating_cash_flow", "经营活动现金流量净额", "經營活動現金流量淨額", "Net cash from operating activities", (
        "经营活动现金流", "經營活動現金流",
        "经营活动产生的现金流量净额", "經營活動產生的現金流量淨額",
        "经营活动所得现金流量净额", "經營活動所得現金流量淨額",
        "经营活动产生/(所用)的现金流量净额", "經營活動產生/(所用)的現金流量淨額",
        "经营活动产生 / (所用) 的现金流量净额", "經營活動產生 / (所用) 的現金流量淨額",
    )),
    TermEntry("investing_cash_flow", "投资活动现金流量净额", "投資活動現金流量淨額", "Net cash from investing activities", ("投资活动现金流", "投資活動現金流")),
    TermEntry("financing_cash_flow", "筹资活动现金流量净额", "籌資活動現金流量淨額", "Net cash from financing activities", ("筹资活动现金流", "籌資活動現金流", "融资活动现金流", "融資活動現金流")),
    TermEntry("cash_equivalents", "货币资金", "貨幣資金", "Cash and cash equivalents", ("现金及现金等价物", "現金及現金等價物", "银行存款", "銀行存款")),
    TermEntry("cash_equivalents_end", "期末现金及现金等价物余额", "期末現金及現金等價物餘額", "Cash and cash equivalents at end", ("现金及现金等价物期末余额", "現金及現金等價物期末餘額")),
    TermEntry("receivables", "应收账款", "應收賬款", "Trade receivables", ("应收款项", "應收款項", "应收帐款", "應收帳款")),
    TermEntry("inventory", "存货", "存貨", "Inventories", ("库存", "庫存", "库存商品", "庫存商品")),
    TermEntry("goodwill", "商誉", "商譽", "Goodwill", ()),
    TermEntry("intangible_assets", "无形资产", "無形資產", "Intangible assets", ("专利权", "專利權", "商标权", "商標權")),
    TermEntry("fixed_assets", "固定资产", "固定資產", "Property, plant and equipment", ("物业厂房设备", "物業廠房設備", "物业及设备", "物業及設備", "PPE")),
    TermEntry("construction_in_progress", "在建工程", "在建工程", "Construction in progress", ("在建项目", "在建項目")),
    TermEntry("long_term_investments", "长期股权投资", "長期股權投資", "Long-term equity investments", ("长期投资", "長期投資", "股权投资", "股權投資", "对联营企业的投资", "對聯營企業的投資", "对合营企业的投资", "對合營企業的投資", "于联营企业及合营企业的投资", "於聯營企業及合營企業的投資")),
    TermEntry("investment_property", "投资性房地产", "投資性房地產", "Investment property", ("投资物业", "投資物業")),
    TermEntry("lease_liabilities", "租赁负债", "租賃負債", "Lease liabilities", ("租赁负债", "租賃負債")),
    TermEntry("provisions", "预计负债", "預計負債", "Provisions", ("准备", "準備", "拨备", "撥備", "减值准备", "減值準備", "资产减值准备", "資產減值準備")),

    # ---- 证券公司资产负债类 ----
    TermEntry("agency_trading_payable", "代理买卖证券款", "代理買賣證券款", "Agency trading payables", ("代买卖证券款", "代買賣證券款", "客户交易结算资金", "客戶交易結算資金", "Clients' trading settlement funds")),
    TermEntry("settlement_reserves", "结算备付金", "結算備付金", "Settlement reserves", ("结算准备金", "結算準備金", "Settlement provision")),
    TermEntry("margin_financing", "融出资金", "融出資金", "Margin financing receivables", ("融资融券资金", "融資融券資金")),
    TermEntry("reverse_repo", "买入返售金融资产", "買入返售金融資產", "Reverse repurchase agreements", ("买入返售证券", "買入返售證券", "买入返售金融资产", "買入返售金融資產")),
    TermEntry("sell_repo", "卖出回购金融资产", "賣出回購金融資產", "Repurchase agreements", ("卖出回购证券", "賣出回購證券", "卖出回购金融负债", "賣出回購金融負債")),
    TermEntry("trading_financial_assets", "交易性金融资产", "交易性金融資產", "Trading financial assets", ("以公允价值计量且其变动计入当期损益的金融资产", "以公允價值計量且其變動計入當期損益的金融資產")),
    TermEntry("trading_financial_liabilities", "交易性金融负债", "交易性金融負債", "Trading financial liabilities", ("以公允价值计量且其变动计入当期损益的金融负债", "以公允價值計量且其變動計入當期損益的金融負債")),
    TermEntry("derivative_financial_assets", "衍生金融资产", "衍生金融資產", "Derivative financial assets", ("衍生工具资产", "衍生工具資產")),
    TermEntry("derivative_financial_liabilities", "衍生金融负债", "衍生金融負債", "Derivative financial liabilities", ("衍生工具负债", "衍生工具負債")),
    TermEntry("other_debt_investments", "其他债权投资", "其他債權投資", "Other debt investments", ("以摊余成本计量的金融资产", "以攤余成本計量的金融資產", "持有至到期投资", "持有至到期投資")),
    TermEntry("other_equity_investments", "其他权益工具投资", "其他權益工具投資", "Other equity investments", ("以公允价值计量且其变动计入其他综合收益的金融资产", "以公允價值計量且其變動計入其他綜合收益的金融資產")),
    TermEntry("debt_investments", "债权投资", "債權投資", "Debt investments", ()),
    TermEntry("interest_receivable", "应收利息", "應收利息", "Interest receivable", ("应收股利", "應收股利")),
    TermEntry("interest_payable", "应付利息", "應付利息", "Interest payable", ("应付股利", "應付股利")),
    TermEntry("long_term_prepaid", "长期待摊费用", "長期待攤費用", "Long-term prepaid expenses", ("递延资产", "遞延資產")),
    TermEntry("deferred_income", "递延收益", "遞延收益", "Deferred income", ("递延收入", "遞延收入")),
    TermEntry("other_receivables", "其他应收款", "其他應收款", "Other receivables", ("其他应收", "其他應收")),
    TermEntry("other_payables", "其他应付款", "其他應付款", "Other payables", ("其他应付", "其他應付")),
    TermEntry("contract_assets", "合同资产", "合同資產", "Contract assets", ()),
    TermEntry("contract_liabilities", "合同负债", "合同負債", "Contract liabilities", ("合同结算", "合同結算")),
    TermEntry("right_of_use_assets", "使用权资产", "使用權資產", "Right-of-use assets", ()),
    TermEntry("other_comprehensive_income", "其他综合收益", "其他綜合收益", "Other comprehensive income", ("OCI",)),
    TermEntry("minority_interest", "少数股东权益", "少數股東權益", "Non-controlling interests", ("少数股东权益", "少數股東權益", "非控制性权益", "非控制性權益")),

    # ---- 利润类（通用扩展）----
    TermEntry("dividend", "股利", "股利", "Dividends", ("利润分配", "利潤分配", "分红", "分紅", "现金股利", "現金股利", "现金红利", "現金紅利", "dividend", "dividends", "cash dividend", "cash dividends", "dividend distribution", "profit distribution")),
    TermEntry("profit_distribution", "利润分配", "利潤分配", "Profit distribution", ("股利分配", "股利分派", "分红", "分紅", "现金分红", "現金分紅")),
    TermEntry("commission_income", "手续费及佣金收入", "手續費及佣金收入", "Commission and fee income", ("手续费收入", "手續費收入", "佣金收入", "佣金收入")),
    TermEntry("commission_expense", "手续费及佣金支出", "手續費及佣金支出", "Commission and fee expense", ("手续费支出", "手續費支出")),
    TermEntry("commission_net", "手续费及佣金净收入", "手續費及佣金淨收入", "Net commission and fee income", ("手续费净收入", "手續費淨收入")),
    TermEntry("interest_income", "利息收入", "利息收入", "Interest income", ("利息收益", "利息收益")),
    TermEntry("interest_expense", "利息支出", "利息支出", "Interest expense", ("利息成本", "利息成本")),
    TermEntry("interest_net", "利息净收入", "利息淨收入", "Net interest income", ("利息净收入", "利息淨收入", "净利息收入", "淨利息收入")),
    TermEntry("investment_income", "投资收益", "投資收益", "Investment income", ("投资收益", "投資收益", "投资净收益", "投資淨收益")),
    TermEntry("fair_value_change", "公允价值变动收益", "公允價值變動收益", "Fair value change income", ("公允价值变动损益", "公允價值變動損益", "公允价值变动", "公允價值變動")),
    TermEntry("other_income", "其他业务收入", "其他業務收入", "Other income", ("其他收入", "其他收入")),
    TermEntry("other_operating_income", "其他收益", "其他收益", "Other operating income", ("营业外收入", "營業外收入")),
    TermEntry("other_operating_cost", "其他业务成本", "其他業務成本", "Other operating cost", ("其他业务支出", "其他業務支出")),
    TermEntry("credit_impairment_loss", "信用减值损失", "信用減值損失", "Credit impairment loss", ("减值损失", "減值損失")),
    TermEntry("asset_impairment_loss", "资产减值损失", "資產減值損失", "Asset impairment loss", ("资产减值", "資產減值")),
    TermEntry("income_tax_expense", "所得税费用", "所得稅費用", "Income tax expense", ("所得税", "所得稅", "税项", "稅項")),

    # ---- 所有者权益类扩展 ----
    TermEntry("general_risk_reserve", "一般风险准备", "一般風險準備", "General risk reserve", ("一般风险准备金", "一般風險準備金")),
    TermEntry("trading_risk_reserve", "交易风险准备", "交易風險準備", "Trading risk reserve", ("交易风险准备金", "交易風險準備金", "证券交易风险准备", "證券交易風險準備")),
    TermEntry("surplus_reserve", "盈余公积", "盈餘公積", "Surplus reserve", ("法定盈余公积", "法定盈餘公積", "任意盈余公积", "任意盈餘公積")),
    TermEntry("special_reserve", "专项储备", "專項儲備", "Special reserve", ("安全生产费", "安全生產費")),
    TermEntry("treasury_stock", "库存股", "庫存股", "Treasury stock", ()),
    TermEntry("other_equity_reserve", "其他权益工具", "其他權益工具", "Other equity instruments", ("优先股", "優先股", "永续债", "永續債")),
    TermEntry("fx_translation_reserve", "外币报表折算差额", "外幣報表折算差額", "Foreign currency translation reserve", ()),
    TermEntry("perpetual_bond", "永续债", "永續債", "Perpetual bonds", ("永续中期票据", "永續中期票據")),
    TermEntry("preferred_stock", "优先股", "優先股", "Preferred stock", ()),
    TermEntry("additional_paid_in_capital", "资本公积", "資本公積", "Additional paid-in capital", ("股本溢价", "股本溢價", "资本溢价", "資本溢價")),

    # ---- 现金流类扩展 ----
    TermEntry("operating_cash_inflow", "经营活动现金流入小计", "經營活動現金流入小計", "Cash inflows from operating activities", ()),
    TermEntry("operating_cash_outflow", "经营活动现金流出小计", "經營活動現金流出小計", "Cash outflows from operating activities", ()),
    TermEntry("investing_cash_inflow", "投资活动现金流入小计", "投資活動現金流入小計", "Cash inflows from investing activities", ()),
    TermEntry("investing_cash_outflow", "投资活动现金流出小计", "投資活動現金流出小計", "Cash outflows from investing activities", ()),
    TermEntry("financing_cash_inflow", "筹资活动现金流入小计", "籌資活動現金流入小計", "Cash inflows from financing activities", ()),
    TermEntry("financing_cash_outflow", "筹资活动现金流出小计", "籌資活動現金流出小計", "Cash outflows from financing activities", ()),
    TermEntry("fx_effect_on_cash", "汇率变动对现金的影响", "匯率變動對現金的影響", "Effect of exchange rate changes on cash", ()),
    TermEntry("cash_at_beginning", "期初现金及现金等价物余额", "期初現金及現金等價物餘額", "Cash and cash equivalents at beginning", ()),
    TermEntry("cash_increase", "现金及现金等价物净增加额", "現金及現金等價物淨增加額", "Net increase in cash and cash equivalents", ()),
    TermEntry("depreciation", "折旧", "折舊", "Depreciation", ("折旧费用", "折舊費用", "折旧及摊销", "折舊及攤銷")),
    TermEntry("amortization", "摊销", "攤銷", "Amortization", ("摊销费用", "攤銷費用")),

    # ---- 监管指标 ----
    TermEntry("net_capital", "净资本", "淨資本", "Net capital", ()),
    TermEntry("risk_coverage_ratio", "风险覆盖率", "風險覆蓋率", "Risk coverage ratio", ()),
    TermEntry("capital_leverage_ratio", "资本杠杆率", "資本槓桿率", "Capital leverage ratio", ()),
    TermEntry("liquidity_coverage_ratio", "流动性覆盖率", "流動性覆蓋率", "Liquidity coverage ratio", ("LCR",)),
    TermEntry("net_stable_funding_ratio", "净稳定资金率", "淨穩定資金率", "Net stable funding ratio", ("NSFR",)),

    # ---- 每股类扩展 ----
    TermEntry("net_asset_per_share", "每股净资产", "每股淨資產", "Net asset value per share", ("每股权益", "每股權益")),
    TermEntry("operating_cash_per_share", "每股经营活动产生的现金流量净额", "每股經營活動產生的現金流量淨額", "Operating cash flow per share", ()),
    TermEntry("dividend_per_share", "每股股利", "每股股利", "Dividend per share", ("每股现金股利", "每股現金股利", "每股派息", "每股派息")),
    TermEntry("dividend_total", "股利总额", "股利總額", "Total dividends", ("现金股利总额", "現金股利總額", "共计股利", "共計股利", "Total amount of cash dividends", "total dividends amounting to")),
    TermEntry("dividend_base_share_count", "利润分配股本基数", "利潤分配股本基數", "Shares for dividend distribution", ("股本总额", "股本總額", "总股本", "總股本", "total outstanding shares", "total share capital")),
    TermEntry("dividend_rate_per_10_shares", "每10股派息", "每10股派息", "Dividend per 10 shares", ("每10股现金股利", "每10股現金股利", "Amount of dividend for every 10 shares", "per 10 shares")),

    # ---- 分部类 ----
    TermEntry("brokerage_income", "经纪业务手续费净收入", "經紀業務手續費淨收入", "Brokerage commission income", ("证券经纪业务收入", "證券經紀業務收入")),
    TermEntry("investment_banking_income", "投资银行业务手续费净收入", "投資銀行業務手續費淨收入", "Investment banking fee income", ("投行业务收入", "投行業務收入")),
    TermEntry("asset_mgmt_income", "资产管理业务手续费净收入", "資產管理業務手續費淨收入", "Asset management fee income", ("资管业务收入", "資管業務收入")),
    TermEntry("proprietary_income", "自营业务收入", "自營業務收入", "Proprietary trading income", ("自营证券投资收益", "自營證券投資收益")),
    TermEntry("credit_business_income", "信用业务收入", "信用業務收入", "Credit business income", ("融资融券利息收入", "融資融券利息收入")),
    TermEntry("margin_loans", "融出资金", "融出資金", "Margin lending", ("融资融券业务融出资金", "融資融券業務融出資金")),
    TermEntry("client_margin_deposit", "客户保证金", "客戶保證金", "Client margin deposit", ("客户交易保证金", "客戶交易保證金")),

    # ---- 银行类 ----
    TermEntry("customer_loans", "客户贷款及垫款", "客戶貸款及墊款", "Customer loans and advances", ("发放贷款及垫款", "發放貸款及墊款", "贷款及垫款", "貸款及墊款")),
    TermEntry("customer_deposits", "吸收存款", "吸收存款", "Customer deposits", ("客户存款", "客戶存款")),
    TermEntry("interbank_deposits", "同业存放", "同業存放", "Interbank deposits", ("同业存放款项", "同業存放款項")),
    TermEntry("central_bank_deposits", "存放中央银行款项", "存放中央銀行款項", "Deposits with central bank", ()),
    TermEntry("loan_loss_provisions", "贷款减值准备", "貸款減值準備", "Loan impairment provisions", ("贷款损失准备", "貸款損失準備", "贷款拨备", "貸款撥備")),

    # ---- 保险类 ----
    TermEntry("insurance_contract_reserves", "保险合同准备金", "保險合同準備金", "Insurance contract reserves", ("未到期责任准备金", "未到期責任準備金")),
    TermEntry("ceded_premium", "分出保费", "分出保費", "Ceded premium", ("分保费", "分保費")),
    TermEntry("insurance_claim_expense", "赔付支出", "賠付支出", "Insurance claim expense", ("理赔支出", "理賠支出")),
    TermEntry("surrender_value", "退保金", "退保金", "Surrender value", ()),
    TermEntry("policy_holder_deposit", "保户储金及投资款", "保戶儲金及投資款", "Policyholder deposits and investment funds", ()),
    TermEntry("reinsurance_receivable", "应收分保账款", "應收分保賬款", "Reinsurance receivables", ()),
    TermEntry("reinsurance_payable", "应付分保账款", "應付分保賬款", "Reinsurance payables", ()),
    TermEntry("insurance_income", "已赚保费", "已賺保費", "Earned premium", ()),
    TermEntry("premium_income", "保险业务收入", "保險業務收入", "Premium income", ("原保险保费收入", "原保險保費收入")),

    # ---- 制造类 ----
    TermEntry("raw_materials", "原材料", "原材料", "Raw materials", ("原料", "原料")),
    TermEntry("work_in_progress", "在产品", "在產品", "Work in progress", ("在制品", "在製品")),
    TermEntry("finished_goods", "产成品", "產成品", "Finished goods", ("库存商品", "庫存商品")),
    TermEntry("cost_of_revenue", "主营业务成本", "主營業務成本", "Cost of revenue", ("营业成本", "營業成本", "主营业务支出", "主營業務支出")),
    TermEntry("main_business_income", "主营业务收入", "主營業務收入", "Main business income", ("主营业务收入", "主營業務收入")),
    TermEntry("contract_work_in_progress", "合同履约成本", "合同履約成本", "Contract fulfillment costs", ()),
    TermEntry("biological_assets", "生产性生物资产", "生產性生物資產", "Biological assets", ()),
    TermEntry("oil_gas_assets", "油气资产", "油氣資產", "Oil and gas assets", ()),
    TermEntry("development_expenses", "开发支出", "開發支出", "Development expenditure", ()),
    TermEntry("deferred_tax_assets", "递延所得税资产", "遞延所得稅資產", "Deferred tax assets", ()),
    TermEntry("deferred_tax_liabilities", "递延所得税负债", "遞延所得稅負債", "Deferred tax liabilities", ()),
    TermEntry("employee_benefit_payable", "应付职工薪酬", "應付職工薪酬", "Employee benefits payable", ("应付工资", "應付工資", "应付福利", "應付福利")),
    TermEntry("tax_payable", "应交税费", "應交稅費", "Taxes payable", ("应交税金", "應交稅金")),
    TermEntry("bonds_payable", "应付债券", "應付債券", "Bonds payable", ("公司债券", "公司債券")),
    TermEntry("long_term_payables", "长期应付款", "長期應付款", "Long-term payables", ()),
    TermEntry("government_grants", "政府补助", "政府補助", "Government grants", ("补贴收入", "補貼收入")),
    TermEntry("asset_disposal_income", "资产处置收益", "資產處置收益", "Asset disposal income", ()),
    TermEntry("operating_profit", "营业利润", "營業利潤", "Operating profit", ("经营利润", "經營利潤")),
    TermEntry("non_operating_income", "营业外收入", "營業外收入", "Non-operating income", ()),
    TermEntry("non_operating_expense", "营业外支出", "營業外支出", "Non-operating expense", ()),
    TermEntry("other_comprehensive_income_fv", "其他综合收益—公允价值变动", "其他綜合收益—公允價值變動", "OCI - Fair value change", ()),
    TermEntry("other_comprehensive_income_fx", "其他综合收益—外币折算差额", "其他綜合收益—外幣折算差額", "OCI - Foreign currency translation", ()),
    TermEntry("cash_dividend_paid", "分配股利支付的现金", "分配股利支付的現金", "Cash dividends paid", ()),
    TermEntry("capital_expenditure", "购建固定资产支付的现金", "購建固定資產支付的現金", "Capital expenditure", ("资本支出", "資本支出")),

    # ---- 房地产类 ----
    TermEntry("development_costs", "开发成本", "開發成本", "Development costs", ()),
    TermEntry("development_products", "开发产品", "開發產品", "Development products", ()),
    TermEntry("land_use_right", "土地使用权", "土地使用權", "Land use rights", ()),
    TermEntry("real_estate_sales", "房地产销售收入", "房地產銷售收入", "Real estate sales revenue", ()),
    TermEntry("property_management_income", "物业管理收入", "物業管理收入", "Property management income", ()),

    # ---- 通用补充 ----
    TermEntry("accounts_payable", "应付账款", "應付賬款", "Accounts payable", ("应付款项", "應付款項", "应付帐款", "應付帳款")),
    TermEntry("prepayments", "预付款项", "預付款項", "Prepayments", ("预付账款", "預付賬款")),
    TermEntry("advance_from_customers", "合同负债", "合同負債", "Advances from customers", ("预收款项", "預收款項", "预收账款", "預收賬款")),
    TermEntry("bill_receivable", "应收票据", "應收票據", "Notes receivable", ()),
    TermEntry("bill_payable", "应付票据", "應付票據", "Notes payable", ()),
    TermEntry("other_current_assets", "其他流动资产", "其他流動資產", "Other current assets", ()),
    TermEntry("other_non_current_assets", "其他非流动资产", "其他非流動資產", "Other non-current assets", ()),
    TermEntry("other_current_liabilities", "其他流动负债", "其他流動負債", "Other current liabilities", ()),
    TermEntry("other_non_current_liabilities", "其他非流动负债", "其他非流動負債", "Other non-current liabilities", ()),
    TermEntry("parent_equity", "归属于母公司所有者权益合计", "歸屬於母公司所有者權益合計", "Equity attributable to parent", ("归属于母公司股东权益", "歸屬於母公司股東權益")),
    TermEntry("total_comprehensive_income", "综合收益总额", "綜合收益總額", "Total comprehensive income", ()),
    TermEntry("comprehensive_income_parent", "归属于母公司所有者的综合收益总额", "歸屬於母公司所有者的綜合收益總額", "Comprehensive income attributable to parent", ()),
]


# ============================================================
# 简繁转换字典（轻量级，覆盖财务领域高频差异字）
# 若需完整转换，建议安装 opencc-python: pip install opencc-python-reimplemented
# ============================================================

S2T_OVERRIDES: dict[str, str] = {
    # 财务领域常见简繁差异（注意香港繁体与台湾繁体的区别）
    "账": "賬",
    "帐": "帳",
    "资": "資",
    "产": "產",
    "负债": "負債",
    "权益": "權益",
    "利润": "利潤",
    "费用": "費用",
    "现金": "現金",
    "银行": "銀行",
    "存货": "存貨",
    "投资": "投資",
    "租赁": "租賃",
    "折旧": "折舊",
    "摊销": "攤銷",
    "准备": "準備",
    "盈余": "盈餘",
    "注册资本": "註冊資本",
    "账面": "賬面",
    "账目": "賬目",
    "应占": "應佔",
    "归属": "歸屬",
    "汇兑": "匯兌",
    "汇兑": "滙兌",
    "储蓄": "儲蓄",
    "贷": "貸",
    "赁": "賃",
    "赃": "賬",
    "购": "購",
    "赎": "贖",
    "赠": "贈",
}


_OPENCC_CONVERTER_S2T = None
_OPENCC_CONVERTER_T2S = None


def _get_s2t_converter():
    global _OPENCC_CONVERTER_S2T
    if _OPENCC_CONVERTER_S2T is None:
        try:
            import opencc
            _OPENCC_CONVERTER_S2T = opencc.OpenCC("s2hk")
        except Exception:
            _OPENCC_CONVERTER_S2T = False
    return _OPENCC_CONVERTER_S2T


def _get_t2s_converter():
    global _OPENCC_CONVERTER_T2S
    if _OPENCC_CONVERTER_T2S is None:
        try:
            import opencc
            _OPENCC_CONVERTER_T2S = opencc.OpenCC("hk2s")
        except Exception:
            _OPENCC_CONVERTER_T2S = False
    return _OPENCC_CONVERTER_T2S


def to_traditional(text: str) -> str:
    """简体中文 → 香港繁体（优先 opencc，回退轻量级字典）。"""
    conv = _get_s2t_converter()
    if conv:
        return conv.convert(text)
    pairs = sorted(S2T_OVERRIDES.items(), key=lambda x: len(x[0]), reverse=True)
    result = text
    for sc, tc in pairs:
        result = result.replace(sc, tc)
    return result


def to_simplified(text: str) -> str:
    """香港繁体 → 简体中文（优先 opencc，回退轻量级字典）。"""
    conv = _get_t2s_converter()
    if conv:
        return conv.convert(text)
    t2s = {v: k for k, v in S2T_OVERRIDES.items()}
    t2s["滙"] = "汇"
    t2s["佔"] = "占"
    pairs = sorted(t2s.items(), key=lambda x: len(x[0]), reverse=True)
    result = text
    for tc, sc in pairs:
        result = result.replace(tc, sc)
    return result


# ============================================================
# Glossary 查询引擎（供 matcher.py 调用）
# ============================================================

class Glossary:
    """术语表查询引擎。

    支持三种输入形式：
    - 简体中文（A股直接输入）
    - 香港繁体（H股繁体年报，自动转简体后匹配）
    - 英文（H股英文年报，直接匹配）
    """

    def __init__(self) -> None:
        self._by_canonical: dict[str, TermEntry] = {t.canonical_key: t for t in CORE_TERMS}
        # 反向索引：任意语言形式 -> canonical_key
        self._to_canonical: dict[str, str] = {}
        for t in CORE_TERMS:
            for form in [t.zh_cn, t.zh_hk, t.en, *t.aliases]:
                self._to_canonical[form.lower()] = t.canonical_key
                # 同时索引简体和繁体版本，确保无论输入什么形式都能命中
                self._to_canonical[to_simplified(form).lower()] = t.canonical_key
                self._to_canonical[to_traditional(form).lower()] = t.canonical_key

    def lookup(self, text: str) -> Optional[str]:
        """将任意语言的术语文本转为 canonical_key。

        匹配策略：
        1. 精确匹配（忽略大小写）
        2. 若输入为繁体，先转简体再匹配
        3. 若输入包含于更长的别名中，尝试部分匹配（后续可扩展 fuzzy）
        """
        key = text.strip().lower()
        if key in self._to_canonical:
            return self._to_canonical[key]
        # 尝试简繁转换后匹配
        key_s = to_simplified(key)
        if key_s in self._to_canonical:
            return self._to_canonical[key_s]
        key_t = to_traditional(key)
        if key_t in self._to_canonical:
            return self._to_canonical[key_t]
        return None

    def get_entry(self, canonical_key: str) -> Optional[TermEntry]:
        return self._by_canonical.get(canonical_key)

    def all_canonical_keys(self) -> list[str]:
        return list(self._by_canonical.keys())


# 模块级单例
glossary = Glossary()


def normalize_term(text: str) -> Optional[str]:
    """快捷入口：文本 -> canonical_key。"""
    return glossary.lookup(text)


def get_term(canonical_key: str) -> Optional[TermEntry]:
    """快捷入口：canonical_key -> TermEntry。"""
    return glossary.get_entry(canonical_key)


# ============================================================
# 向后兼容接口（保留原有 CSV 加载逻辑）
# ============================================================

@lru_cache(maxsize=1)
def load_glossary() -> dict[str, dict[str, str]]:
    """加载词典：返回 {canonical_key: {zh, en, ...}}"""
    csv_path = Path(__file__).resolve().parents[2] / "kb" / "glossary_zh_en.csv"
    if not csv_path.exists():
        return {}
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["canonical_key"]: row for row in reader}


def resolve_by_zh(zh_term: str) -> str | None:
    """根据中文术语找规范键（优先内置 glossary，fallback 到 CSV）。"""
    canonical = normalize_term(zh_term)
    if canonical:
        return canonical
    # fallback 到 CSV
    for key, row in load_glossary().items():
        if row.get("zh") == zh_term:
            return key
    return None


def resolve_by_en(en_term: str) -> str | None:
    """根据英文术语找规范键（优先内置 glossary，fallback 到 CSV）。"""
    canonical = normalize_term(en_term)
    if canonical:
        return canonical
    # fallback 到 CSV
    en_lower = en_term.lower()
    for key, row in load_glossary().items():
        if row.get("en", "").lower() == en_lower:
            return key
    return None
