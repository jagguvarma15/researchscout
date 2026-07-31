"""Per-request ask/chat metrics for the dashboards.

One row per answered question: mode, timings, and the found verdict. Recording happens
after the response has finished streaming, in a session of its own, and is always
best-effort - a metrics failure must never surface to the user. The question text is
stored truncated; this is a local-only single-user app, and "which questions found
nothing" is the whole point of the not-found panel.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from researchscout.store.models import AskMetricRow

_QUESTION_CAP = 200


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
