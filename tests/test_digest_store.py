from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.digest import Digest, RankedPaper
from researchscout.schema import Author, Paper
from researchscout.store.digests import get_digest, list_digests, upsert_digest

pytestmark = pytest.mark.integration

END = datetime(2026, 7, 6, tzinfo=UTC)


def _digest(slug: str = "2026-w28", body: str = "Big week.") -> Digest:
    paper = Paper(
        id="arxiv:2401.00001",
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=END - timedelta(days=2),
        source="arxiv",
    )
    return Digest(
        slug=slug,
        title="Research radar, week 28 2026",
        period_start=END - timedelta(days=7),
        period_end=END,
        body=body,
        cited=["arxiv:2401.00001"],
        items=[RankedPaper(paper=paper, score=0.9, citations=12.0)],
    )


def test_upsert_get_roundtrip(session: Session) -> None:
    upsert_digest(session, _digest())
    row = get_digest(session, "2026-w28")
    assert row is not None
    assert row.body == "Big week."
    assert row.items[0]["paper_id"] == "arxiv:2401.00001"
    assert row.items[0]["citations"] == 12.0


def test_rerun_replaces_the_week(session: Session) -> None:
    upsert_digest(session, _digest(body="First take."))
    upsert_digest(session, _digest(body="Second take."))
    row = get_digest(session, "2026-w28")
    assert row is not None
    assert row.body == "Second take."
    assert len(list_digests(session)) == 1


def test_list_is_newest_first(session: Session) -> None:
    older = _digest(slug="2026-w27")
    older.period_end = END - timedelta(days=7)
    upsert_digest(session, older)
    upsert_digest(session, _digest(slug="2026-w28"))
    assert [row.slug for row in list_digests(session)] == ["2026-w28", "2026-w27"]
