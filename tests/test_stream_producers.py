from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

import researchscout.stream.producers as producers_mod
from researchscout.config import Settings
from researchscout.schema import Paper
from researchscout.sources.base import RawItem
from researchscout.stream.broker import InMemoryBroker, StreamTopics
from researchscout.stream.envelope import decode
from researchscout.stream.producers import (
    _should_publish,
    build_producer_tasks,
    poll_fulltext,
    publish_source,
)

TOPICS = StreamTopics.for_prefix("rs")
NOW = datetime(2026, 7, 30, 6, tzinfo=UTC)


class FakeSource:
    name = "fakearxiv"

    def __init__(self) -> None:
        self.pages = {
            None: ([self._raw("2607.00001"), self._raw("2607.00002")], "page-2"),
            "page-2": ([self._raw("2607.00003")], None),
        }

    def _raw(self, arxiv_id: str) -> RawItem:
        return RawItem(source=self.name, fetched_at=NOW, payload={"id": arxiv_id})

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        return self.pages[cursor]

    def normalize(self, raw: RawItem) -> Paper:
        arxiv_id = str(raw.payload["id"])
        return Paper(
            id=f"arxiv:{arxiv_id}",
            external_ids={"arxiv": arxiv_id},
            title="t",
            abstract="a",
            published_at=NOW,
            updated_at=raw.payload.get("updated"),
            source=self.name,
        )


class BrokenSource(FakeSource):
    def normalize(self, raw: RawItem) -> Paper:
        raise ValueError("malformed entry")


@contextmanager
def _no_session() -> Iterator[None]:
    yield None


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"raw": [], "state": [], "full_text": []}
    monkeypatch.setattr(producers_mod, "session_scope", _no_session)
    monkeypatch.setattr(
        producers_mod,
        "append_raw",
        lambda session, **kw: calls["raw"].append(kw) or 1,
    )
    monkeypatch.setattr(
        producers_mod,
        "save_state",
        lambda session, source, cursor, since: calls["state"].append((source, cursor)),
    )
    monkeypatch.setattr(
        producers_mod,
        "record_full_text_result",
        lambda session, paper_id, text, **kw: calls["full_text"].append((paper_id, text)),
    )
    monkeypatch.setattr(producers_mod, "_priority_ids", lambda session: set())
    return calls


def test_publish_source_pages_and_lands_raw(_stub_store: dict[str, list]) -> None:
    broker = InMemoryBroker()
    count = publish_source(broker, TOPICS, FakeSource(), NOW, kind="paper")

    assert count == (3, 0)
    published = broker.messages[TOPICS.raw]
    assert len(published) == 3
    envelope = decode(published[0][1])
    assert envelope.kind == "paper" and envelope.source == "fakearxiv"
    assert envelope.payload == {"raw": {"id": "2607.00001"}}
    assert [s.stage for s in envelope.lineage] == ["produce"]
    assert envelope.lineage[0].outcome == "ok"
    assert len(_stub_store["raw"]) == 3  # replay parity: raw items still land
    assert _stub_store["state"] == [("fakearxiv", "page-2"), ("fakearxiv", None)]


def test_publish_source_skips_known_enriched(_stub_store: dict[str, list]) -> None:
    known = {
        ("arxiv", "2607.00001"): (None, True),
        ("arxiv", "2607.00002"): (None, True),
        ("arxiv", "2607.00003"): (None, True),
    }
    broker = InMemoryBroker()
    count = publish_source(broker, TOPICS, FakeSource(), NOW, kind="paper", known=known)

    assert count == (0, 3)
    assert TOPICS.raw not in broker.messages or broker.messages[TOPICS.raw] == []
    assert _stub_store["raw"] == []  # skipped items land nothing
    assert _stub_store["state"] == [("fakearxiv", "page-2"), ("fakearxiv", None)]  # cursor still


def test_should_publish_covers_the_uncertainty_matrix() -> None:
    source = FakeSource()
    fresh = source._raw("2607.00001")
    updated = RawItem(
        source=source.name,
        fetched_at=NOW,
        payload={"id": "2607.00001", "updated": NOW + timedelta(days=1)},
    )
    enriched = {("arxiv", "2607.00001"): (NOW, True)}

    assert _should_publish(source, fresh, {})  # unknown paper
    assert not _should_publish(source, fresh, enriched)  # known, enriched, not newer
    assert _should_publish(source, fresh, {("arxiv", "2607.00001"): (NOW, False)})  # unenriched
    assert _should_publish(source, updated, enriched)  # newer version fetched
    assert _should_publish(source, updated, {("arxiv", "2607.00001"): (None, True)})  # unknown age
    assert _should_publish(BrokenSource(), fresh, enriched)  # normalize error -> parse decides


def test_poll_fulltext_publishes_and_marks_unavailable(
    monkeypatch: pytest.MonkeyPatch, _stub_store: dict[str, list]
) -> None:
    pending = [("arxiv:2607.1", "2607.00001", NOW), ("arxiv:2607.2", "2607.00002", NOW)]
    monkeypatch.setattr(producers_mod, "papers_missing_full_text", lambda *a, **k: pending)
    texts = {"2607.00001": "## S\n\n" + "word " * 2_000_000, "2607.00002": None}
    monkeypatch.setattr(producers_mod, "fetch_full_text", lambda arxiv_id: texts[arxiv_id])
    settings = Settings(arxiv_page_delay_sec=0.0)

    broker = InMemoryBroker()
    poll_fulltext(settings, broker, TOPICS)

    assert TOPICS.raw not in broker.messages  # fulltext rides the slow lane
    published = broker.messages[TOPICS.raw_fulltext]
    assert len(published) == 1
    envelope = decode(published[0][1])
    assert envelope.kind == "fulltext"
    assert envelope.payload["paper_id"] == "arxiv:2607.1"
    assert len(envelope.payload["text"]) == 2_000_000  # capped under the topic message limit
    # The miss goes through the grace-aware recorder (the tombstone decision is its job).
    assert _stub_store["full_text"] == [("arxiv:2607.2", None)]


def test_build_producer_tasks_maps_intervals() -> None:
    settings = Settings(
        stream_poll_interval_sec=111,
        scheduler_signals_interval_sec=222,
        stream_fulltext_interval_sec=333,
    )
    tasks = build_producer_tasks(settings, InMemoryBroker(), TOPICS)
    assert [t.name for t in tasks] == ["produce-content", "produce-signals", "produce-fulltext"]
    assert [t.interval_sec for t in tasks] == [111, 222, 333]
