from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.events import (
    EventInput,
    append_events,
    dismissed_event_paper_ids,
    positive_event_vectors,
)
from researchscout.store.models import EventRow
from researchscout.store.papers import upsert_paper

pytestmark = pytest.mark.integration

# The embeddings column is a fixed vector(384); tests write one-hots of that width.
_DIM = 384


def _onehot(i: int) -> list[float]:
    vector = [0.0] * _DIM
    vector[i] = 1.0
    return vector


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


def test_positive_event_vectors_latest_per_paper_excluding_saved(session: Session) -> None:
    from researchscout.store.saved import save_paper
    from researchscout.store.vectors import upsert_embedding

    for arxiv in ("2401.00001", "2401.00002", "2401.00003"):
        upsert_paper(session, _paper(arxiv))
        upsert_embedding(session, f"arxiv:{arxiv}", "stub", _onehot(1))
    save_paper(session, "local", "arxiv:2401.00003")
    append_events(
        session,
        "local",
        [
            EventInput(event="click", paper_id="arxiv:2401.00001"),
            EventInput(event="dwell", paper_id="arxiv:2401.00001", value=9000.0),
            EventInput(event="open_pdf", paper_id="arxiv:2401.00002"),
            # An impression is the denominator, never a preference.
            EventInput(event="impression", paper_id="arxiv:2401.00002", rank=0),
            # The save outranks the click behind it: the saved paper never appears here.
            EventInput(event="click", paper_id="arxiv:2401.00003"),
        ],
    )

    rows = positive_event_vectors(session, "local", "stub")
    assert sorted(paper_id for paper_id, _, _, _ in rows) == [
        "arxiv:2401.00001",
        "arxiv:2401.00002",
    ]
    # One row per paper, with the title and the embedding joined on.
    by_id = {paper_id: (title, vector) for paper_id, title, _, vector in rows}
    assert by_id["arxiv:2401.00001"] == ("T", _onehot(1))


def test_positive_event_vectors_window_and_other_users(session: Session) -> None:
    from researchscout.store.vectors import upsert_embedding

    upsert_paper(session, _paper("2401.00001"))
    upsert_embedding(session, "arxiv:2401.00001", "stub", _onehot(1))
    session.add(
        EventRow(
            user_sub="local",
            event="click",
            paper_id="arxiv:2401.00001",
            occurred_at=datetime.now(UTC) - timedelta(days=90),
        )
    )
    session.flush()
    # Too old for the window, and invisible to another reader either way.
    assert positive_event_vectors(session, "local", "stub", days=30) == []
    assert positive_event_vectors(session, "local", "stub", days=365) != []


def test_dismissed_event_paper_ids_distinct(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    append_events(
        session,
        "local",
        [
            EventInput(event="dismiss", paper_id="arxiv:2401.00001"),
            EventInput(event="dismiss", paper_id="arxiv:2401.00001"),
            EventInput(event="click", paper_id="arxiv:2401.00001"),
        ],
    )
    assert dismissed_event_paper_ids(session, "local") == ["arxiv:2401.00001"]
    assert dismissed_event_paper_ids(session, "someone-else") == []
