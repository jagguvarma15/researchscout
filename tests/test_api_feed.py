from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.feed as feed_router
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_session
from researchscout.api.main import create_app
from researchscout.retrieve.personalize import PersonalizedPaper
from researchscout.schema import Author, Paper


class _Embedder:
    model_id = "mock-v1"
    dim = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0]


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: _Embedder()
    app.dependency_overrides[require_user] = lambda: User(sub="tester", username="tester")
    return TestClient(app)


def _paper(pid: str) -> Paper:
    return Paper(
        id=pid,
        title=pid,
        abstract="x",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=datetime.now(UTC),
        source="arxiv",
    )


@pytest.fixture(autouse=True)
def _stub_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feed_router, "get_interests", lambda session, sub: ["vision"])
    monkeypatch.setattr(feed_router, "_record", lambda **fields: None)


def test_feed_serves_items_and_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_rank(*args: Any, **kwargs: Any) -> list[PersonalizedPaper]:
        kwargs["profile"].update(interests=1, saves=3, reads=2, centroids=2)
        return [PersonalizedPaper(paper=_paper("arxiv:1"), score=0.9, distance=0.1, reason="why")]

    monkeypatch.setattr(feed_router, "personalized_papers", fake_rank)
    response = _client().get("/v1/me/feed")
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["id"] == "arxiv:1"
    assert body["items"][0]["reason"] == "why"
    assert body["profile"] == {"interests": 1, "saves": 3, "reads": 2, "centroids": 2}


def test_feed_profile_null_on_cold_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feed_router, "personalized_papers", lambda *a, **k: [])
    body = _client().get("/v1/me/feed").json()
    assert body["items"] == []
    assert body["profile"] is None


def test_feed_default_window_is_freshness_days(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_rank(*args: Any, **kwargs: Any) -> list[PersonalizedPaper]:
        seen["days"] = kwargs["days"]
        return []

    monkeypatch.setattr(feed_router, "personalized_papers", fake_rank)
    _client().get("/v1/me/feed")
    from researchscout.config import get_settings

    assert seen["days"] == get_settings().freshness_days


def test_feed_forwards_days_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_rank(*args: Any, **kwargs: Any) -> list[PersonalizedPaper]:
        seen.update(days=kwargs["days"], k=kwargs["k"])
        return []

    monkeypatch.setattr(feed_router, "personalized_papers", fake_rank)
    _client().get("/v1/me/feed?days=7&limit=5")
    assert seen == {"days": 7, "k": 5}


def test_feed_records_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {}

    def fake_rank(*args: Any, **kwargs: Any) -> list[PersonalizedPaper]:
        kwargs["timings"].update(cache_hit=1.0, candidates=12.0)
        kwargs["profile"].update(interests=1, saves=0, reads=0, centroids=1)
        return [PersonalizedPaper(paper=_paper("arxiv:1"), score=0.5, distance=0.5, reason=None)]

    monkeypatch.setattr(feed_router, "personalized_papers", fake_rank)
    monkeypatch.setattr(feed_router, "_record", lambda **fields: recorded.update(fields))
    _client().get("/v1/me/feed?limit=10")
    assert recorded["returned"] == 1
    assert recorded["candidates"] == 12
    assert recorded["profile_cache_hit"] is True
    assert recorded["k"] == 10
    assert recorded["user_hash"] is not None  # owner_tag of the sub, never the sub


@pytest.mark.parametrize("query", ["days=0", "days=366", "limit=0", "limit=101"])
def test_feed_rejects_out_of_range(query: str) -> None:
    assert _client().get(f"/v1/me/feed?{query}").status_code == 422
