"""Unit coverage for the batch inject degradation path (no database)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from researchscout.embed.base import Embedder
from researchscout.stream.categorize import Categorized
from researchscout.stream.envelope import Envelope
from researchscout.stream.inject import Injector


class _NullEmbedder(Embedder):
    model_id = "null"
    dim = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 1.0]


@contextmanager
def _broken_session() -> Iterator[None]:
    raise RuntimeError("database is down")
    yield None


def _item(event_id: str) -> Categorized:
    envelope = Envelope(
        event_id=event_id,
        kind="paper",
        source="arxiv",
        fetched_at=datetime(2026, 7, 31, tzinfo=UTC),
        payload={"paper": {"id": f"arxiv:{event_id}"}},
    )
    return Categorized(envelope, None)


def test_batch_failure_degrades_to_serial_and_stamps_errors() -> None:
    injector = Injector(_NullEmbedder(), _broken_session)
    out = injector.run_batch([_item("u1"), _item("u2")])

    assert [envelope.event_id for envelope in out] == ["u1", "u2"]
    for envelope in out:
        # The batch session died before any per-item work, so only the serial retry
        # stamped inject; lineage recording itself failed too, which is survivable.
        inject_stamps = [s for s in envelope.lineage if s.stage == "inject"]
        assert len(inject_stamps) == 1
        assert inject_stamps[0].outcome == "error"


def test_empty_batch_is_a_no_op() -> None:
    assert Injector(_NullEmbedder(), _broken_session).run_batch([]) == []
