from datetime import UTC, datetime
from pathlib import Path

import bytewax.operators as op
import pytest
from bytewax.connectors.kafka import KafkaSourceMessage
from bytewax.dataflow import Dataflow
from bytewax.testing import TestingSink, TestingSource, run_main

import researchscout.stream.categorize as categorize_mod
from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.sources.arxiv import _entry_payload
from researchscout.stream.broker import StreamTopics
from researchscout.stream.categorize import Categorized, Categorizer
from researchscout.stream.envelope import Envelope, decode, encode
from researchscout.stream.flow import FlowDeps, build_flow, decode_message, to_sink_message
from researchscout.stream.parse import parse_stage

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_query.atom"


class _NullEmbedder(Embedder):
    model_id = "fake"
    dim = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 1.0]


class _NullLLM(LLM):
    model = "fake"

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        return "keywords, from, the, model"


def _arxiv_envelope() -> Envelope:
    import feedparser

    feed = feedparser.parse(FIXTURE.read_text())
    return Envelope(
        kind="paper",
        source="arxiv",
        fetched_at=datetime(2026, 7, 30, tzinfo=UTC),
        payload={"raw": _entry_payload(feed.entries[0])},
    )


def _bad_source_envelope() -> Envelope:
    return Envelope(
        kind="paper",
        source="no-such-source",
        fetched_at=datetime(2026, 7, 30, tzinfo=UTC),
        payload={"raw": {}},
    )


def _fake_inject(item: Categorized) -> Envelope:
    stamp = item.envelope.begin("inject")
    item.envelope.finish(stamp)
    return item.envelope


def _fake_inject_batch(items: list[Categorized]) -> list[Envelope]:
    return [_fake_inject(item) for item in items]


@pytest.mark.parametrize("batch_size", [1, 2])
def test_stages_compose_under_run_main(monkeypatch: pytest.MonkeyPatch, batch_size: int) -> None:
    monkeypatch.setattr(categorize_mod, "list_topics", lambda session: [])
    from contextlib import nullcontext

    categorizer = Categorizer(
        _NullEmbedder(),
        _NullLLM(),
        lambda: nullcontext(None),
        topic_match_min=0.55,
        keyword_min_similarity=0.35,
        keywords_llm_fallback=True,
        labels=[],
    )

    flow = Dataflow("test-stream")
    packets = op.input(
        "in",
        flow,
        TestingSource([_arxiv_envelope(), _bad_source_envelope()], batch_size=batch_size),
    )
    parsed = op.map("parse", packets, parse_stage)
    categorized = op.flat_map_batch("categorize", parsed, categorizer.run_batch)
    injected = op.flat_map_batch("inject", categorized, _fake_inject_batch)
    out: list[Envelope] = []
    op.output("out", injected, TestingSink(out))
    run_main(flow)

    assert len(out) == 2
    ok, bad = out
    assert [s.stage for s in ok.lineage] == ["parse", "categorize", "inject"]
    assert [s.outcome for s in ok.lineage] == ["ok", "ok", "ok"]
    assert ok.payload["enrichment"]["group"] == "cs"
    assert [s.outcome for s in bad.lineage] == ["error", "skipped", "ok"]


def _null_deps() -> FlowDeps:
    return FlowDeps(
        parse=lambda envelope: envelope,
        categorize_batch=lambda envelopes: [Categorized(e, None) for e in envelopes],
        inject_batch=lambda items: [item.envelope for item in items],
    )


def test_build_flow_taps_toggle() -> None:
    topics = StreamTopics.for_prefix("rs")
    with_taps = build_flow("localhost:9092", topics, "rs-stream", _null_deps())
    step_ids = [step.step_id for step in with_taps.substeps]
    assert any("parsed-tap" in s for s in step_ids)
    assert any("enriched-tap" in s for s in step_ids)

    without = build_flow("localhost:9092", topics, "rs-stream", _null_deps(), taps=False)
    step_ids = [step.step_id for step in without.substeps]
    assert not any("tap" in s for s in step_ids)
    assert any("inject" in s for s in step_ids)  # the stages themselves remain


def test_build_flow_merges_the_fulltext_lane() -> None:
    topics = StreamTopics.for_prefix("rs")
    flow = build_flow("localhost:9092", topics, "rs-stream", _null_deps())
    step_ids = [step.step_id for step in flow.substeps]
    assert any("raw-in" in s for s in step_ids)
    assert any("fulltext-in" in s for s in step_ids)
    assert any("merge-raw" in s for s in step_ids)


def test_decode_message_drops_bad_bytes() -> None:
    envelope = _arxiv_envelope()
    good = KafkaSourceMessage(key=None, value=encode(envelope))
    decoded = decode_message(good)
    assert decoded is not None and decoded.event_id == envelope.event_id
    assert decode_message(KafkaSourceMessage(key=None, value=b"junk")) is None
    assert decode_message(KafkaSourceMessage(key=None, value=None)) is None


def test_to_sink_message_keys_by_canonical_id() -> None:
    envelope = _arxiv_envelope()
    parse_stage(envelope)
    message = to_sink_message(envelope)
    assert message.key == b"arxiv:2401.12345"
    assert decode(message.value).event_id == envelope.event_id
