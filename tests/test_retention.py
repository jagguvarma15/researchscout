"""The rolling corpus window: cutoff math (unit) and the purge itself (integration)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.schema import Author, Paper, Signal, SignalType
from researchscout.store.models import (
    DigestRow,
    PaperEmbeddingRow,
    PaperRow,
    SignalRow,
    UserHighlightRow,
)
from researchscout.store.papers import upsert_paper
from researchscout.store.retention import (
    count_expired,
    effective_window_days,
    purge_expired,
    retention_cutoff,
    within_retention,
)
from researchscout.store.saved import save_paper
from researchscout.store.signals import append_signal

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def test_retention_is_off_by_default() -> None:
    settings = Settings(retention_days=0)
    assert retention_cutoff(settings, NOW) is None
    assert within_retention(datetime(2017, 1, 1, tzinfo=UTC), settings, NOW)
    assert effective_window_days(30, settings) == 30


def test_the_cutoff_is_the_window_back_from_now() -> None:
    settings = Settings(retention_days=10)
    assert retention_cutoff(settings, NOW) == NOW - timedelta(days=10)
    assert within_retention(NOW - timedelta(days=9, hours=23), settings, NOW)
    assert not within_retention(NOW - timedelta(days=10, seconds=1), settings, NOW)


def test_look_backs_clamp_to_the_window() -> None:
    settings = Settings(retention_days=10)
    assert effective_window_days(30, settings) == 10
    assert effective_window_days(7, settings) == 7


def _paper(arxiv: str, published_at: datetime) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=f"Paper {arxiv}",
        abstract="An abstract.",
        authors=[Author(name="Jane Doe")],
        categories=["cs.LG"],
        published_at=published_at,
        source="arxiv",
    )


def _count(session: Session, model: type) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _seed(session: Session, now: datetime) -> None:
    old = now - timedelta(days=40)
    for arxiv, published in [
        ("2609.00001", old),  # expired
        ("2609.00002", old),  # expired, saved -> kept
        ("2609.00003", old),  # expired, highlighted -> kept
        ("2610.00004", now - timedelta(days=2)),  # inside the window
    ]:
        upsert_paper(session, _paper(arxiv, published))
        session.add(
            PaperEmbeddingRow(paper_id=f"arxiv:{arxiv}", model_id="m", embedding=[0.0] * 384)
        )
        append_signal(
            session,
            Signal(
                paper_id=f"arxiv:{arxiv}",
                type=SignalType.citation,
                value=1.0,
                source="test",
                observed_at=now,
            ),
        )
    save_paper(session, "local", "arxiv:2609.00002")
    session.add(
        UserHighlightRow(
            user_sub="local",
            paper_id="arxiv:2609.00003",
            highlight_id="h1",
            page=1,
            color="yellow",
            text="kept",
            rects=[],
        )
    )
    for slug, end in [("2026-w36", old), ("2026-w42", now - timedelta(days=1))]:
        session.add(
            DigestRow(
                slug=slug,
                title=slug,
                period_start=end - timedelta(days=7),
                period_end=end,
                body="",
                items=[],
            )
        )
    session.commit()


@pytest.mark.integration
def test_the_dry_run_counts_without_deleting(session: Session) -> None:
    now = datetime.now(UTC)
    _seed(session, now)
    summary = count_expired(session, now - timedelta(days=10))
    assert (summary.papers, summary.digests, summary.exempt) == (1, 1, 2)
    assert _count(session, PaperRow) == 4


@pytest.mark.integration
def test_the_purge_keeps_the_window_and_what_readers_keep(session: Session) -> None:
    now = datetime.now(UTC)
    _seed(session, now)

    summary = purge_expired(session, now - timedelta(days=10), batch=1)

    assert (summary.papers, summary.digests, summary.exempt) == (1, 1, 2)
    remaining = set(session.execute(select(PaperRow.id)).scalars())
    assert remaining == {"arxiv:2609.00002", "arxiv:2609.00003", "arxiv:2610.00004"}
    # Children of the removed paper went with it through the cascades.
    assert _count(session, PaperEmbeddingRow) == 3
    assert _count(session, SignalRow) == 3
    assert set(session.execute(select(DigestRow.slug)).scalars()) == {"2026-w42"}


@pytest.mark.integration
def test_a_second_purge_is_a_no_op(session: Session) -> None:
    now = datetime.now(UTC)
    _seed(session, now)
    purge_expired(session, now - timedelta(days=10))
    again = purge_expired(session, now - timedelta(days=10))
    assert (again.papers, again.digests) == (0, 0)
