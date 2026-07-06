from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.saved as saved_router
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.schema import Author, Paper


def _paper(pid: str = "arxiv:2401.00001") -> Paper:
    return Paper(
        id=pid,
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="user-1", username="demo")
    return TestClient(app)


def test_save_publishes_event(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(saved_router, "get_paper", lambda *a: _paper())
    monkeypatch.setattr(saved_router, "save_paper", lambda *a: True)
    monkeypatch.setattr(saved_router, "publish_paper_saved", lambda *a: published.append(a))
    response = _client().post("/v1/papers/arxiv:2401.00001/save")
    assert response.status_code == 200
    assert response.json() == {"saved": True}
    assert published == [("user-1", "arxiv:2401.00001", True)]


def test_resave_is_idempotent_and_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[object] = []
    monkeypatch.setattr(saved_router, "get_paper", lambda *a: _paper())
    monkeypatch.setattr(saved_router, "save_paper", lambda *a: False)
    monkeypatch.setattr(saved_router, "publish_paper_saved", lambda *a: published.append(a))
    assert _client().post("/v1/papers/arxiv:2401.00001/save").status_code == 200
    assert published == []


def test_save_unknown_paper_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "get_paper", lambda *a: None)
    assert _client().post("/v1/papers/arxiv:0000.00000/save").status_code == 404


def test_unsave_publishes_event(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(saved_router, "unsave_paper", lambda *a: True)
    monkeypatch.setattr(saved_router, "publish_paper_saved", lambda *a: published.append(a))
    response = _client().delete("/v1/papers/arxiv:2401.00001/save")
    assert response.status_code == 200
    assert response.json() == {"saved": False}
    assert published == [("user-1", "arxiv:2401.00001", False)]


def test_my_saved_lists_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "list_saved", lambda *a: [_paper()])
    response = _client().get("/v1/me/saved")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()["items"]] == ["arxiv:2401.00001"]


def test_saved_requires_auth() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    assert client.post("/v1/papers/arxiv:2401.00001/save").status_code == 401
    assert client.get("/v1/me/saved").status_code == 401
