import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.retrieve.personalize import _cosine, interest_centroid, personalized_papers


class StubEmbedder(Embedder):
    model_id = "stub"
    dim = 3

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._mapping[text]


def test_interest_centroid_averages_and_normalizes() -> None:
    embedder = StubEmbedder({"a": [3.0, 0.0, 0.0], "b": [0.0, 4.0, 0.0]})
    centroid = interest_centroid(embedder, ["a", "b"])
    assert centroid == pytest.approx([0.6, 0.8, 0.0])  # mean [1.5, 2, 0] normalized
    assert math.isclose(sum(x * x for x in centroid), 1.0)


def test_interest_centroid_none_when_empty() -> None:
    embedder = StubEmbedder({})
    assert interest_centroid(embedder, []) is None
    assert interest_centroid(embedder, ["  ", ""]) is None


def test_cosine() -> None:
    assert _cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == 0.0
    assert _cosine([1.0, 0.0], [0.5, 0.0]) == 0.5


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


@pytest.mark.integration
def test_personalized_ranks_interest_aligned_first(session: Session) -> None:
    from researchscout.schema import Author, Paper
    from researchscout.store.papers import upsert_paper
    from researchscout.store.vectors import upsert_embedding

    def _paper(pid: str) -> Paper:
        return Paper(
            id=pid,
            external_ids={"arxiv": pid.split(":")[1]},
            title=pid,
            abstract="x",
            authors=[Author(name="A")],
            categories=["cs.LG"],
            published_at=datetime.now(UTC) - timedelta(days=1),
            source="arxiv",
        )

    embedder = MockEmbedder({"vision": _onehot(1)})
    upsert_paper(session, _paper("arxiv:1"))
    upsert_paper(session, _paper("arxiv:2"))
    upsert_embedding(session, "arxiv:1", embedder.model_id, _onehot(1))  # aligned with "vision"
    upsert_embedding(session, "arxiv:2", embedder.model_id, _onehot(0))  # orthogonal
    session.flush()

    results = personalized_papers(session, embedder, ["vision"], k=10, days=30)
    assert [item.paper.id for item in results] == ["arxiv:1", "arxiv:2"]


@pytest.mark.integration
def test_personalized_cold_start_is_empty(session: Session) -> None:
    assert personalized_papers(session, MockEmbedder({}), [], k=10, days=30) == []
