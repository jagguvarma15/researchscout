from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.schema import Author, Paper
from researchscout.store.chunks import best_chunk_texts, best_chunks, index_chunks, search_chunks
from researchscout.store.papers import set_full_text, upsert_paper

pytestmark = pytest.mark.integration

DIM = 384


def _onehot(i: int) -> list[float]:
    vector = [0.0] * DIM
    vector[i] = 1.0
    return vector


class MockEmbedder(Embedder):
    """Routes each chunk to a one-hot slot by a keyword in its text."""

    model_id = "mock-v1"
    dim = DIM

    def __init__(self, slots: dict[str, int]) -> None:
        self._slots = slots

    def _vector(self, text: str) -> list[float]:
        for keyword, slot in self._slots.items():
            if keyword in text:
                return _onehot(slot)
        return _onehot(DIM - 1)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _paper(pid: str, arxiv: str) -> Paper:
    return Paper(
        id=pid,
        external_ids={"arxiv": arxiv},
        title=pid,
        abstract="a",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        source="arxiv",
    )


def test_index_and_search_chunks_pool_to_papers(session: Session) -> None:
    upsert_paper(session, _paper("arxiv:2607.00001", "2607.00001"))
    upsert_paper(session, _paper("arxiv:2607.00002", "2607.00002"))
    upsert_paper(session, _paper("arxiv:2607.00003", "2607.00003"))  # checked, no HTML
    session.flush()
    set_full_text(
        session,
        "arxiv:2607.00001",
        "## Methods\n\nwe use sinkhorn iterations\n\n## Results\n\nsinkhorn converges fast",
    )
    set_full_text(session, "arxiv:2607.00002", "## Methods\n\npolicy gradients everywhere")
    set_full_text(session, "arxiv:2607.00003", "")
    embedder = MockEmbedder({"sinkhorn": 0, "policy": 1})

    written = index_chunks(session, embedder)
    assert written == 3  # two sections + one section
    assert index_chunks(session, embedder) == 0  # idempotent

    hits = search_chunks(session, _onehot(0), model_id=embedder.model_id, k=5)
    assert hits[0][0] == "arxiv:2607.00001"  # both its chunks pool to one paper entry
    assert len([pid for pid, _ in hits if pid == "arxiv:2607.00001"]) == 1
    assert hits[0][1] == pytest.approx(0.0, abs=1e-3)

    quotes = best_chunk_texts(session, _onehot(1), ["arxiv:2607.00002"], model_id=embedder.model_id)
    assert "policy gradients" in quotes["arxiv:2607.00002"]

    found = best_chunks(
        session,
        _onehot(0),
        ["arxiv:2607.00001", "arxiv:2607.00002"],
        model_id=embedder.model_id,
    )
    assert set(found) == {"arxiv:2607.00001", "arxiv:2607.00002"}
    text, dist = found["arxiv:2607.00001"]
    assert "sinkhorn" in text and dist == pytest.approx(0.0, abs=1e-3)
    _, off_dist = found["arxiv:2607.00002"]
    assert off_dist > 0.5  # the orthogonal paper's best chunk is measurably far


def test_search_chunks_scoped_by_model(session: Session) -> None:
    upsert_paper(session, _paper("arxiv:2607.00001", "2607.00001"))
    session.flush()
    set_full_text(session, "arxiv:2607.00001", "## S\n\nsinkhorn text")
    index_chunks(session, MockEmbedder({"sinkhorn": 0}))
    assert search_chunks(session, _onehot(0), model_id="other-model", k=5) == []
