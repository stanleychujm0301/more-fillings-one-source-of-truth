from __future__ import annotations

from typing import Any, Iterable

from ahcc.schemas import DiffExplanation, DiffExplanationItem, Evidence, ReportSide


def format_explanation_value(value: Any) -> str:
    if value is None:
        return "未记录"
    if isinstance(value, float):
        if value.is_integer():
            return f"{value:,.0f}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def side_evidence(evidence: Iterable[Evidence], side: ReportSide) -> Evidence | None:
    return next((ev for ev in evidence if ev.side == side), None)


def format_location(evidence: Iterable[Evidence]) -> str:
    parts: list[str] = []
    for side, label in ((ReportSide.A_SHARE, "A"), (ReportSide.H_SHARE, "H")):
        pages = sorted({ev.page for ev in evidence if ev.side == side and ev.page})
        if pages:
            parts.append(f"{label} 第{'/'.join(str(page) for page in pages)}页")
    return "；".join(parts)


def explanation_item(
    *,
    label: str,
    role: str | None,
    a_value: Any,
    h_value: Any,
    delta: Any = None,
    a_evidence: Evidence | None = None,
    h_evidence: Evidence | None = None,
    a_snippet: str | None = None,
    h_snippet: str | None = None,
) -> DiffExplanationItem:
    return DiffExplanationItem(
        label=label,
        role=role,
        a_value=a_value,
        h_value=h_value,
        delta=delta,
        a_page=a_evidence.page if a_evidence else None,
        h_page=h_evidence.page if h_evidence else None,
        a_snippet=a_snippet or (a_evidence.snippet if a_evidence else None),
        h_snippet=h_snippet or (h_evidence.snippet if h_evidence else None),
    )


def make_value_explanation(
    *,
    headline: str,
    label: str,
    role: str | None,
    a_value: Any,
    h_value: Any,
    evidence: list[Evidence],
    delta: Any = None,
    review_hint: str | None = None,
) -> DiffExplanation:
    a_ev = side_evidence(evidence, ReportSide.A_SHARE)
    h_ev = side_evidence(evidence, ReportSide.H_SHARE)
    issue = (
        f"A 披露{label} {format_explanation_value(a_value)}；"
        f"H 披露{label} {format_explanation_value(h_value)}"
    )
    if delta is not None:
        issue += f"；差异 {format_explanation_value(delta)}"
    return DiffExplanation(
        headline=headline,
        issue=issue,
        location=format_location(evidence),
        items=[
            explanation_item(
                label=label,
                role=role,
                a_value=a_value,
                h_value=h_value,
                delta=delta,
                a_evidence=a_ev,
                h_evidence=h_ev,
            )
        ],
        review_hint=review_hint,
    )

