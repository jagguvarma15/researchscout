from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.schema import Author, Paper
from researchscout.store.papers import upsert_paper
from researchscout.store.vectors import index_papers, papers_missing_embedding, search

pytestmark = pytest.mark.integration

DIM = 384


def _onehot(i: int) -> list[float]:
    vector = [0.0] * DIM
    vector[i] = 1.0
    return vector


class MockEmbedder(Embedder):
    """Maps a known document/query text to a fixed vector — no torch."""

    model_id = "mock-v1"
    dim = DIM

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._mapping[text]


def _paper(arxiv: str, title: str, abstract: str = "x") -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract=abstract,
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def test_index_is_idempotent_and_search_ranks_by_distance(session: Session) -> None:
    fixtures = {
        "2401.00001": ("Transformers", _onehot(0)),
        "2401.00002": ("Diffusion", _onehot(1)),
        "2401.00003": ("Graphs", _onehot(2)),
    }
    mapping: dict[str, list[float]] = {}
    for arxiv, (title, vector) in fixtures.items():
        upsert_paper(session, _paper(arxiv, title))
        mapping[f"{title}\n\nx"] = vector
    session.flush()
    embedder = MockEmbedder(mapping)

    assert index_papers(session, embedder) == 3
    assert index_papers(session, embedder) == 0  # idempotent
    assert papers_missing_embedding(session, embedder.model_id) == []

    results = search(session, _onehot(0), k=3)
    assert results[0][0] == "arxiv:2401.00001"
    assert results[0][1] == pytest.approx(0.0, abs=1e-6)
    assert len(results) == 3


def test_local_embedder_contract() -> None:
    from researchscout.embed.local import LocalEmbedder

    embedder = LocalEmbedder()
    assert embedder.dim == 384

    docs = embedder.embed_documents(["hello world", "a second document"])
    assert len(docs) == 2
    assert len(docs[0]) == 384
    norm = sum(x * x for x in docs[0]) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-3)  # normalized

    query = embedder.embed_query("hello world")
    assert len(query) == 384
    assert query != docs[0]  # query instruction prefix changes the vector
