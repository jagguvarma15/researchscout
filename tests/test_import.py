"""Single-paper import: fetch faked in unit tests, landing verified against the store."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import researchscout.importing as importing_mod
from researchscout.config import Settings
from researchscout.embed.base import Embedder
from researchscout.importing import fetch_arxiv_entry, import_paper, publish_enrichment
from researchscout.store.models import PaperEmbeddingRow, RawItemRow, SavedPaperRow
from researchscout.store.papers import get_paper
from researchscout.stream.envelope import decode


class _TinyEmbedder(Embedder):
    model_id = "test-tiny"
    dim = 384

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 384


_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2607.22222v1</id>
    <title>Imported \\textbf{Paper}</title>
    <summary>An imported abstract.</summary>
    <author><name>C. Three</name></author>
    <published>2026-07-15T00:00:00Z</published>
    <updated>2026-07-16T00:00:00Z</updated>
  </entry>
</feed>
"""

_EMPTY_FEED = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_fetch_arxiv_entry_returns_payload_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_ATOM))
    payload = fetch_arxiv_entry("2607.22222")
    assert payload is not None and payload["id"].endswith("2607.22222v1")

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_EMPTY_FEED))
    assert fetch_arxiv_entry("9999.00000") is None


def test_publish_enrichment_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[tuple[str, str, bytes]] = []

    class FakeBroker:
        def __init__(self, bootstrap: str) -> None:
            pass

        def publish(self, topic: str, key: str, value: bytes) -> None:
            published.append((topic, key, value))

        def flush(self, timeout: float | None = None) -> None:
            return None

    monkeypatch.setattr(importing_mod, "KafkaBroker", FakeBroker)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_ATOM))
    payload = fetch_arxiv_entry("2607.22222")
    assert payload is not None

    assert publish_enrichment(Settings(), payload) is True
    topic, key, value = published[0]
    assert topic == "rs.raw.v1"
    envelope = decode(value)
    assert envelope.kind == "paper" and envelope.payload["raw"] == payload

    class BrokenBroker(FakeBroker):
        def publish(self, topic: str, key: str, value: bytes) -> None:
            raise RuntimeError("no broker")

    monkeypatch.setattr(importing_mod, "KafkaBroker", BrokenBroker)
    assert publish_enrichment(Settings(), payload) is False  # never raises


@pytest.mark.integration
def test_import_paper_lands_saves_and_converges(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_ATOM))
    payload = fetch_arxiv_entry("2607.22222")
    assert payload is not None

    paper_id, title, already_known, embedded = import_paper(session, "local", payload)
    assert paper_id == "arxiv:2607.22222"
    assert title == "Imported Paper"  # the stream's TeX cleanup applied
    assert already_known is False
    assert embedded is False  # no embedder passed, none written

    paper = get_paper(session, paper_id)
    assert paper is not None and paper.abstract == "An imported abstract."
    saved = session.execute(select(SavedPaperRow.paper_id)).scalars().all()
    assert saved == [paper_id]  # auto-saved to the Reading list
    raw_count = session.execute(select(RawItemRow)).scalars().all()
    assert len(raw_count) == 1

    def _count(model: Any) -> int:
        return len(session.execute(select(model)).scalars().all())

    again_id, _, again_known, _ = import_paper(session, "local", payload)
    assert again_id == paper_id and again_known is True
    assert _count(RawItemRow) == 1  # re-imports never duplicate the raw row
    assert _count(SavedPaperRow) == 1


@pytest.mark.integration
def test_import_paper_embeds_synchronously(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an embedder, the vector lands in the same transaction as the paper."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_ATOM))
    payload = fetch_arxiv_entry("2607.22222")
    assert payload is not None

    paper_id, _, _, embedded = import_paper(session, "local", payload, _TinyEmbedder())
    assert embedded is True
    row = session.execute(
        select(PaperEmbeddingRow).where(PaperEmbeddingRow.paper_id == paper_id)
    ).scalar_one()
    assert row.model_id == "test-tiny"
