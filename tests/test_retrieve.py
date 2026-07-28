from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.retrieve.search import retrieve
from researchscout.schema import Author, Paper, Signal, SignalType
from researchscout.store.papers import upsert_paper
from researchscout.store.signals import append_signal
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


def _setup_hybrid(session: Session) -> MockEmbedder:
    """Three same-age papers: two near-duplicates (same vector) and one lexical target."""
    rows = [
        ("2402.00001", "Gradient descent dynamics", 0),
        ("2402.00002", "Gradient descent convergence", 0),
        ("2402.00003", "Sparse quantization tricks", 1),
    ]
    # Both query vectors are orthogonal to the quantization paper's document vector, so any
    # ranking of it above the others must come from the lexical leg, not similarity.
    mapping: dict[str, list[float]] = {"q": _onehot(0), "quantization": _onehot(2)}
    for arxiv, title, slot in rows:
        upsert_paper(session, _paper(arxiv, title, 2))
        mapping[f"{title}\n\nx"] = _onehot(slot)
    session.flush()
    embedder = MockEmbedder(mapping)
    index_papers(session, embedder)
    return embedder


def test_lexical_match_ranks_first_despite_orthogonal_vector(session: Session) -> None:
    embedder = _setup_hybrid(session)
    results = retrieve(session, embedder, "quantization", k=10, days=30)
    ids = [item.paper.id for item in results]
    assert "arxiv:2402.00003" in ids
    # RRF: the lexical hit collects contributions from both legs and floats to the top.
    assert ids[0] == "arxiv:2402.00003"


def test_selective_filter_still_fills_the_candidate_pool(session: Session) -> None:
    """A facet filter must not starve the vector leg: every in-filter paper comes back."""
    mapping: dict[str, list[float]] = {"q": _onehot(0)}
    for i in range(30):
        category = "cs.AI" if i < 5 else "cs.LG"
        paper = _paper(f"2403.{i:05d}", f"Paper {i}", 2, category)
        upsert_paper(session, paper)
        mapping[f"Paper {i}\n\nx"] = _onehot(i % DIM)
    session.flush()
    embedder = MockEmbedder(mapping)
    index_papers(session, embedder)

    results = retrieve(session, embedder, "q", k=40, days=30, categories=["cs.AI"])
    ids = {item.paper.id for item in results}
    assert ids == {f"arxiv:2403.{i:05d}" for i in range(5)}


def test_cited_paper_outranks_uncited_near_duplicate(session: Session) -> None:
    embedder = _setup_hybrid(session)
    append_signal(
        session,
        Signal(
            paper_id="arxiv:2402.00002",
            type=SignalType.citation,
            source="test",
            value=50.0,
            observed_at=datetime.now(UTC),
        ),
    )
    results = retrieve(session, embedder, "q", k=10, days=30)
    ids = [item.paper.id for item in results]
    # Same vector, same age: only the citation authority separates the two.
    assert ids.index("arxiv:2402.00002") < ids.index("arxiv:2402.00001")
