from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.retrieve.search import retrieve
from researchscout.schema import Author, Paper
from researchscout.store.papers import upsert_paper
from researchscout.store.vectors import index_papers

pytestmark = pytest.mark.integration

DIM = 384


def _onehot(i: int) -> list[float]:
    vector = [0.0] * DIM
    vector[i] = 1.0
    return vector


class MockEmbedder(Embedder):
    model_id = "mock-v1"
    dim = DIM

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._mapping[text]


def _paper(arxiv: str, title: str, days_ago: int, category: str = "cs.LG") -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="x",
        authors=[Author(name="A")],
        categories=[category],
        published_at=datetime.now(UTC) - timedelta(days=days_ago),
        source="arxiv",
    )


def _setup(session: Session) -> MockEmbedder:
    rows = [
        ("2401.00001", "Recent", 2, "cs.LG", 0),
        ("2401.00002", "Older", 20, "cs.LG", 0),
        ("2401.00003", "Stale", 100, "cs.LG", 0),
        ("2401.00004", "Other", 2, "cs.AI", 1),
    ]
    mapping: dict[str, list[float]] = {"q": _onehot(0)}
    for arxiv, title, days_ago, category, slot in rows:
        upsert_paper(session, _paper(arxiv, title, days_ago, category))
        mapping[f"{title}\n\nx"] = _onehot(slot)
    session.flush()
    embedder = MockEmbedder(mapping)
    index_papers(session, embedder)
    return embedder


def test_freshness_window_excludes_stale(session: Session) -> None:
    embedder = _setup(session)
    ids = [item.paper.id for item in retrieve(session, embedder, "q", k=10, days=30)]
    assert "arxiv:2401.00003" not in ids  # 100 days old, outside the window
    assert "arxiv:2401.00001" in ids


def test_recency_reweighting_ranks_newer_first(session: Session) -> None:
    embedder = _setup(session)
    results = retrieve(session, embedder, "q", k=10, days=30)
    ids = [item.paper.id for item in results]
    # Recent and Older are equally similar (same vector); recency puts Recent first.
    assert ids.index("arxiv:2401.00001") < ids.index("arxiv:2401.00002")
    assert results[0].paper.id == "arxiv:2401.00001"


def test_category_filter(session: Session) -> None:
    embedder = _setup(session)
    results = retrieve(session, embedder, "q", k=10, days=30, categories=["cs.AI"])
    ids = [item.paper.id for item in results]
    assert ids == ["arxiv:2401.00004"]
