"""「名称-数值」表对齐自检的判别能力。

同一个信号（H 侧数值出现在 A 侧另一个名称下）对应两件相反的事：整行错位（抽取问题，
不出结论）与整列错乱（真实差异，照常比对）。区分靠同行共位锚点。
"""

from __future__ import annotations

from ahcc.check.table_row_align import alignment_warning, check_row_alignment

# 一张 8 行「名称 → 数值」表，值都有区分度（非整千整万）
_NAMES = ["北京", "上海", "广州", "深圳", "天津", "重庆", "成都", "杭州"]
_A_VALUES = {
    "北京": 810136.0,
    "上海": 443188.0,
    "广州": 338488.0,
    "深圳": 286699.0,
    "天津": 101325.0,
    "重庆": 124849.0,
    "成都": 145884.0,
    "杭州": 270506.0,
}
# 各不相同的锚点（机构数量 + 办公地址）
_A_ANCHORS = {name: (idx + 3, f"{name}市某路{idx + 1}号") for idx, name in enumerate(_NAMES)}


def _rotated(values: dict[str, float]) -> dict[str, float]:
    """把数值整体轮转一位 —— 「列被打乱」与「行错开一行」共用的数值形态。"""
    ordered = [values[n] for n in _NAMES]
    return {name: ordered[(idx + 1) % len(_NAMES)] for idx, name in enumerate(_NAMES)}


def test_column_permutation_with_stable_anchors_is_comparable() -> None:
    """数值列被打乱、同行锚点原位不动 → 真实差异，必须照常比对。"""
    report = check_row_alignment(
        _A_VALUES,
        _rotated(_A_VALUES),
        a_anchors=_A_ANCHORS,
        h_anchors=_A_ANCHORS,  # 锚点两侧完全一致
    )

    assert report.anchors_available
    assert len(report.cross_name_hits) == len(_NAMES)
    assert report.anchor_moved_hits == []
    assert len(report.anchor_stable_hits) == len(_NAMES)
    assert report.value_multiset_equal
    assert report.is_column_permuted
    assert report.comparable
    assert not report.is_misaligned
    assert "整体错乱" in report.reason()


def test_whole_row_shift_is_rejected() -> None:
    """整行错开一行：数值与锚点一起移动 → 抽取错位，整表不出结论。"""
    h_anchors = {
        name: _A_ANCHORS[_NAMES[(idx + 1) % len(_NAMES)]] for idx, name in enumerate(_NAMES)
    }

    report = check_row_alignment(
        _A_VALUES,
        _rotated(_A_VALUES),
        a_anchors=_A_ANCHORS,
        h_anchors=h_anchors,  # 锚点跟着数值一起挪到了下一家
    )

    assert report.anchors_available
    assert len(report.anchor_moved_hits) == len(_NAMES)
    assert report.anchor_stable_hits == []
    assert report.is_misaligned
    assert not report.comparable
    assert not report.is_column_permuted
    assert "行错位" in report.reason()

    warning = alignment_warning(report, table_label="测试表", side_hint="H")
    assert warning["flag"] == "table_row_misaligned"
    assert warning["severity"] == "high"
    assert warning["anchor_moved_hits"] == len(_NAMES)


def test_without_anchors_falls_back_to_conservative_suppression() -> None:
    """没有锚点时无法区分两者，退回旧的保守行为：不出结论，并说明原因。"""
    report = check_row_alignment(_A_VALUES, _rotated(_A_VALUES))

    assert not report.anchors_available
    assert report.is_misaligned
    assert not report.comparable
    assert not report.is_column_permuted
    assert "缺少同行共位锚点" in report.reason()


def test_non_discriminative_anchors_are_treated_as_unavailable() -> None:
    """锚点全都一样（例如整表数量相同、无地址列）时不足以判别，同样保守处理。"""
    flat_anchors = {name: (10, "") for name in _NAMES}

    report = check_row_alignment(
        _A_VALUES, _rotated(_A_VALUES), a_anchors=flat_anchors, h_anchors=flat_anchors
    )

    assert not report.anchors_available
    assert not report.comparable


def test_aligned_table_reports_no_cross_hits() -> None:
    """正常表：同名同值，没有跨名称命中，也就没有任何告警。"""
    report = check_row_alignment(
        _A_VALUES, dict(_A_VALUES), a_anchors=_A_ANCHORS, h_anchors=_A_ANCHORS
    )

    assert report.cross_name_hits == []
    assert report.exact_matches == len(_NAMES)
    assert report.comparable
    assert not report.is_column_permuted
    assert report.reason() == "对齐正常"


def test_single_row_difference_stays_comparable() -> None:
    """只有一行不同、且新值不出现在别的名称下 —— 普通的逐行差异，照常比对。"""
    h_values = dict(_A_VALUES)
    h_values["天津"] = 999123.0

    report = check_row_alignment(
        _A_VALUES, h_values, a_anchors=_A_ANCHORS, h_anchors=_A_ANCHORS
    )

    assert report.cross_name_hits == []
    assert report.comparable
    assert not report.value_multiset_equal


def test_too_few_rows_never_concludes_misalignment() -> None:
    """行数不足时不下错位结论 —— 少数几行的偶然同值支撑不了任何判断。"""
    a = {"北京": 810136.0, "上海": 443188.0}
    h = {"北京": 443188.0, "上海": 810136.0}
    anchors = {"北京": (3, "北京路1号"), "上海": (4, "上海路2号")}

    report = check_row_alignment(a, h, a_anchors=anchors, h_anchors=anchors)

    assert report.too_few_rows
    assert not report.is_misaligned
    assert report.comparable
