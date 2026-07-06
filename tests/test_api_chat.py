import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import researchscout.api.routers.chat as chat_router
from researchscout.answer import Answer, StreamDelta, StreamMeta
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.main import create_app
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper


def _scored(pid: str = "arxiv:2401.00001") -> ScoredPaper:
    paper = Paper(
        id=pid,
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )
    return ScoredPaper(paper=paper, score=1.0, distance=0.0)


def _client(monkeypatch: pytest.MonkeyPatch, *, limited: bool = False) -> TestClient:
    def no_limit(key: str, *, limit: int, window_seconds: int) -> None:
        if limited:
            raise HTTPException(status_code=429, headers={"Retry-After": "60"})

    monkeypatch.setattr(chat_router, "check_rate_limit", no_limit)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="tester", username="tester")
    return TestClient(app)


def _events(body: str) -> list[tuple[str, dict]]:
    parsed = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def test_chat_streams_meta_tokens_done(monkeypatch: pytest.MonkeyPatch) -> None:
    used = [_scored()]

    def fake_stream(*a: object, **k: object):
        yield StreamMeta(retrieved=1, used=used)
        yield StreamDelta(text="Hello ")
        yield StreamDelta(text="[arxiv:2401.00001]")
        yield Answer(
            text="Hello [arxiv:2401.00001]", cited=["arxiv:2401.00001"], hallucinated=[], used=used
        )

    monkeypatch.setattr(chat_router, "answer_stream", fake_stream)
    client = _client(monkeypatch)
    response = client.post("/v1/chat", json={"question": "q"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [name for name, _ in events] == ["meta", "token", "token", "done"]
    assert events[0][1] == {"retrieved": 1}
    assert events[-1][1]["cited"] == ["arxiv:2401.00001"]
    assert events[-1][1]["used"][0]["id"] == "arxiv:2401.00001"


def test_chat_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_router, "check_rate_limit", lambda *a, **k: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    client = TestClient(app)
    assert client.post("/v1/chat", json={"question": "q"}).status_code == 401


def test_chat_rate_limited_is_429(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, limited=True)
    response = client.post("/v1/chat", json={"question": "q"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_chat_llm_failure_emits_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from openai import OpenAIError

    def broken_stream(*a: object, **k: object):
        yield StreamMeta(retrieved=1, used=[_scored()])
        raise OpenAIError("connection refused")

    monkeypatch.setattr(chat_router, "answer_stream", broken_stream)
    client = _client(monkeypatch)
    response = client.post("/v1/chat", json={"question": "q"})
    assert response.status_code == 200
    events = _events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == 502
