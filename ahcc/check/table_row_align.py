"""通用「名称-数值」表格行比对，带抽取错位自检。

两种长得一模一样、结论却相反的现象
----------------------------------
「H 侧某个名称的数值，等于 A 侧另一个名称的数值」这一信号，同时对应两件事：

1. **整行错位**（抽取问题）：一侧解析时名称与数值错开了一行，整张表的名称→数值
   映射系统性偏移。此时不出结论是对的 —— 报一堆假差异比报 0 条更有害。
2. **整列错乱**（真实差异）：文档里「数值」这一列被整体打乱，而同行的其它字段
   （数量、地址等）原位不动。这是真实的披露不一致，必须报。

只看数值无法区分两者。**判据是同行共位锚点**：错位时锚点跟着数值一起移动，
列错乱时锚点纹丝不动。

实测：光大银行 A/H 分支机构表
------------------------------
早期版本把这一对判成了「整行错位」并整表否决，实际核对三份原始 PDF 后证明是列错乱：

    A(354 页) 抽到 44 家, H(352 页, 2026-03-30 Word 稿) 抽到 44 家, 交集 44
    H[上海分行]=39,540 == A[长春分行]；H[乌鲁木齐分行]=125,039 == A[太原分行] …
    跨名称命中 40/44 家

但是：

    机构数量  两侧一致 44/44
    办公地址  两侧一致 44/44
    跨名称命中中「锚点也跟着移动」的：0/40
    两侧数值多重集完全相等（44 个值，总和均为 5,405,731）—— 纯排列

且官方正式版 H 报告（316 页 InDesign 版）与 A 报告逐行一致，证明 A 侧是正确值、
被核查的 H 稿「资产规模」整列被打乱。结论：那是 40 处真实不一致，不是抽取错位。

本模块与具体公司、具体表无关，分行/分部/子公司/前十大股东都可以用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from loguru import logger

from ahcc.align.glossary import to_simplified

# 跨名称命中（且锚点也跟着移动）的比例超过这个值，即判定为整行错位。
# 正常表应当接近 0（同名同值，不会跨名称命中）。
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
    # 跨名称命中中，同行共位锚点**也跟着移动**到那另一个名称上的（整行错位的证据）
    anchor_moved_hits: list[tuple[str, str, float]] = field(default_factory=list)
    # 跨名称命中中，同行共位锚点**仍与本名称一致**的（数值列独立移动 = 整列错乱）
    anchor_stable_hits: list[tuple[str, str, float]] = field(default_factory=list)
    # 是否提供了有区分度的锚点。为 False 时无法判别两种现象，只能保守处理。
    anchors_available: bool = False
    # 两侧匹配行的数值多重集是否相等（整列被打乱的强信号）
    value_multiset_equal: bool = False

    @property
    def matched_count(self) -> int:
        return len(self.matched_names)

    @property
    def misalignment_ratio(self) -> float:
        if not self.matched_names:
            return 0.0
        return len(self.cross_name_hits) / len(self.matched_names)

    @property
    def anchor_moved_ratio(self) -> float:
        if not self.matched_names:
            return 0.0
        return len(self.anchor_moved_hits) / len(self.matched_names)

    @property
    def too_few_rows(self) -> bool:
        return self.matched_count < MIN_ROWS_FOR_CONCLUSION

    @property
    def is_misaligned(self) -> bool:
        """整行错位 —— 名称与数值错开了一行，整表不出结论。

        有锚点时以「锚点是否跟着数值一起移动」为准；没有锚点时无法区分错位与列错乱，
        退回旧的保守判据（跨名称命中密集即否决）。
        """
        if self.too_few_rows:
            return False
        if self.anchors_available:
            return self.anchor_moved_ratio >= MISALIGNMENT_RATIO_THRESHOLD
        return self.misalignment_ratio >= MISALIGNMENT_RATIO_THRESHOLD

    @property
    def is_column_permuted(self) -> bool:
        """整列错乱 —— 数值列被整体打乱，同行锚点原位不动。这是**真实差异**。"""
        return (
            not self.too_few_rows
            and self.anchors_available
            and not self.is_misaligned
            and self.misalignment_ratio >= MISALIGNMENT_RATIO_THRESHOLD
            and bool(self.anchor_stable_hits)
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
            hits = self.anchor_moved_hits if self.anchors_available else self.cross_name_hits
            examples = "；".join(f"H[{h}]={v:,.0f} 等于 A[{a}]" for h, a, v in hits[:3])
            if self.anchors_available:
                return (
                    f"名称与数值行错位：{len(self.anchor_moved_hits)}/{self.matched_count} "
                    f"({self.anchor_moved_ratio:.0%}) 的 H 侧数值出现在 A 侧另一个名称下，"
                    f"且同行共位锚点也一并移动。{examples}"
                )
            return (
                f"疑似名称与数值行错位：{len(self.cross_name_hits)}/{self.matched_count} "
                f"({self.misalignment_ratio:.0%}) 的 H 侧数值出现在 A 侧另一个名称下；"
                f"因缺少同行共位锚点，无法区分「整行错位」与「整列错乱」，保守起见不出结论。"
                f"{examples}"
            )
        if self.is_column_permuted:
            multiset_note = "，两侧数值多重集完全相等" if self.value_multiset_equal else ""
            return (
                f"数值列整体错乱：{len(self.anchor_stable_hits)}/{self.matched_count} "
                f"({self.misalignment_ratio:.0%}) 的 H 侧数值出现在 A 侧另一个名称下，"
                f"但同行共位锚点两侧一致{multiset_note} —— 系真实差异，非抽取错位。"
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


def _normalize_anchor(anchor: Any) -> Optional[tuple]:
    """把一行的共位锚点归一化成可比对的元组。空/缺失字段一律丢弃。"""
    if anchor is None:
        return None
    parts: Sequence = anchor if isinstance(anchor, (tuple, list)) else (anchor,)
    cleaned = tuple(
        normalize_name(str(p)) if isinstance(p, str) else p
        for p in parts
        if p is not None and str(p).strip() != ""
    )
    return cleaned or None


def _anchors_are_discriminative(anchors: Mapping[str, tuple], names: Sequence[str]) -> bool:
    """锚点必须能区分不同的行，否则「锚点是否移动」这一判据没有意义。

    例如整表机构数量都是 10、又没有地址列时，锚点全部相同，
    「锚点没变」既可能是列错乱也可能是错位，不能据此下结论。
    """
    present = [anchors[n] for n in names if anchors.get(n) is not None]
    if len(present) < MIN_ROWS_FOR_CONCLUSION:
        return False
    # 至少要有一半的行拥有各不相同的锚点
    return len(set(present)) >= max(2, len(present) // 2)


def check_row_alignment(
    a_values: Mapping[str, float],
    h_values: Mapping[str, float],
    *,
    a_anchors: Mapping[str, Any] | None = None,
    h_anchors: Mapping[str, Any] | None = None,
    tolerance: float = 1e-9,
) -> RowAlignmentReport:
    """比对前的对齐自检。

    核心信号：**H 侧某个名称的数值，是否等于 A 侧另一个名称的数值。**
    正常表里这种跨名称命中应当极少。大面积出现时有两种可能，靠 `a_anchors`/
    `h_anchors`（同行共位字段，如机构数量+办公地址）区分：

    - 锚点也跟着移动到那另一个名称上 → 整行错位（抽取问题），整表不出结论；
    - 锚点仍与本名称一致 → 数值列被单独打乱（真实差异），照常比对。

    不传锚点时退回保守行为：跨名称命中密集即否决，与旧版一致。
    """
    a_norm = {normalize_name(k): v for k, v in a_values.items()}
    h_norm = {normalize_name(k): v for k, v in h_values.items()}
    matched = sorted(set(a_norm) & set(h_norm))

    a_anchor_norm: dict[str, Optional[tuple]] = {
        normalize_name(k): _normalize_anchor(v) for k, v in (a_anchors or {}).items()
    }
    h_anchor_norm: dict[str, Optional[tuple]] = {
        normalize_name(k): _normalize_anchor(v) for k, v in (h_anchors or {}).items()
    }
    anchors_available = bool(a_anchor_norm and h_anchor_norm) and _anchors_are_discriminative(
        a_anchor_norm, matched
    )

    # A 侧「数值 → 名称」反查表
    a_by_value: dict[float, list[str]] = {}
    for name, value in a_norm.items():
        a_by_value.setdefault(round(float(value), 4), []).append(name)

    cross_hits: list[tuple[str, str, float]] = []
    anchor_moved: list[tuple[str, str, float]] = []
    anchor_stable: list[tuple[str, str, float]] = []
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
        if not other:
            continue
        hit = (name, other[0], h_val)
        cross_hits.append(hit)
        if not anchors_available:
            continue
        # H 侧本行的锚点，跟的是本名称在 A 侧的行，还是那另一个名称的行？
        h_anchor = h_anchor_norm.get(name)
        own_anchor = a_anchor_norm.get(name)
        other_anchor = a_anchor_norm.get(other[0])
        if h_anchor is None:
            continue
        if own_anchor is not None and h_anchor == own_anchor:
            anchor_stable.append(hit)
        elif other_anchor is not None and h_anchor == other_anchor:
            anchor_moved.append(hit)

    return RowAlignmentReport(
        matched_names=matched,
        a_only=sorted(set(a_norm) - set(h_norm)),
        h_only=sorted(set(h_norm) - set(a_norm)),
        cross_name_hits=cross_hits,
        exact_matches=exact,
        anchor_moved_hits=anchor_moved,
        anchor_stable_hits=anchor_stable,
        anchors_available=anchors_available,
        value_multiset_equal=(
            bool(matched)
            and Counter(round(float(a_norm[n]), 4) for n in matched)
            == Counter(round(float(h_norm[n]), 4) for n in matched)
        ),
    )


def alignment_warning(report: RowAlignmentReport, *, table_label: str, side_hint: str = "") -> dict:
    """把对齐问题转成一条 module_warning（而不是 N 条假差异）。

    整表被否决时**必须**产出这条预警 —— 否则「本表不出结论」对用户完全不可见，
    看上去就是「这张表没有问题」。
    """
    return {
        "side": side_hint,
        "flag": "table_row_misaligned" if report.is_misaligned else "table_rows_insufficient",
        "message": f"{table_label}：{report.reason()}",
        "category": "extraction",
        "severity": "high" if report.is_misaligned else "low",
        "blocking": False,
        "matched_rows": report.matched_count,
        "cross_name_hits": len(report.cross_name_hits),
        "anchor_moved_hits": len(report.anchor_moved_hits),
        "anchors_available": report.anchors_available,
        "misalignment_ratio": round(report.misalignment_ratio, 4),
    }


def log_report(report: RowAlignmentReport, table_label: str) -> None:
    if report.is_column_permuted:
        verdict = "整列错乱（真实差异，照常比对）"
    elif report.comparable:
        verdict = "可比对"
    else:
        verdict = "不出结论"
    logger.info(
        "{}：A/H 匹配 {} 行，完全一致 {} 行，跨名称命中 {} 行（{:.0%}）"
        "[锚点{}｜锚点移动 {} 行｜锚点稳定 {} 行｜多重集{}] → {}",
        table_label,
        report.matched_count,
        report.exact_matches,
        len(report.cross_name_hits),
        report.misalignment_ratio,
        "可用" if report.anchors_available else "不可用",
        len(report.anchor_moved_hits),
        len(report.anchor_stable_hits),
        "相等" if report.value_multiset_equal else "不等",
        verdict,
    )
