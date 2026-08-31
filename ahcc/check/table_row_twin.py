"""无标签表格行孪生比对 — edit/swap 类篡改的主力检出引擎。

为什么需要这个模块
------------------
注入评估（ahcc/eval/inject.py）实证：90 个注入目标里只有约 8 个落在 glossary
映射行上 —— 任何依赖术语表的检测路径（profile 画像、key_metric 精确差异）
召回天花板都远低于 50%。本模块完全不依赖 glossary，直接在解析后的表格结构
（ReportDocument.tables）上做行级孪生比对。

适用场景与噪声免疫设计
----------------------
主战场是「同一文档的两个版本对查」（注入评估、篡改复核）：双侧表格结构一致，
锚点匹配率 ≈ 1.0，篡改点以「同行同列取值不同」的形式暴露。

真实 A/H 跨报告对不是本模块的主战场，但也**不会**被简单放过或误伤：
- 行标签集合 Jaccard 门槛过滤掉版式迥异的表（CAS vs IFRS 行项目不同）；
- 单位/币种因子估计（10^k 或中位数比例）把「A 千元 vs H 百万」这类纯列示
  差异解释掉，只有因子解释不了的残差才报出 —— 那恰恰是真实跨报告不一致；
- 每张表配对要求 ≥90% 的匹配行在因子下吻合，错位/错配的表整表放弃；
- 每表最多 5 条、每任务最多 40 条熔断 —— 篡改是稀疏事件，超量即配对错误。

「稀疏」假设的例外：整列被打乱
------------------------------
上面两道闸都假设篡改稀疏。**整列被打乱**恰好违反该假设 —— 几乎每行都不同，
却没有出现任何一个新值（H 侧数值集合与 A 侧完全相同，只是配错了行）。
实测光大银行分支机构资产规模表即为此形态（44 行错 40 行）。
`_is_column_permutation` 识别这种形态并让它绕过低锚点/超量两道闸，
按真实差异出报，summary 里注明「整列错乱」。

产出 rule_id="table_row_value_conflict"，triage="real"，供评估匹配器
（ahcc/eval/matcher.py：页码 ±2 + 篡改值 token + triage=real + severity≥medium）
直接命中。
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from loguru import logger

from ahcc.align.glossary import to_simplified
from ahcc.check.explanation import make_value_explanation
from ahcc.profile.extract_metrics import (
    _find_label_column,
    _header_text_for_column,
    _looks_like_label,
    _parse_number,
    _period_for_table_cell,
)
from ahcc.schemas import (
    Diff,
    DiffScope,
    DiffSeverity,
    DiffType,
    Evidence,
    FinancialTable,
    LocalizedString,
    ReportDocument,
    ReportSide,
)

RULE_ID = "table_row_value_conflict"

# ---- 配对与命中门槛 -------------------------------------------------------
_MIN_MATCHED_KEYS = 8        # 两表共有的 (标签, 列角色) 键数下限
_MIN_LABEL_JACCARD = 0.6     # 行标签集合相似度下限（同版式信号）
_MIN_ANCHOR_RATIO = 0.9      # 匹配行中「因子下吻合」的比例下限
_REL_TOL = 1e-3              # 数值吻合容差（0.1%）
_DIFF_MIN_REL = 1e-3         # 残差超过 0.1% 才报差异
_MAX_DIFFS_PER_TABLE = 5     # 篡改是稀疏事件：单表超量 => 配对错误，整表放弃
_MAX_DIFFS_PER_JOB = 40      # 任务级熔断
_MIN_LABEL_LEN = 3           # 简化后标签最少字符数（净利润/总资产均为 3 字；泛化标签由停用表拦截）
_MIN_DIGITS = 4              # 数值最少有效位数（与 inject._MIN_DIGITS 对齐）
_MAX_CANDIDATE_TABLES = 10   # 每张 A 表最多评估的 H 候选表数

# 泛化标签没有配对价值：多行共享同一标签时无法判定哪行对哪行
_GENERIC_LABELS = {
    "合计", "小计", "总计", "共计", "总計", "合計",
    "total", "subtotal", "grand total",
}

# 单位/币种因子候选：先 1.0（同文档同单位），再 10^k（千元/百万换算），最后中位数比例
_FACTOR_CANDIDATES = (1.0, 1e3, 1e2, 1e1, 1e4, 1e-1, 1e-2, 1e-3, 1e-4)


@dataclass
class _RowValue:
    """表格中某 (标签, 列角色) 位置上的一个数值。"""

    label: str           # 简化后的行标签
    role: str            # 列角色：期间（"2024"）或位置（"col2"）
    value: float
    raw: str             # 原始文本（保留千分位，用于展示）
    page: int
    snippet: str         # 行文本证据


@dataclass
class _TableRows:
    table: FinancialTable
    rows: dict[tuple[str, str], _RowValue] = field(default_factory=dict)  # (label, role) -> value
    labels: set[str] = field(default_factory=set)


@dataclass
class TwinCheckStats:
    tables_a: int = 0
    tables_h: int = 0
    candidate_pairs: int = 0
    paired_tables: int = 0
    skipped_low_anchor: int = 0
    dropped_overflow_tables: int = 0
    permuted_tables: int = 0
    diffs_emitted: int = 0
    diffs_capped: int = 0


def _digit_count(text: str) -> int:
    return sum(1 for ch in text if ch.isdigit())


def _eligible_label(label: str) -> bool:
    if len(label) < _MIN_LABEL_LEN:
        return False
    return label.lower() not in _GENERIC_LABELS


def _build_table_rows(table: FinancialTable) -> _TableRows:
    """把一张表的 cells 还原成 (标签, 列角色) -> 数值 的行映射。"""
    result = _TableRows(table=table)
    if not table.cells:
        return result

    rows: dict[int, list] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)

    for row_idx in sorted(rows.keys()):
        cells = sorted(rows[row_idx], key=lambda c: c.col)
        if any(c.is_header for c in cells):
            continue
        found = _find_label_column(cells)
        if not found:
            continue
        label_col, raw_label = found
        label = to_simplified(re.sub(r"\s+", "", raw_label))
        if not _eligible_label(label):
            continue
        result.labels.add(label)
        row_text = " | ".join(c.text.strip() for c in cells if c.text.strip())[:200]

        for cell in cells:
            if cell.col == label_col or not cell.text.strip():
                continue
            if _looks_like_label(cell.text):
                continue
            value = _parse_number(cell.text)
            if value is None or value == 0:
                continue
            if _digit_count(cell.text) < _MIN_DIGITS:
                continue
            header_text = _header_text_for_column(rows, row_idx, cell.col)
            role = _period_for_table_cell(table, header_text) or f"col{cell.col}"
            key = (label, role)
            # 同表同键重复（合并单元格/重复行）时保留首个，避免歧义配对
            if key in result.rows:
                continue
            result.rows[key] = _RowValue(
                label=label,
                role=role,
                value=value,
                raw=cell.text.strip(),
                page=table.page,
                snippet=row_text,
            )
    return result


def _rel_delta(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-9)


def _best_factor(pairs: list[tuple[float, float]]) -> tuple[Optional[float], int]:
    """在候选因子中找能让最多键吻合的因子。

    返回 (因子, 该因子下吻合的键数)。1.0 优先 —— 同文档对查不需要任何缩放；
    10^k 覆盖千元/百万元列示差；都不行时试中位数比例（币种折算等非整幂场景）。
    """
    candidates = list(_FACTOR_CANDIDATES)
    ratios = sorted(a / h for a, h in pairs if h)
    if ratios:
        candidates.append(ratios[len(ratios) // 2])

    best_factor: Optional[float] = None
    best_hits = 0
    for factor in candidates:
        if factor <= 0:
            continue
        hits = sum(1 for a, h in pairs if _rel_delta(a, h * factor) <= _REL_TOL)
        if hits > best_hits:
            best_factor, best_hits = factor, hits
    return best_factor, best_hits


def _is_column_permutation(pairs: list[tuple[float, float]]) -> bool:
    """两侧取值是否互为排列 —— 「整列被打乱」型篡改的判据。

    低锚点跳过与超量放弃这两道闸都建立在「篡改是稀疏事件」的假设上，
    整列被打乱恰好违反该假设：几乎每行都不同，却没有任何一个新值出现 ——
    H 侧数值集合与 A 侧完全相同，只是配错了行。这种密集差异是真的，不是噪声。

    要求多重集**完全**相等（不允许缺行）、样本量够大、且值有区分度，
    避免整千整万的圆整数表凑巧命中。
    """
    from ahcc.check.table_row_align import _is_distinctive_value

    if len(pairs) < _MIN_MATCHED_KEYS:
        return False
    a_values = [a for a, _ in pairs]
    h_values = [h for _, h in pairs]
    if sum(1 for v in a_values if _is_distinctive_value(v)) < len(a_values) * 0.6:
        return False
    if Counter(round(v, 4) for v in a_values) != Counter(round(v, 4) for v in h_values):
        return False
    # 至少要有两行真的错位，否则「排列」只是全等表
    return sum(1 for a, h in pairs if _rel_delta(a, h) > _DIFF_MIN_REL) >= 2


def _pair_score_label_overlap(a: _TableRows, h: _TableRows) -> float:
    if not a.labels or not h.labels:
        return 0.0
    inter = len(a.labels & h.labels)
    union = len(a.labels | h.labels)
    return inter / union if union else 0.0


def _make_diff(a_row: _RowValue, h_row: _RowValue, factor: float, *, permuted: bool = False) -> Diff:
    h_display_value = h_row.value
    delta = abs(a_row.value - h_display_value)
    rel = _rel_delta(a_row.value, h_display_value)
    severity = DiffSeverity.HIGH if rel >= 0.05 else DiffSeverity.MEDIUM
    evidence = [
        Evidence(side=ReportSide.A_SHARE, page=a_row.page, bbox=None,
                 snippet=a_row.snippet, section=None),
        Evidence(side=ReportSide.H_SHARE, page=h_row.page, bbox=None,
                 snippet=h_row.snippet, section=None),
    ]
    unit_note = "" if factor == 1.0 else f"（已按因子 {factor:g} 归一列示单位后仍不一致）"
    permuted_note = (
        "（该表取值整列错乱：两侧数值集合完全相同，但对应的行不同）" if permuted else ""
    )
    summary_zh = (
        f"{a_row.label}（{a_row.role}）：A 股第{a_row.page}页为 {a_row.raw}，"
        f"H 股第{h_row.page}页为 {h_row.raw}，相对差异 {rel*100:.2f}%{unit_note}{permuted_note}"
    )
    return Diff(
        diff_id=f"ROWTWIN_{uuid.uuid4().hex[:8]}",
        diff_type=DiffType.NUMERIC,
        diff_scope=DiffScope.CROSS_REPORT,
        severity=severity,
        triage="real",
        canonical_key=None,
        topic=LocalizedString(zh=a_row.label, en=a_row.label),
        summary=LocalizedString(zh=summary_zh, en=summary_zh),
        a_value=a_row.value,
        h_value=h_display_value,
        delta=delta,
        tolerance=_REL_TOL,
        evidence=evidence,
        rule_id=RULE_ID,
        diff_explanation=make_value_explanation(
            headline=f"{a_row.label} 表格行数值冲突",
            label=a_row.label,
            role=a_row.role,
            a_value=a_row.value,
            h_value=h_display_value,
            delta=delta,
            evidence=evidence,
            review_hint="同一报表行在同一列位取值不同，建议核对原始底稿与披露版本",
        ),
    )


def run_table_row_twin_checks(
    doc_a: ReportDocument,
    doc_h: ReportDocument,
    *,
    max_diffs: int = _MAX_DIFFS_PER_JOB,
) -> tuple[list[Diff], TwinCheckStats]:
    """对两份文档的全部表格做行级孪生比对。

    返回 (diffs, stats)；stats 供 orchestrator 记日志与熔断预警。
    """
    stats = TwinCheckStats(
        tables_a=len(doc_a.tables or []),
        tables_h=len(doc_h.tables or []),
    )
    a_tables = [_build_table_rows(t) for t in (doc_a.tables or [])]
    h_tables = [_build_table_rows(t) for t in (doc_h.tables or [])]
    a_tables = [t for t in a_tables if t.rows]
    h_tables = [t for t in h_tables if t.rows]
    if not a_tables or not h_tables:
        return [], stats

    # 倒排索引：标签 -> 含该标签的 H 表，避免全表两两比对
    h_label_index: dict[str, list[int]] = {}
    for idx, ht in enumerate(h_tables):
        for label in ht.labels:
            h_label_index.setdefault(label, []).append(idx)

    diffs: list[Diff] = []
    used_h: set[int] = set()

    for at in a_tables:
        if len(diffs) >= max_diffs:
            stats.diffs_capped += 1
            break
        # 候选 H 表：共享标签数降序
        shared: dict[int, int] = {}
        for label in at.labels:
            for idx in h_label_index.get(label, ()):  # noqa: B905
                if idx not in used_h:
                    shared[idx] = shared.get(idx, 0) + 1
        candidates = sorted(shared.items(), key=lambda kv: kv[1], reverse=True)
        candidates = [(i, n) for i, n in candidates if n >= _MIN_MATCHED_KEYS][:_MAX_CANDIDATE_TABLES]

        for h_idx, _n_shared in candidates:
            ht = h_tables[h_idx]
            stats.candidate_pairs += 1
            if _pair_score_label_overlap(at, ht) < _MIN_LABEL_JACCARD:
                continue
            matched_keys = sorted(set(at.rows) & set(ht.rows))
            if len(matched_keys) < _MIN_MATCHED_KEYS:
                continue
            pairs = [(at.rows[k].value, ht.rows[k].value) for k in matched_keys]
            factor, hits = _best_factor(pairs)
            permuted = False
            if factor is None or hits / len(matched_keys) < _MIN_ANCHOR_RATIO:
                if not _is_column_permutation(pairs):
                    stats.skipped_low_anchor += 1
                    continue
                # 整列被打乱：低锚点不是配对失败，而正是篡改本身的形态
                permuted = True
                factor = 1.0

            # 配对成功：残差即差异
            table_diffs: list[Diff] = []
            for key in matched_keys:
                a_row, h_row = at.rows[key], ht.rows[key]
                if _rel_delta(a_row.value, h_row.value * factor) <= _DIFF_MIN_REL:
                    continue
                # 残差必须不是单位展示问题之外的小数噪声：相对差异超过容差即报
                table_diffs.append(_make_diff(a_row, h_row, factor, permuted=permuted))
            if len(table_diffs) > _MAX_DIFFS_PER_TABLE and not permuted:
                # 超量 = 配对错误（真实篡改是稀疏的），整表放弃
                stats.dropped_overflow_tables += 1
                continue
            if permuted and table_diffs:
                stats.permuted_tables += 1
                logger.info(
                    "表格行孪生比对：第{}页表格取值整列错乱（{}/{} 行不同，两侧数值集合相同），"
                    "按真实差异出报",
                    at.table.page, len(table_diffs), len(matched_keys),
                )

            stats.paired_tables += 1
            used_h.add(h_idx)
            for d in table_diffs:
                if len(diffs) >= max_diffs:
                    stats.diffs_capped += 1
                    break
                diffs.append(d)
            break  # 每张 A 表只配一张 H 表

    stats.diffs_emitted = len(diffs)
    logger.info(
        "表格行孪生比对: A表={} H表={} 候选对={} 配对={} 低锚点跳过={} 超量放弃={} "
        "整列错乱={} 差异={} 熔断丢弃={}",
        stats.tables_a, stats.tables_h, stats.candidate_pairs, stats.paired_tables,
        stats.skipped_low_anchor, stats.dropped_overflow_tables, stats.permuted_tables,
        stats.diffs_emitted, stats.diffs_capped,
    )
    return diffs, stats
