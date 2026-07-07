import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.profile as profile_router
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.main import create_app


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="user-1", username="demo")
    return TestClient(app)


def test_interests_require_auth() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    assert client.get("/v1/me/interests").status_code == 401
    assert client.put("/v1/me/interests", json={"interests": ["agents"]}).status_code == 401


def test_my_interests_lists_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_router, "get_interests", lambda *a: ["agents", "rlhf"])
    response = _client().get("/v1/me/interests")
    assert response.status_code == 200
    assert response.json() == {"interests": ["agents", "rlhf"]}


def test_update_interests_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[tuple[object, ...]] = []

    def fake_set(*args: object) -> list[str]:
        stored.append(args)
        return ["agents", "rlhf"]

    monkeypatch.setattr(profile_router, "set_interests", fake_set)
    response = _client().put("/v1/me/interests", json={"interests": ["agents", "rlhf"]})
    assert response.status_code == 200
    assert response.json() == {"interests": ["agents", "rlhf"]}
    assert stored == [(None, "user-1", ["agents", "rlhf"])]


def test_overlong_interest_is_422() -> None:
    response = _client().put("/v1/me/interests", json={"interests": ["x" * 50]})
    assert response.status_code == 422


def test_too_many_interests_is_422() -> None:
    interests = [f"topic-{i}" for i in range(25)]
    response = _client().put("/v1/me/interests", json={"interests": interests})
    assert response.status_code == 422
