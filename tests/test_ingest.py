from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.ingest.pipeline import run_ingest
from researchscout.schema import Author, Paper, Signal, SignalType
from researchscout.sources.base import RawItem, Source
from researchscout.store.models import PaperRow
from researchscout.store.papers import get_paper
from researchscout.store.state import get_state

pytestmark = pytest.mark.integration

SINCE = datetime(2024, 1, 1, tzinfo=UTC)


def _paper(arxiv: str, title: str = "T") -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="An abstract.",
        authors=[Author(name="Jane Doe")],
        categories=["cs.LG"],
        published_at=SINCE,
        source="arxiv",
    )


class FakeSource(Source):
    name = "fake"
    kind = "content"

    def __init__(self, papers: list[Paper], page_size: int = 100) -> None:
        self._papers = papers
        self._page_size = page_size

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        start = int(cursor) if cursor else 0
        chunk = self._papers[start : start + self._page_size]
        items = [
            RawItem(source=self.name, fetched_at=SINCE, payload=p.model_dump(mode="json"))
            for p in chunk
        ]
        nxt = start + self._page_size
        return items, (str(nxt) if nxt < len(self._papers) else None)

    def normalize(self, raw: RawItem) -> Paper:
        return Paper.model_validate(raw.payload)


class FakeSignalSource(Source):
    name = "fake-signal"
    kind = "signal"

    def __init__(self, signals: list[Signal]) -> None:
        self._signals = signals

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        items = [
            RawItem(source=self.name, fetched_at=SINCE, payload=s.model_dump(mode="json"))
            for s in self._signals
        ]
        return items, None

    def normalize(self, raw: RawItem) -> Signal:
        return Signal.model_validate(raw.payload)


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(PaperRow)).scalar_one()


def test_ingest_paginates_and_stores(session: Session) -> None:
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 4)]
    summary = run_ingest(session, FakeSource(papers, page_size=1), SINCE)
    assert summary.fetched == 3
    assert summary.new_papers == 3
    assert summary.raw_stored == 3
    assert _count(session) == 3
    cursor, _last_since = get_state(session, "fake")
    assert cursor is None  # paginated to exhaustion


def test_reingest_writes_zero_new(session: Session) -> None:
    papers = [_paper("2401.00001"), _paper("2401.00002")]
    run_ingest(session, FakeSource(papers), SINCE)
    summary = run_ingest(session, FakeSource(papers), SINCE)
    assert summary.new_papers == 0
    assert summary.collapsed == 2
    assert _count(session) == 2


def test_cross_source_collapse(session: Session) -> None:
    run_ingest(session, FakeSource([_paper("2401.00001")]), SINCE)
    summary = run_ingest(session, FakeSource([_paper("2401.00001", title="Other")]), SINCE)
    assert summary.new_papers == 0
    assert summary.collapsed == 1
    assert _count(session) == 1


def test_max_items_caps_ingestion(session: Session) -> None:
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 6)]
    summary = run_ingest(session, FakeSource(papers, page_size=2), SINCE, max_items=3)
    assert summary.fetched == 3
    assert _count(session) == 3


def test_reingest_refreshes_same_paper(session: Session) -> None:
    run_ingest(session, FakeSource([_paper("2401.00001")]), SINCE)
    refreshed = _paper("2401.00001").model_copy(update={"venue": "NeurIPS 2024"})
    summary = run_ingest(session, FakeSource([refreshed]), SINCE)

    assert summary.new_papers == 0
    assert summary.collapsed == 1
    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.venue == "NeurIPS 2024"


def test_citation_signal_sets_count(session: Session) -> None:
    run_ingest(session, FakeSource([_paper("2401.00001")]), SINCE)
    signal = Signal(
        paper_id="arxiv:2401.00001",
        type=SignalType.citation,
        source="fake-signal",
        value=7.0,
        observed_at=SINCE,
    )
    run_ingest(session, FakeSignalSource([signal]), SINCE)

    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.citation_count == 7

    run_ingest(session, FakeSource([_paper("2401.00001", title="Updated")]), SINCE)
    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.title == "Updated"
    assert got.citation_count == 7  # content re-ingest never resets the materialized count
