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

    results = search(session, _onehot(0), model_id=embedder.model_id, k=3)
    assert results[0][0] == "arxiv:2401.00001"
    assert results[0][1] == pytest.approx(0.0, abs=1e-6)
    assert len(results) == 3

    # Vectors from another embedding space are never searched.
    assert search(session, _onehot(0), model_id="other-model", k=3) == []


def test_query_prefix_is_bge_only() -> None:
    from researchscout.embed.local import query_prefix_for

    assert query_prefix_for("BAAI/bge-small-en-v1.5").startswith("Represent this sentence")
    assert query_prefix_for("BAAI/bge-base-en-v1.5") != ""
    assert query_prefix_for("ibm-granite/granite-embedding-small-english-r2") == ""
    assert query_prefix_for("BAAI/bge-m3") == ""  # multilingual family: no english prefix


def test_default_embedder_honors_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from researchscout.embed.factory import default_embedder

    default_embedder.cache_clear()
    monkeypatch.setenv("RS_EMBEDDING_MODEL", "intfloat/e5-small-v2")
    try:
        assert default_embedder().model_id == "intfloat/e5-small-v2"
        assert default_embedder() is default_embedder()  # one model per process
    finally:
        default_embedder.cache_clear()


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
