"""The citation walker: budgets, stamping, graceful throttling, the keyless fallback."""

from contextlib import nullcontext
from datetime import UTC, datetime

import httpx
import pytest

import researchscout.ingest.citations as walker
from researchscout.config import Settings
from researchscout.schema import Signal, SignalType


def _signal(paper_id: str, value: float) -> Signal:
    return Signal(
        paper_id=paper_id,
        type=SignalType.citation,
        source="semantic_scholar",
        value=value,
        metadata={},
        observed_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


class _S2:
    name = "semantic_scholar"

    def __init__(self, known: set[str], throttle_after: int | None = None) -> None:
        self._known = known
        self._throttle_after = throttle_after
        self.calls = 0

    def citations_for(self, pairs: list[tuple[str, str]]) -> list[Signal]:
        self.calls += 1
        if self._throttle_after is not None and self.calls > self._throttle_after:
            raise httpx.HTTPError("429")
        return [_signal(pid, 5.0) for pid, _ in pairs if pid in self._known]


class _Store:
    """Stand-in for the watermark store: never-stamped papers walk first."""

    def __init__(self, papers: list[str]) -> None:
        self.papers = papers
        self.stamped: dict[str, str] = {}
        self.written: list[str] = []

    def stalest(self, session: object, *, limit: int) -> list[tuple[str, str]]:
        unstamped = [p for p in self.papers if p not in self.stamped]
        return [(p, p.split(":")[1]) for p in unstamped[:limit]]

    def fallback(self, session: object, *, older_than: object, limit: int) -> list[tuple[str, str]]:
        return self.stalest(session, limit=limit)

    def mark(self, session: object, paper_ids: object, *, source: str, fetched_at: object) -> None:
        for pid in paper_ids:
            self.stamped[pid] = source


def _wire(monkeypatch: pytest.MonkeyPatch, store: _Store, sources: list[object]) -> None:
    monkeypatch.setattr(walker, "stalest_citation_targets", store.stalest)
    monkeypatch.setattr(walker, "stale_fallback_targets", store.fallback)
    monkeypatch.setattr(walker, "mark_citations_refreshed", store.mark)
    monkeypatch.setattr(walker, "session_scope", lambda: nullcontext(None))
    monkeypatch.setattr(walker, "append_signal_idempotent", lambda session, sig: True)
    monkeypatch.setattr(
        walker, "set_citation_count", lambda session, pid, value: store.written.append(pid)
    )
    monkeypatch.setattr(walker, "enabled_sources", lambda kind: sources)
    monkeypatch.setattr(walker.time, "sleep", lambda seconds: None)


def test_the_primary_pass_stamps_known_and_unknown_alike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Semantic Scholar does not know it yet" is an observation: stamping sends the paper
    to the back of the queue instead of starving the tail on it every day."""
    store = _Store(["arxiv:1", "arxiv:2", "arxiv:3"])
    _wire(monkeypatch, store, [_S2(known={"arxiv:1", "arxiv:3"})])

    note = walker.run_citation_refresh(Settings(citations_daily_papers=10))
    assert sorted(store.stamped) == ["arxiv:1", "arxiv:2", "arxiv:3"]
    assert store.written == ["arxiv:1", "arxiv:3"]  # counts only for papers S2 returned
    assert "s2: 3 paper(s)" in note
    assert "openalex: disabled" in note


def test_the_budget_bounds_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store([f"arxiv:{n}" for n in range(6)])
    _wire(monkeypatch, store, [_S2(known=set())])

    note = walker.run_citation_refresh(Settings(citations_daily_papers=4))
    assert len(store.stamped) == 4
    assert "s2: 4 paper(s)" in note


def test_a_throttled_pass_keeps_its_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store([f"arxiv:{n}" for n in range(1200)])
    _wire(monkeypatch, store, [_S2(known=set(), throttle_after=1)])

    note = walker.run_citation_refresh(Settings(citations_daily_papers=5000))
    assert len(store.stamped) == 500  # the first batch survived the stop
    assert "stopped early" in note


class _KeylessOpenAlex:
    name = "openalex"
    has_key = False

    def citations_for(self, pairs: list[tuple[str, str]]) -> list[Signal]:
        raise AssertionError("a keyless connector must not be called")


def test_the_fallback_skips_itself_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(["arxiv:1"])
    _wire(monkeypatch, store, [_S2(known=set()), _KeylessOpenAlex()])

    note = walker.run_citation_refresh(Settings())
    assert "openalex: skipped (no key)" in note


class _OpenAlex:
    name = "openalex"
    has_key = True

    def citations_for(self, pairs: list[tuple[str, str]]) -> list[Signal]:
        return [
            Signal(
                paper_id=pid,
                type=SignalType.citation,
                source="openalex",
                value=2.0,
                metadata={},
                observed_at=datetime(2026, 8, 17, tzinfo=UTC),
            )
            for pid, _ in pairs
        ]


def test_the_fallback_takes_what_the_primary_left(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(["arxiv:1", "arxiv:2", "arxiv:3"])
    # The primary throttles out immediately, leaving everything unstamped for the fallback.
    _wire(monkeypatch, store, [_S2(known=set(), throttle_after=0), _OpenAlex()])

    note = walker.run_citation_refresh(Settings(citations_fallback_papers=10))
    assert set(store.stamped.values()) == {"openalex"}
    assert "openalex: 3 paper(s)" in note
