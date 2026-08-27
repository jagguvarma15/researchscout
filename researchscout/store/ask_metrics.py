"""Per-request ask/chat metrics for the dashboards.

One row per answered question: mode, timings, and the found verdict. Recording happens
after the response has finished streaming, in a session of its own, and is always
best-effort - a metrics failure must never surface to the user. The question text is
stored truncated; this is a local-only single-user app, and "which questions found
nothing" is the whole point of the not-found panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from researchscout.store.models import AskMetricRow

_QUESTION_CAP = 200


@dataclass(frozen=True)
class AskSummary:
    """Ask/chat usage over a recent window, for the status payload."""

    days: int
    asked: int
    found_rate: float | None
    fast_p50_ms: int | None
    fast_p95_ms: int | None
    llm_p50_ms: int | None
    llm_p95_ms: int | None


def ask_summary(session: Session, *, days: int = 7) -> AskSummary:
    """Counts, found rate, and per-mode latency percentiles over the window."""
    since = datetime.now(UTC) - timedelta(days=days)
    asked, found = session.execute(
        select(
            func.count(),
            func.avg(case((AskMetricRow.found, 1.0), else_=0.0)),
        ).where(AskMetricRow.asked_at >= since)
    ).one()
    percentiles: dict[str, tuple[int, int]] = {}
    rows = session.execute(
        select(
            AskMetricRow.mode,
            func.percentile_cont(0.5).within_group(AskMetricRow.total_ms),
            func.percentile_cont(0.95).within_group(AskMetricRow.total_ms),
        )
        .where(AskMetricRow.asked_at >= since)
        .group_by(AskMetricRow.mode)
    ).all()
    for mode, p50, p95 in rows:
        if p50 is not None and p95 is not None:
            percentiles[mode] = (int(p50), int(p95))
    fast = percentiles.get("fast")
    llm = percentiles.get("llm")
    return AskSummary(
        days=days,
        asked=int(asked),
        found_rate=float(found) if found is not None else None,
        fast_p50_ms=fast[0] if fast else None,
        fast_p95_ms=fast[1] if fast else None,
        llm_p50_ms=llm[0] if llm else None,
        llm_p95_ms=llm[1] if llm else None,
    )


def recent_notfound(session: Session, *, limit: int = 10) -> list[str]:
    """The latest distinct questions that found nothing - the corpus-gap list."""
    rows = session.execute(
        select(AskMetricRow.question, func.max(AskMetricRow.asked_at).label("last_at"))
        .where(AskMetricRow.found.is_(False))
        .group_by(AskMetricRow.question)
        .order_by(func.max(AskMetricRow.asked_at).desc())
        .limit(limit)
    ).all()
    return [question for question, _ in rows]


def record_ask(
    session: Session,
    *,
    mode: str,
    surface: str,
    question: str,
    retrieved: int,
    best_relevance: float | None,
    found: bool,
    retrieve_ms: int | None,
    rerank_ms: int | None,
    llm_ms: int | None,
    total_ms: int,
) -> None:
    """Append one metrics row (the caller owns the session and its lifecycle)."""
    session.add(
        AskMetricRow(
            mode=mode,
            surface=surface,
            question=question[:_QUESTION_CAP],
            retrieved=retrieved,
            best_relevance=best_relevance,
            found=found,
            retrieve_ms=retrieve_ms,
            rerank_ms=rerank_ms,
            llm_ms=llm_ms,
            total_ms=total_ms,
        )
    )
    session.flush()
