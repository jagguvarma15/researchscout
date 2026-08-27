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
