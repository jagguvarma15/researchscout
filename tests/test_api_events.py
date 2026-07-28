import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.events as events_router
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.main import create_app
from researchscout.store.events import EventInput


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="tester", username="tester")
    return TestClient(app)


def test_events_batch_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_append(session: object, user_sub: str, events: list[EventInput]) -> int:
        seen["user_sub"] = user_sub
        seen["events"] = events
        return len(events)

    monkeypatch.setattr(events_router, "append_events", fake_append)
    response = _client().post(
        "/v1/events",
        json={
            "events": [
                {
                    "event": "impression",
                    "paper_id": "arxiv:2401.00001",
                    "rank": 3,
                    "surface": "feed",
                },
                {
                    "event": "dwell",
                    "paper_id": "arxiv:2401.00001",
                    "value": 25000,
                    "surface": "detail",
                },
            ]
        },
    )
    assert response.status_code == 202
    assert response.json() == {"stored": 2}
    assert seen["user_sub"] == "tester"
    assert seen["events"][0] == EventInput(  # type: ignore[index]
        event="impression", paper_id="arxiv:2401.00001", rank=3, surface="feed"
    )


def test_events_reject_unknown_kind() -> None:
    body = {"events": [{"event": "hover", "paper_id": "arxiv:2401.00001"}]}
    assert _client().post("/v1/events", json=body).status_code == 422


def test_events_reject_empty_batch() -> None:
    assert _client().post("/v1/events", json={"events": []}).status_code == 422
