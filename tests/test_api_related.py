"""The related-neighborhood endpoint over a real graph: references, citers, neighbors."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from researchscout.api.deps import get_embedder, get_session
from researchscout.api.main import create_app
from researchscout.embed.base import Embedder
from researchscout.schema import Author, Paper, Signal, SignalType
from researchscout.store.citations import store_references
from researchscout.store.papers import upsert_paper
from researchscout.store.signals import append_signal
from researchscout.store.vectors import upsert_embedding

pytestmark = pytest.mark.integration

DIM = 384


def _onehot(i: int) -> list[float]:
    vector = [0.0] * DIM
    vector[i] = 1.0
    return vector


class MockEmbedder(Embedder):
    model_id = "mock-v1"
    dim = DIM

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_onehot(0) for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return _onehot(0)


def _paper(arxiv: str, title: str) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime.now(UTC) - timedelta(days=1),
        source="arxiv",
    )


def _client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_embedder] = lambda: MockEmbedder()
    return TestClient(app)


def test_related_collects_graph_and_similar(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001", "The Paper"))
    upsert_paper(session, _paper("2401.00002", "A Reference"))
    upsert_paper(session, _paper("2401.00003", "A Citer"))
    upsert_paper(session, _paper("2401.00004", "A Twin"))
    upsert_embedding(session, "arxiv:2401.00001", "mock-v1", _onehot(1))
    upsert_embedding(session, "arxiv:2401.00002", "mock-v1", _onehot(2))
    upsert_embedding(session, "arxiv:2401.00003", "mock-v1", _onehot(3))
    upsert_embedding(session, "arxiv:2401.00004", "mock-v1", _onehot(1))
    # The paper references a stored work and one outside the corpus; a third work cites it.
    store_references(session, "arxiv:2401.00001", ["2401.00002", "9999.00000"])
    store_references(session, "arxiv:2401.00003", ["2401.00001"])
    session.commit()

    response = _client(session).get("/v1/papers/arxiv:2401.00001/related")
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["references"]] == ["arxiv:2401.00002"]
    assert [item["id"] for item in body["cited_by"]] == ["arxiv:2401.00003"]
    similar_ids = [item["id"] for item in body["similar"]]
    # The identical twin ranks first and the paper itself never appears.
    assert similar_ids[0] == "arxiv:2401.00004"
    assert "arxiv:2401.00001" not in similar_ids


def test_related_unknown_paper_is_404(session: Session) -> None:
    assert _client(session).get("/v1/papers/arxiv:0000.00000/related").status_code == 404


def test_detail_serves_breakthrough_score(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001", "Quiet"))
    upsert_paper(session, _paper("2401.00002", "Rising"))
    append_signal(
        session,
        Signal(
            paper_id="arxiv:2401.00002",
            type=SignalType.citation,
            source="test",
            value=50.0,
            observed_at=datetime.now(UTC),
        ),
    )
    session.commit()

    client = _client(session)
    quiet = client.get("/v1/papers/arxiv:2401.00001").json()
    rising = client.get("/v1/papers/arxiv:2401.00002").json()
    assert quiet["score"] is None
    assert rising["score"] is not None
    assert rising["score"] > 0
