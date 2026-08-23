from typing import Any

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.topics as topics_router
from researchscout.api.deps import get_session
from researchscout.api.main import create_app


class FakeTopicRow:
    id = 7
    label = "Sparse MoE decoding"
    summary = "Cheap speculative decoding for sparse models."
    score = 12.58
    size = 14
    trend = "rising"
    history: list[dict[str, Any]] = [
        {"built_at": "2026-08-16T00:00:00+00:00", "size": 9},
        {"built_at": "2026-08-23T00:00:00+00:00", "size": 14},
    ]
    papers: list[dict[str, Any]] = [{"paper_id": "arxiv:2608.11402", "title": "T", "score": 0.84}]


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_topics_index_carries_the_size_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(topics_router, "list_topics", lambda *a, **k: [FakeTopicRow()])
    response = _client().get("/v1/topics")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["label"] == "Sparse MoE decoding"
    assert item["trend"] == "rising"
    assert [point["size"] for point in item["history"]] == [9, 14]
    assert item["papers"][0]["paper_id"] == "arxiv:2608.11402"


def test_topic_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(topics_router, "get_topic", lambda *a: FakeTopicRow())
    response = _client().get("/v1/topics/7")
    assert response.status_code == 200
    assert response.json()["history"][-1]["size"] == 14


def test_topic_detail_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(topics_router, "get_topic", lambda *a: None)
    assert _client().get("/v1/topics/999").status_code == 404


def test_topics_history_tolerates_a_null_column(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows written before the history column existed read back as an empty series."""

    class BareRow(FakeTopicRow):
        history = None  # type: ignore[assignment]

    monkeypatch.setattr(topics_router, "list_topics", lambda *a, **k: [BareRow()])
    response = _client().get("/v1/topics")
    assert response.json()["items"][0]["history"] == []
