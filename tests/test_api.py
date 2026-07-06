from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from openai import OpenAIError

import researchscout.api.routers.ask as ask_router
import researchscout.api.routers.papers as papers_router
from researchscout.answer import Answer
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.main import create_app
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper


def _paper(pid: str = "arxiv:2401.00001", title: str = "T") -> Paper:
    return Paper(
        id=pid,
        title=title,
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="tester", username="tester")
    return TestClient(app)


def test_healthz() -> None:
    response = _client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_papers_lists_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(papers_router, "list_papers", lambda *a, **k: [_paper()])
    response = _client().get("/v1/papers")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == ["arxiv:2401.00001"]
    assert items[0]["score"] is None


def test_papers_query_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    scored = ScoredPaper(paper=_paper(), score=0.9, distance=0.1)
    seen: dict[str, object] = {}

    def fake_retrieve(session: object, embedder: object, q: str, **kwargs: object) -> list:
        seen["q"] = q
        seen.update(kwargs)
        return [scored]

    monkeypatch.setattr(papers_router, "retrieve", fake_retrieve)
    response = _client().get("/v1/papers", params={"q": "state space models", "limit": 5})
    assert response.status_code == 200
    assert response.json()["items"][0]["score"] == 0.9
    assert seen["q"] == "state space models"
    assert seen["k"] == 5


def test_paper_detail_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(papers_router, "get_paper", lambda *a, **k: None)
    assert _client().get("/v1/papers/arxiv:0000.00000").status_code == 404


def test_paper_detail_allows_slashes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_get_paper(session: object, paper_id: str) -> Paper:
        seen["id"] = paper_id
        return _paper(pid=paper_id)

    monkeypatch.setattr(papers_router, "get_paper", fake_get_paper)
    response = _client().get("/v1/papers/doi:10.1145/3600006.3613165")
    assert response.status_code == 200
    assert seen["id"] == "doi:10.1145/3600006.3613165"


def test_ask_returns_grounded_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    used = ScoredPaper(paper=_paper(), score=1.0, distance=0.0)
    result = Answer(
        text="See [arxiv:2401.00001].",
        cited=["arxiv:2401.00001"],
        hallucinated=["arxiv:9999.99999"],
        used=[used],
    )
    monkeypatch.setattr(ask_router, "answer", lambda *a, **k: result)
    response = _client().post("/v1/ask", json={"question": "what is new?"})
    assert response.status_code == 200
    body = response.json()
    assert body["cited"] == ["arxiv:2401.00001"]
    assert body["hallucinated"] == ["arxiv:9999.99999"]
    assert body["used"][0]["id"] == "arxiv:2401.00001"


def test_ask_rejects_empty_question() -> None:
    assert _client().post("/v1/ask", json={"question": ""}).status_code == 422


def test_ask_maps_llm_failure_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> Answer:
        raise OpenAIError("connection refused")

    monkeypatch.setattr(ask_router, "answer", boom)
    response = _client().post("/v1/ask", json={"question": "q"})
    assert response.status_code == 502
