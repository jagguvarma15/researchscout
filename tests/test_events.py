from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.events import EventInput, append_events
from researchscout.store.models import EventRow
from researchscout.store.papers import upsert_paper

pytestmark = pytest.mark.integration


def _paper(arxiv: str) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title="T",
        abstract="a",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def test_append_events_stores_known_and_drops_unknown(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    session.flush()
    stored = append_events(
        session,
        "local",
        [
            EventInput(event="impression", paper_id="arxiv:2401.00001", rank=0, surface="feed"),
            EventInput(event="dwell", paper_id="arxiv:2401.00001", value=25000.0),
            EventInput(event="click", paper_id="arxiv:9999.00000"),  # unknown id: dropped
        ],
    )
    assert stored == 2
    rows = session.execute(select(EventRow).order_by(EventRow.id)).scalars().all()
    assert [(row.event, row.paper_id, row.rank, row.value) for row in rows] == [
        ("impression", "arxiv:2401.00001", 0, None),
        ("dwell", "arxiv:2401.00001", None, 25000.0),
    ]
    assert rows[0].user_sub == "local"
    assert rows[0].occurred_at is not None


def test_append_events_empty_batch_is_zero(session: Session) -> None:
    assert append_events(session, "local", []) == 0
