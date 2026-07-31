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
