from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.ingest.pipeline import run_ingest, window_start
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


def test_resume_continues_from_cursor(session: Session) -> None:
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 6)]
    run_ingest(session, FakeSource(papers, page_size=2), SINCE, max_items=2)
    cursor, _ = get_state(session, "fake")
    assert cursor is not None  # interrupted mid-pagination

    summary = run_ingest(session, FakeSource(papers, page_size=2), SINCE, resume=True)
    assert summary.new_papers == 3  # the remainder, no duplicates
    assert _count(session) == 5
    cursor, _ = get_state(session, "fake")
    assert cursor is None  # paginated to exhaustion


def test_resume_ignores_stale_window(session: Session) -> None:
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 4)]
    run_ingest(session, FakeSource(papers, page_size=2), SINCE, max_items=2)

    other_since = datetime(2024, 2, 1, tzinfo=UTC)
    summary = run_ingest(session, FakeSource(papers, page_size=2), other_since, resume=True)
    assert summary.collapsed == 2  # started from offset 0, re-saw the first page
    assert summary.new_papers == 1
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


class FailingSource(FakeSource):
    """Serves its first page, then rate-limits like an upstream shedding load."""

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        if cursor:
            raise httpx.ConnectError("boom")
        return super().fetch(since, cursor)


def test_upstream_failure_keeps_earlier_pages(session: Session) -> None:
    """A 429 twenty pages in must not roll back page 0 - that is where the newest papers are."""
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 5)]
    summary = run_ingest(session, FailingSource(papers, page_size=2), SINCE)

    assert summary.fetched == 2
    assert summary.new_papers == 2
    assert summary.stopped_early is not None
    assert _count(session) == 2
    cursor, _ = get_state(session, "fake")
    assert cursor == "2"  # still pointing at the failed page for a same-window resume


def test_each_page_commits_as_it_lands(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    commits: list[int] = []
    original = session.commit

    def counting_commit() -> None:
        commits.append(1)
        original()

    monkeypatch.setattr(session, "commit", counting_commit)
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 7)]
    run_ingest(session, FakeSource(papers, page_size=2), SINCE)
    assert len(commits) == 3  # one per page, so a late failure costs only its own page


def test_early_stop_after_known_pages(session: Session) -> None:
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 11)]
    run_ingest(session, FakeSource(papers, page_size=5), SINCE)

    summary = run_ingest(session, FakeSource(papers, page_size=2), SINCE, stop_after_known_pages=2)
    assert summary.fetched == 4  # two nothing-new pages, then the walk ends
    assert summary.new_papers == 0
    assert summary.stopped_early == "nothing new on 2 consecutive page(s)"
    cursor, _ = get_state(session, "fake")
    assert cursor is None  # the window is marked done, not resumable into the skipped tail


def test_early_stop_resets_when_a_page_has_news(session: Session) -> None:
    papers = [_paper(f"2401.{i:05d}") for i in range(1, 11)]
    for known in papers[:2] + papers[4:]:
        run_ingest(session, FakeSource([known]), SINCE)

    summary = run_ingest(session, FakeSource(papers, page_size=2), SINCE, stop_after_known_pages=2)
    # Pages land known, new, known, known - the new page resets the counter, so the stop
    # comes after the fourth page and the fifth is never fetched.
    assert summary.fetched == 8
    assert summary.new_papers == 2
    assert summary.stopped_early is not None


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


class PoisonSource(FakeSource):
    """Every second payload is unparseable — the malformed-entry case."""

    def normalize(self, raw: RawItem) -> Paper:
        if raw.payload.get("title") == "POISON":
            raise ValueError("malformed entry")
        return Paper.model_validate(raw.payload)


def test_a_malformed_entry_is_skipped_not_fatal(session: Session) -> None:
    """With a deterministic window a poison entry would otherwise stall the same run daily."""
    papers = [_paper("2401.00001"), _paper("2401.00002", title="POISON"), _paper("2401.00003")]
    summary = run_ingest(session, PoisonSource(papers), SINCE)
    assert summary.skipped == 1
    assert summary.new_papers == 2
    assert _count(session) == 2


def test_replayed_signals_write_once(session: Session) -> None:
    """The unique observation index makes replays converge instead of raising."""
    run_ingest(session, FakeSource([_paper("2401.00001")]), SINCE)
    signal = Signal(
        paper_id="arxiv:2401.00001",
        type=SignalType.citation,
        source="fake-signal",
        value=4.0,
        metadata={},
        observed_at=SINCE,
    )
    first = run_ingest(session, FakeSignalSource([signal]), SINCE)
    replay = run_ingest(session, FakeSignalSource([signal]), SINCE)
    assert first.signals == 1
    assert replay.signals == 1  # counted as processed, but the row exists exactly once
    from researchscout.store.signals import series

    assert len(series(session, "arxiv:2401.00001", "citation", SINCE)) == 1


def _patched_state(
    monkeypatch: pytest.MonkeyPatch,
    cursor: str | None,
    last_since: datetime | None,
    updated_at: datetime | None,
) -> None:
    monkeypatch.setattr(
        "researchscout.ingest.pipeline.read_state",
        lambda session, source: (cursor, last_since, updated_at),
    )


NOW = datetime(2026, 8, 18, 0, 30, 17, 123456, tzinfo=UTC)


def test_window_start_without_state_is_the_plain_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched_state(monkeypatch, None, None, None)
    start = window_start(None, "arxiv", NOW, overlap_days=4, max_window_days=30)
    assert start == datetime(2026, 8, 14, 0, 0, tzinfo=UTC)  # hour-truncated, minus overlap


def test_window_start_resumes_an_interrupted_walk_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The saved cursor indexes one specific query; only the identical since can adopt it."""
    saved = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
    _patched_state(monkeypatch, "1200", saved, datetime(2026, 8, 17, 9, 0, tzinfo=UTC))
    start = window_start(None, "arxiv", NOW, overlap_days=4, max_window_days=30)
    assert start == saved


def test_window_start_widens_over_downtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """The watermark froze when the host went down; the window reaches back past it."""
    week_ago = datetime(2026, 8, 11, 7, 45, tzinfo=UTC)
    _patched_state(monkeypatch, None, datetime(2026, 8, 7, 0, 0, tzinfo=UTC), week_ago)
    start = window_start(None, "arxiv", NOW, overlap_days=4, max_window_days=30)
    assert start == datetime(2026, 8, 7, 7, 0, tzinfo=UTC)  # trunc(watermark) - overlap


def test_window_start_caps_at_the_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything longer gone is a deliberate backfill, not a catch-up."""
    long_ago = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    _patched_state(monkeypatch, None, long_ago, long_ago)
    start = window_start(None, "arxiv", NOW, overlap_days=4, max_window_days=30)
    assert start == NOW - timedelta(days=30)


def test_window_start_treats_a_stale_cursor_as_a_completed_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    _patched_state(monkeypatch, "500", stale, datetime(2026, 8, 17, 9, 0, tzinfo=UTC))
    start = window_start(None, "arxiv", NOW, overlap_days=4, max_window_days=30)
    assert start == datetime(2026, 8, 13, 9, 0, tzinfo=UTC)  # falls through to the watermark


def test_window_start_is_stable_within_the_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-run inside the same slot must produce the same since, or resume can never work."""
    _patched_state(monkeypatch, None, None, None)
    first = window_start(None, "arxiv", NOW, overlap_days=4, max_window_days=30)
    later = window_start(
        None, "arxiv", NOW + timedelta(minutes=20), overlap_days=4, max_window_days=30
    )
    assert first == later
