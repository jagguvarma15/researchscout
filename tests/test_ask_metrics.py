from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.store.ask_metrics import record_ask
from researchscout.store.models import AskMetricRow

pytestmark = pytest.mark.integration


def test_record_ask_lands_a_row_and_truncates_the_question(session: Session) -> None:
    record_ask(
        session,
        mode="fast",
        surface="chat",
        question="q" * 500,
        retrieved=3,
        best_relevance=0.42,
        found=True,
        retrieve_ms=120,
        rerank_ms=800,
        llm_ms=None,
        total_ms=950,
    )

    row = session.execute(select(AskMetricRow)).scalar_one()
    assert len(row.question) == 200
    assert row.mode == "fast" and row.surface == "chat"
    assert row.found is True and row.best_relevance == 0.42
    assert row.llm_ms is None and row.total_ms == 950
    assert row.asked_at >= datetime.now(UTC) - timedelta(minutes=1)


def _ask(
    session: Session,
    *,
    mode: str = "fast",
    question: str = "q",
    found: bool = True,
    total_ms: int = 100,
    outcome: str | None = None,
    hallucinated: int | None = None,
) -> None:
    record_ask(
        session,
        mode=mode,
        surface="chat",
        question=question,
        retrieved=1 if found else 0,
        best_relevance=0.9 if found else 0.1,
        found=found,
        retrieve_ms=50,
        rerank_ms=None,
        llm_ms=None,
        total_ms=total_ms,
        outcome=outcome if outcome is not None else ("ok" if found else "notfound"),
        hallucinated=hallucinated,
    )


def test_ask_summary_counts_rates_and_percentiles(session: Session) -> None:
    from researchscout.store.ask_metrics import ask_summary

    for total in (100, 200, 300, 400):
        _ask(session, total_ms=total)
    _ask(session, mode="llm", question="slow one", found=False, total_ms=2000)

    summary = ask_summary(session, days=7)
    assert summary.asked == 5
    assert summary.found_rate == pytest.approx(0.8)
    assert summary.fast_p50_ms == 250
    assert summary.fast_p95_ms == 385
    assert summary.llm_p50_ms == 2000
    assert summary.llm_p95_ms == 2000


def test_ask_summary_empty_window(session: Session) -> None:
    from researchscout.store.ask_metrics import ask_summary

    summary = ask_summary(session, days=7)
    assert summary.asked == 0
    assert summary.found_rate is None
    assert summary.fast_p50_ms is None and summary.llm_p95_ms is None


def test_recent_notfound_lists_distinct_latest_first(session: Session) -> None:
    from researchscout.store.ask_metrics import recent_notfound

    _ask(session, question="found fine")
    _ask(session, question="missed once", found=False)
    _ask(session, question="missed twice", found=False)
    _ask(session, question="missed twice", found=False)

    questions = recent_notfound(session, limit=10)
    assert questions[0] == "missed twice"
    assert sorted(questions) == ["missed once", "missed twice"]


def test_recent_notfound_ignores_refusals_and_errors(session: Session) -> None:
    """An off-topic question or a quota death is not a corpus gap."""
    from researchscout.store.ask_metrics import recent_notfound

    _ask(session, question="real gap", found=False)
    _ask(session, question="lasagna recipe", found=False, outcome="refused")
    _ask(session, question="quota death", found=False, outcome="llm_error")
    _ask(session, question="queue full", found=False, outcome="busy")
    # A legacy row (pre-v2, outcome NULL) was always a real answer - it stays visible.
    record_ask(
        session,
        mode="fast",
        surface="chat",
        question="legacy gap",
        retrieved=0,
        best_relevance=None,
        found=False,
        retrieve_ms=None,
        rerank_ms=None,
        llm_ms=None,
        total_ms=10,
    )

    questions = recent_notfound(session, limit=10)
    assert sorted(questions) == ["legacy gap", "real gap"]


def test_ask_summary_counts_outcomes_and_hallucinations(session: Session) -> None:
    from researchscout.store.ask_metrics import ask_summary

    _ask(session, mode="llm", outcome="ok", hallucinated=0)
    _ask(session, mode="llm", outcome="ok", hallucinated=2)
    _ask(session, mode="llm", found=False, outcome="refused")
    _ask(session, mode="llm", found=False, outcome="llm_error")
    _ask(session, mode="llm", found=False, outcome="busy")
    _ask(session, mode="llm", found=False, outcome="busy")

    summary = ask_summary(session, days=7)
    assert summary.refused == 1
    assert summary.llm_errors == 1
    assert summary.busy == 2
    assert summary.hallucination_rate == pytest.approx(0.5)


def test_record_ask_carries_the_v2_fields(session: Session) -> None:
    from sqlalchemy import select

    from researchscout.store.models import AskMetricRow

    record_ask(
        session,
        mode="llm",
        surface="chat",
        question="q",
        retrieved=3,
        best_relevance=None,
        found=True,
        retrieve_ms=100,
        rerank_ms=None,
        llm_ms=900,
        total_ms=1100,
        model="m" * 200,
        outcome="ok",
        user_hash="abc123def456",
        agentic=True,
        pinned=True,
        rerank_used=True,
        prompt_tokens=321,
        completion_tokens=45,
        first_token_ms=250,
        hallucinated=1,
    )
    row = session.execute(select(AskMetricRow)).scalar_one()
    assert row.model is not None and len(row.model) == 120
    assert row.outcome == "ok" and row.user_hash == "abc123def456"
    assert row.agentic is True and row.pinned is True and row.rerank_used is True
    assert row.prompt_tokens == 321 and row.completion_tokens == 45
    assert row.first_token_ms == 250 and row.hallucinated == 1


def test_prune_ask_metrics_drops_only_old_rows(session: Session) -> None:
    from sqlalchemy import select, update

    from researchscout.store.ask_metrics import prune_ask_metrics
    from researchscout.store.models import AskMetricRow

    _ask(session, question="old")
    _ask(session, question="new")
    session.flush()
    session.execute(
        update(AskMetricRow)
        .where(AskMetricRow.question == "old")
        .values(asked_at=datetime.now(UTC) - timedelta(days=120))
    )

    prune_ask_metrics(session, keep_days=90)
    kept = session.execute(select(AskMetricRow.question)).scalars().all()
    assert kept == ["new"]
