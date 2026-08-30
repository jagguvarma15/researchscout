"""Per-request ask/chat metrics for the dashboards.

One row per ask - answered, not-found, refused, quota-dead, or busy-rejected - with mode,
timings, token cost, and the outcome. Recording happens after the response has finished
streaming, in a session of its own, and is always best-effort - a metrics failure must
never surface to the user. The question text is stored truncated (refusals included: they
are the most useful rows for tuning the classifier, and questions are already stored for
every normal ask); retention is bounded by :func:`prune_ask_metrics`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from researchscout.store.models import AskMetricRow

_QUESTION_CAP = 200
# How much history stays; questions are personal-ish text, not a permanent record.
_KEEP_DAYS = 90


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
    # Outcome counts over the window (v2 rows; legacy rows are all ok/notfound).
    refused: int = 0
    llm_errors: int = 0
    busy: int = 0
    # Share of completed llm answers whose post-check dropped an invented citation.
    hallucination_rate: float | None = None


def ask_summary(session: Session, *, days: int = 7) -> AskSummary:
    """Counts, rates, and per-mode latency percentiles over the window."""
    since = datetime.now(UTC) - timedelta(days=days)
    asked, found = session.execute(
        select(
            func.count(),
            func.avg(case((AskMetricRow.found, 1.0), else_=0.0)),
        ).where(AskMetricRow.asked_at >= since)
    ).one()
    outcome_rows = session.execute(
        select(AskMetricRow.outcome, func.count())
        .where(AskMetricRow.asked_at >= since, AskMetricRow.outcome.is_not(None))
        .group_by(AskMetricRow.outcome)
    ).all()
    outcomes = {outcome: int(count) for outcome, count in outcome_rows}
    hallucination = session.execute(
        select(func.avg(case((AskMetricRow.hallucinated > 0, 1.0), else_=0.0))).where(
            AskMetricRow.asked_at >= since,
            AskMetricRow.outcome == "ok",
            AskMetricRow.mode == "llm",
            AskMetricRow.hallucinated.is_not(None),
        )
    ).scalar_one_or_none()
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
        refused=outcomes.get("refused", 0),
        llm_errors=outcomes.get("llm_error", 0),
        busy=outcomes.get("busy", 0),
        hallucination_rate=float(hallucination) if hallucination is not None else None,
    )


def recent_notfound(session: Session, *, limit: int = 10) -> list[str]:
    """The latest distinct questions that found nothing - the corpus-gap list.

    The outcome filter matters now that refusals and errors also land rows with
    ``found=false``: an off-topic question or a quota death is not a corpus gap. Legacy
    NULL-outcome rows stay visible (they were all real answers).
    """
    rows = session.execute(
        select(AskMetricRow.question, func.max(AskMetricRow.asked_at).label("last_at"))
        .where(
            AskMetricRow.found.is_(False),
            (AskMetricRow.outcome == "notfound") | AskMetricRow.outcome.is_(None),
        )
        .group_by(AskMetricRow.question)
        # The id tiebreak matters: now() is transaction-scoped in Postgres, so rows
        # written in one transaction share a timestamp and "latest" needs the sequence.
        .order_by(func.max(AskMetricRow.asked_at).desc(), func.max(AskMetricRow.id).desc())
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
    model: str | None = None,
    outcome: str | None = None,
    user_hash: str | None = None,
    agentic: bool = False,
    pinned: bool = False,
    rerank_used: bool | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    first_token_ms: int | None = None,
    hallucinated: int | None = None,
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
            model=model[:120] if model else None,
            outcome=outcome,
            user_hash=user_hash,
            agentic=agentic,
            pinned=pinned,
            rerank_used=rerank_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            first_token_ms=first_token_ms,
            hallucinated=hallucinated,
        )
    )
    session.flush()


def prune_ask_metrics(session: Session, *, keep_days: int = _KEEP_DAYS) -> None:
    """Drop metrics rows older than the retention window."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    session.execute(delete(AskMetricRow).where(AskMetricRow.asked_at < cutoff))
