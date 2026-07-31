import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.keywords as keywords_router
from researchscout.api.deps import get_session
from researchscout.api.main import create_app

_RANKED = [("sparse attention", 2), ("routing", 1)]


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def fake_counts(session: object, *, limit: int) -> tuple[list[tuple[str, int]], int]:
        return _RANKED[:limit], len(_RANKED)

    monkeypatch.setattr(keywords_router, "keyword_counts", fake_counts)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_keywords_returns_ranked_items(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch).get("/v1/keywords")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {"keyword": "sparse attention", "papers": 2},
        {"keyword": "routing", "papers": 1},
    ]
    assert body["total"] == 2


def test_keywords_limit_caps_items_not_total(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _client(monkeypatch).get("/v1/keywords?limit=1").json()
    assert [item["keyword"] for item in body["items"]] == ["sparse attention"]
    assert body["total"] == 2


def test_keywords_limit_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/v1/keywords?limit=0").status_code == 422
    assert client.get("/v1/keywords?limit=2001").status_code == 422
