"""列键兼容判定 — 所有比对通道共用的「两列是否可比」唯一事实源。

核心原则（「不许静默删检出」约束的算法层表达）：
**列键明确才硬否决，列键缺失永远宽松回退。**
- 任一侧列键为 None → 可比（True）；
- kind：两侧都识别出值种类且不同 → 不可比（金额 vs 增减% / 占比 / 百分点 / 附注号）；
- period：两侧都精确到月日且不等 → 不可比（2025-12-31 列 vs 2025-06-30 列）；
  仅年粒度时比年份；一年粒度一月粒度比年份（无法证伪则宽松）；
- period_role：current vs prior → 不可比（本期/上期互换的 swap 篡改）；
- scope：两侧都识别且不同 → 不可比（合并 vs 母公司）。
"""

from __future__ import annotations

import re

from ahcc.schemas import ColumnKey, ValueKind


def _period_parts(period: str | None) -> tuple[str | None, str | None]:
    """期间串拆为 (年份, 月日/季度部分)。

    '2025-12-31'→('2025','12-31')；'2025'→('2025',None)；
    裸季度/半年键 'Q2'→(None,'Q2')、'H1'→(None,'H1')。
    """
    if not period:
        return None, None
    text = str(period).strip()
    match = re.match(r"((?:19|20)\d{2})(?:-(.*))?$", text)
    if match:
        return match.group(1), match.group(2) or None
    if re.fullmatch(r"[QH][1-4]|H[12]", text):
        return None, text
    return None, None


def periods_pairable(a_period: str | None, b_period: str | None) -> bool:
    """两个期间是否可比。缺失宽松；都精确到月日必须全等；年粒度比年份。"""
    a_year, a_sub = _period_parts(a_period)
    b_year, b_sub = _period_parts(b_period)
    if a_year is None or b_year is None:
        return True
    if a_year != b_year:
        return False
    # 同年：都带月日/季度时必须一致；一方仅年粒度无法证伪 → 宽松
    if a_sub is not None and b_sub is not None:
        return a_sub == b_sub
    return True


def pairable(a: ColumnKey | None, b: ColumnKey | None) -> bool:
    """两个列键是否可比（所有数值配对通道的预检门槛）。"""
    if a is None or b is None:
        return True
    # 值种类：双方都识别且不同 → 不可比。MAIN 与任何非 MAIN 也不可比
    # （金额列的值不能与增减%/占比/百分点/附注号列的值配对）。
    if a.kind != b.kind:
        return False
    # 相对期间：current vs prior 不可比
    if a.period_role and b.period_role and a.period_role != b.period_role:
        return False
    # 绝对期间
    if not periods_pairable(a.period, b.period):
        return False
    # 口径
    if a.scope and b.scope and a.scope != b.scope:
        return False
    return True


def same_kind(a: ColumnKey | None, b: ColumnKey | None) -> bool:
    """值种类是否一致（缺失视为 MAIN，宽松）。"""
    if a is None or b is None:
        return True
    return a.kind == b.kind


def legacy_role_name(key: ColumnKey | None) -> str | None:
    """列键 → 旧 role 字符串（main_value/ratio/note_reference/...）。

    供 _internal_value_role/_metric_value_role 的「先读列键，回退旧逻辑」路径
    使用；列键缺失或 kind==MAIN 时返回 None（由旧逻辑继续判定上下文角色）。
    注意旧词表里没有 CHANGE_PCT/POINTS 的独立 role —— 统一映射到 "ratio"
    族（不可与 main_value 配对的语义一致）。
    """
    if key is None:
        return None
    mapping = {
        ValueKind.NOTE_REF: "note_reference",
        ValueKind.CHANGE_AMOUNT: "change_amount",
        ValueKind.RATIO: "ratio",
        ValueKind.CHANGE_PCT: "ratio",
        ValueKind.POINTS: "ratio",
        ValueKind.EXCHANGE_RATE: "foreign_currency_translation",
    }
    return mapping.get(key.kind)


def is_column_key_informative(key: ColumnKey | None) -> bool:
    """列键是否携带了可用于硬判定的信息（LLM 强制核验的触发条件之一）。"""
    if key is None:
        return False
    return bool(
        key.period
        or key.period_role
        or key.scope
        or key.kind != ValueKind.MAIN
        or key.unit_hint
    )
