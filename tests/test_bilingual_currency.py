"""B1 币种差异处理单元测试：跨币种换算匹配、币种归一化、金额提取带币种。"""

from __future__ import annotations

from ahcc.check.bilingual import (
    _Block,
    _extract_amount_facts,
    _normalize_currency,
    _single_value_match,
)


def test_normalize_currency():
    assert _normalize_currency("RMB") == "CNY"
    assert _normalize_currency("rmb") == "CNY"
    assert _normalize_currency("人民币") == "CNY"
    assert _normalize_currency("¥") == "CNY"
    assert _normalize_currency("HKD") == "HKD"
    assert _normalize_currency("港币") == "HKD"
    assert _normalize_currency("港元") == "HKD"
    assert _normalize_currency("HK$") == "HKD"
    assert _normalize_currency("USD") == "USD"
    assert _normalize_currency("美元") == "USD"
    assert _normalize_currency("") is None
    assert _normalize_currency(None) is None  # type: ignore[arg-type]


def test_cross_currency_match_via_fx():
    # 默认汇率 CNY→HKD=1.08，容差 1%：1000 CNY ≈ 1080 HKD
    assert _single_value_match(1000, 1080, "RMB 1000", "HKD 1080", "CNY", "HKD")
    # 1000 CNY vs 2000 HKD 换算后差异过大，不匹配
    assert not _single_value_match(1000, 2000, "", "", "CNY", "HKD")


def test_same_currency_logic_unchanged():
    # 同币种精确比较
    assert _single_value_match(1000, 1000, "", "", "CNY", "CNY")
    # 不传 currency 时行为不变（向后兼容）
    assert _single_value_match(1000, 1000)
    # 去掉量级因子容忍后，1000 vs 1 不再误匹配（归一化后应精确比较）
    assert not _single_value_match(1000, 1)


def test_no_factor_mismatch_after_normalization():
    """提取已按单位归一化，比对时不再用量级因子容忍，避免真实差异漏检。"""
    # 10,000千元(归一化 10,000,000) vs RMB 10,000(归一化 10,000) → 不再误匹配
    assert not _single_value_match(10_000_000, 10_000, "10,000千元", "RMB 10,000", "CNY", "CNY")
    # 同单位归一化后相等仍匹配
    assert _single_value_match(10_000_000, 10_000_000)


def test_extract_amount_facts_carries_currency():
    block = _Block(index=0, page=1, text="本集团总资产为人民币100亿元", section=None)
    facts = _extract_amount_facts(block)
    amounts = [f for f in facts if f.kind == "amount"]
    assert amounts, "应提取到金额事实"
    assert any(f.currency == "CNY" for f in amounts), [(f.raw, f.currency) for f in amounts]


def test_extract_amount_facts_hkd_currency():
    block = _Block(index=0, page=1, text="Revenue was HK$ 5,000 million", section=None)
    facts = _extract_amount_facts(block)
    amounts = [f for f in facts if f.kind == "amount"]
    assert amounts
    assert any(f.currency == "HKD" for f in amounts), [(f.raw, f.currency) for f in amounts]
