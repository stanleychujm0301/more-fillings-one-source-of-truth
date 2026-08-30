"""通用「名称-数值」表格行比对，带抽取错位自检。

为什么需要错位自检
------------------
`branch_disclosure.py` 在光大银行 A/H 对上稳定产出 40 条「分支机构资产规模不一致」，
README 把它当成亮点能力。实测证伪：

    A 抽到 44 家, H 抽到 44 家, 交集 44
    H[上海分行]=39,540  == A[长春分行]
    H[乌鲁木齐分行]=125,039 == A[太原分行]
    ...
    H 侧的值能在 A 侧的**另一家**分行找到: 40/44 家
    A/H 完全一致: 4/44 家

即 **40/44 的 H 侧数值等于 A 侧另一个名称下的数值** —— 这是名称与数值的行错位
（一侧解析时名称与数值错开了一行），不是 40 处真实不一致。相对差异中位数 70%，
真实银行的 A/H 分行表不可能有这种偏离。

结论：这类「名称→数值」表在比对前必须先自检错位。**错位时整表不出结论**，
改报一条抽取告警 —— 报 40 条假差异比报 0 条差异更有害。

本模块与具体公司、具体表无关，分行/分部/子公司/前十大股东都可以用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from loguru import logger

from ahcc.align.glossary import to_simplified

# H 侧数值能在 A 侧「另一个名称」下找到的比例超过这个值，即判定为行错位。
# 实测真实错位为 0.91；正常表应当接近 0（同名同值，不会跨名称命中）。
MISALIGNMENT_RATIO_THRESHOLD = 0.30

# 样本量太小时不出结论 —— 少数几行的偶然同值不足以支撑任何判断。
MIN_ROWS_FOR_CONCLUSION = 5


@dataclass
class RowAlignmentReport:
    """一次「名称-数值」表比对的对齐诊断。"""

    matched_names: list[str] = field(default_factory=list)
    a_only: list[str] = field(default_factory=list)
    h_only: list[str] = field(default_factory=list)
    cross_name_hits: list[tuple[str, str, float]] = field(default_factory=list)
    exact_matches: int = 0

    @property
    def matched_count(self) -> int:
        return len(self.matched_names)

    @property
    def misalignment_ratio(self) -> float:
        if not self.matched_names:
            return 0.0
        return len(self.cross_name_hits) / len(self.matched_names)

    @property
    def too_few_rows(self) -> bool:
        return self.matched_count < MIN_ROWS_FOR_CONCLUSION

    @property
    def is_misaligned(self) -> bool:
        return (
            not self.too_few_rows
            and self.misalignment_ratio >= MISALIGNMENT_RATIO_THRESHOLD
        )

    @property
    def comparable(self) -> bool:
        """是否可以给出逐行比对结论。

        这是一个**对已检出错位的否决**，而不是「必须先证明对齐才允许比对」。
        行数太少时无法可靠判断错位，但那不构成拒绝比对的理由 ——
        最小规模门槛由调用方的 `_branch_alignment_confident` 单独把关。
        """
        return not self.is_misaligned

    def reason(self) -> str:
        if self.is_misaligned:
            examples = "；".join(
                f"H[{h}]={v:,.0f} 等于 A[{a}]" for h, a, v in self.cross_name_hits[:3]
            )
            return (
                f"疑似名称与数值行错位：{len(self.cross_name_hits)}/{self.matched_count} "
                f"({self.misalignment_ratio:.0%}) 的 H 侧数值出现在 A 侧另一个名称下。{examples}"
            )
        return "对齐正常"


def _is_distinctive_value(value: float) -> bool:
    """这个数值是否「有辨识度」—— 跨名称撞上它才说明是错位而非巧合。

    真实银行分行资产规模长这样：39,540 / 125,039 / 100,509 —— 两个不同分行
    恰好相等的概率极低，因此跨名称命中就是错位的强证据。
    而 90,000 / 70,000 这类整千整万的圆整数在一张表里天然容易重复
    （尤其是测试夹具或粗略披露），跨名称命中只是巧合，不能作为错位判据。
    """
    if value == 0:
        return False
    return abs(value) % 1000 != 0


def normalize_name(name: str) -> str:
    """名称归一化。复用项目已有的 `to_simplified`，替掉各处手写的繁简映射表。"""
    return to_simplified((name or "").strip()).replace(" ", "")


def check_row_alignment(
    a_values: Mapping[str, float],
    h_values: Mapping[str, float],
    *,
    tolerance: float = 1e-9,
) -> RowAlignmentReport:
    """比对前的对齐自检。

    核心判据：**H 侧某个名称的数值，是否等于 A 侧另一个名称的数值。**
    正常表里这种跨名称命中应当极少；大面积出现说明有一侧的名称与数值错开了行。
    """
    a_norm = {normalize_name(k): v for k, v in a_values.items()}
    h_norm = {normalize_name(k): v for k, v in h_values.items()}
    matched = sorted(set(a_norm) & set(h_norm))

    # A 侧「数值 → 名称」反查表
    a_by_value: dict[float, list[str]] = {}
    for name, value in a_norm.items():
        a_by_value.setdefault(round(float(value), 4), []).append(name)

    cross_hits: list[tuple[str, str, float]] = []
    exact = 0
    for name in matched:
        a_val, h_val = float(a_norm[name]), float(h_norm[name])
        if abs(a_val - h_val) <= tolerance:
            exact += 1
            continue
        if not _is_distinctive_value(h_val):
            continue
        owners = a_by_value.get(round(h_val, 4), [])
        other = [o for o in owners if o != name]
        if other:
            cross_hits.append((name, other[0], h_val))

    return RowAlignmentReport(
        matched_names=matched,
        a_only=sorted(set(a_norm) - set(h_norm)),
        h_only=sorted(set(h_norm) - set(a_norm)),
        cross_name_hits=cross_hits,
        exact_matches=exact,
    )


def alignment_warning(report: RowAlignmentReport, *, table_label: str, side_hint: str = "") -> dict:
    """把对齐问题转成一条 module_warning（而不是 N 条假差异）。"""
    return {
        "side": side_hint,
        "flag": "table_row_misaligned" if report.is_misaligned else "table_rows_insufficient",
        "message": f"{table_label}：{report.reason()}",
        "category": "extraction",
        "severity": "high" if report.is_misaligned else "low",
        "blocking": False,
        "matched_rows": report.matched_count,
        "cross_name_hits": len(report.cross_name_hits),
        "misalignment_ratio": round(report.misalignment_ratio, 4),
    }


def log_report(report: RowAlignmentReport, table_label: str) -> None:
    logger.info(
        "{}：A/H 匹配 {} 行，完全一致 {} 行，跨名称命中 {} 行（{:.0%}）→ {}",
        table_label,
        report.matched_count,
        report.exact_matches,
        len(report.cross_name_hits),
        report.misalignment_ratio,
        "可比对" if report.comparable else "不出结论",
    )
