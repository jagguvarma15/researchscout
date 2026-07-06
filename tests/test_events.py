import json
from datetime import UTC, datetime

import pytest

import researchscout.ingest.pipeline as pipeline_mod
import researchscout.workers.embed_worker as embed_mod
import researchscout.workers.ingest_worker as ingest_worker_mod
from researchscout.events.schemas import TOPIC_PAPERS_NEW, IngestJob, PaperCreated
from researchscout.events.sink import KafkaEventSink, NullSink
from researchscout.ingest.pipeline import IngestSummary, run_ingest
from researchscout.schema import Author, Paper, Signal, SignalType
from researchscout.sources.base import RawItem, Source
from researchscout.workers.embed_worker import handle_paper
from researchscout.workers.ingest_worker import handle_job

SINCE = datetime(2024, 1, 1, tzinfo=UTC)


def _paper(arxiv: str) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=SINCE,
        source="arxiv",
    )


class FakeContentSource(Source):
    name = "fake-events"
    kind = "content"

    def __init__(self, papers: list[Paper]) -> None:
        self._papers = papers

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        items = [
            RawItem(source=self.name, fetched_at=SINCE, payload=p.model_dump(mode="json"))
            for p in self._papers
        ]
        return items, None

    def normalize(self, raw: RawItem) -> Paper:
        return Paper.model_validate(raw.payload)


class FakeSignalSource(FakeContentSource):
    name = "fake-signal-events"
    kind = "signal"

    def normalize(self, raw: RawItem) -> Signal:
        paper = Paper.model_validate(raw.payload)
        return Signal(
            paper_id=paper.id,
            type=SignalType.citation,
            source=self.name,
            value=1.0,
            observed_at=SINCE,
        )


class RecordingSink:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.flushed = 0

    def paper_created(self, paper: Paper) -> None:
        self.created.append(paper.id)

    def flush(self) -> None:
        self.flushed += 1


def _silence_persistence(monkeypatch: pytest.MonkeyPatch, *, existing: str | None = None) -> None:
    monkeypatch.setattr(pipeline_mod, "append_raw", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "append_signal", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "upsert_paper", lambda *a, **k: "x")
    monkeypatch.setattr(pipeline_mod, "link_external_ids", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "find_by_external_id", lambda *a, **k: existing)


def test_new_papers_reach_the_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_persistence(monkeypatch)
    sink = RecordingSink()
    papers = [_paper("2401.00001"), _paper("2401.00002")]
    run_ingest(None, FakeContentSource(papers), SINCE, events=sink)
    assert sink.created == ["arxiv:2401.00001", "arxiv:2401.00002"]
    assert sink.flushed == 1


def test_collapsed_papers_stay_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_persistence(monkeypatch, existing="arxiv:2401.00001")
    sink = RecordingSink()
    summary = run_ingest(None, FakeContentSource([_paper("2401.00001")]), SINCE, events=sink)
    assert summary.collapsed == 1
    assert sink.created == []


def test_signals_stay_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_persistence(monkeypatch)
    sink = RecordingSink()
    summary = run_ingest(None, FakeSignalSource([_paper("2401.00001")]), SINCE, events=sink)
    assert summary.signals == 1
    assert sink.created == []


def test_default_sink_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_persistence(monkeypatch)
    summary = run_ingest(None, FakeContentSource([_paper("2401.00001")]), SINCE)
    assert summary.new_papers == 1  # no sink argument, nothing blows up


def test_schemas_round_trip() -> None:
    job = IngestJob(source="arxiv", since=SINCE, max_items=5, categories=["cs.LG"])
    assert IngestJob.model_validate_json(job.model_dump_json()) == job
    event = PaperCreated(paper=_paper("2401.00001"))
    assert PaperCreated.model_validate_json(event.model_dump_json()) == event


class FakeProducer:
    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes, bytes]] = []
        self.flushed = 0

    def produce(self, topic: str, key: bytes, value: bytes) -> None:
        self.produced.append((topic, key, value))

    def flush(self) -> None:
        self.flushed += 1


def test_kafka_sink_produces_keyed_json() -> None:
    producer = FakeProducer()
    sink = KafkaEventSink(producer=producer)
    sink.paper_created(_paper("2401.00001"))
    sink.flush()
    topic, key, value = producer.produced[0]
    assert topic == TOPIC_PAPERS_NEW
    assert key == b"arxiv:2401.00001"
    assert json.loads(value)["paper"]["id"] == "arxiv:2401.00001"
    assert producer.flushed == 1


def test_handle_job_routes_category_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_ingest(session: object, source: object, since: object, **kwargs: object):
        seen["categories"] = getattr(source, "categories", None)
        seen.update(kwargs)
        return IngestSummary(source="arxiv")

    monkeypatch.setattr(ingest_worker_mod, "run_ingest", fake_run_ingest)
    job = IngestJob(source="arxiv", since=SINCE, max_items=5, categories=["cs.CV"])
    sink = NullSink()
    handle_job(None, job, sink)
    assert seen["categories"] == ["cs.CV"]
    assert seen["max_items"] == 5
    assert seen["events"] is sink


def test_handle_paper_embeds_and_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEmbedder:
        model_id = "fake-model"
        dim = 3

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["T\n\nA"]  # title + abstract, same as index_papers
            return [[0.1, 0.2, 0.3]]

        def embed_query(self, text: str) -> list[float]:
            raise NotImplementedError

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        embed_mod,
        "upsert_embedding",
        lambda session, paper_id, model_id, vector: seen.update(
            {"paper_id": paper_id, "model_id": model_id, "vector": vector}
        ),
    )
    handle_paper(None, FakeEmbedder(), PaperCreated(paper=_paper("2401.00001")))
    assert seen == {
        "paper_id": "arxiv:2401.00001",
        "model_id": "fake-model",
        "vector": [0.1, 0.2, 0.3],
    }
