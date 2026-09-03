from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.digests as digests_router
from researchscout.api.deps import get_session
from researchscout.api.main import create_app


class FakeDigestRow:
    slug = "2026-w28"
    kind = "weekly"
    title = "Research radar, week 28 2026"
    period_start = datetime(2026, 6, 29, tzinfo=UTC)
    period_end = datetime(2026, 7, 6, tzinfo=UTC)
    body = "Big week [arxiv:2401.00001]."
    llm_ok = True
    items: list[dict[str, Any]] = [
        {
            "paper_id": "arxiv:2401.00001",
            "title": "T",
            "score": 0.9,
            "citations": 12.0,
            "primary_category": "cs.LG",
            "keywords": ["attention"],
            "authors": ["X", "Y"],
            "author_count": 5,
            "venue": "NeurIPS 2026",
            "why": {"citation": 1.2},
        }
    ]


class LegacyDigestRow(FakeDigestRow):
    """A pre-0035 payload: only the four original item keys stored."""

    items: list[dict[str, Any]] = [
        {"paper_id": "arxiv:2401.00001", "title": "T", "score": 0.9, "citations": 12.0}
    ]


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_digests_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "list_digests", lambda *a, **k: [FakeDigestRow()])
    monkeypatch.setattr(digests_router, "count_digests", lambda *a, **k: 41)
    response = _client().get("/v1/digests")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 41
    assert payload["limit"] == 20
    assert payload["offset"] == 0
    items = payload["items"]
    assert items[0]["slug"] == "2026-w28"
    assert items[0]["kind"] == "weekly"
    assert items[0]["item_count"] == 1
    assert items[0]["llm_ok"] is True
    assert "body" not in items[0]


def test_digests_index_forwards_the_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_list(session: Any, **kwargs: Any) -> list[FakeDigestRow]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(digests_router, "list_digests", fake_list)
    monkeypatch.setattr(digests_router, "count_digests", lambda session, *, kind: 0)
    response = _client().get("/v1/digests?kind=daily&limit=5&offset=10")
    assert response.status_code == 200
    assert seen == {"kind": "daily", "limit": 5, "offset": 10}
    assert response.json()["limit"] == 5
    assert response.json()["offset"] == 10


def test_digests_index_rejects_unknown_kind() -> None:
    assert _client().get("/v1/digests?kind=hourly").status_code == 422


def test_digest_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "get_digest", lambda *a: FakeDigestRow())
    response = _client().get("/v1/digests/2026-w28")
    assert response.status_code == 200
    body = response.json()
    assert body["body"].startswith("Big week")
    assert body["kind"] == "weekly"
    assert body["llm_ok"] is True
    assert body["item_count"] == 1
    item = body["items"][0]
    assert item["paper_id"] == "arxiv:2401.00001"
    assert item["keywords"] == ["attention"]
    assert item["author_count"] == 5
    assert item["why"] == {"citation": 1.2}


def test_digest_detail_serves_legacy_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "get_digest", lambda *a: LegacyDigestRow())
    response = _client().get("/v1/digests/2026-w28")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["keywords"] == []
    assert item["authors"] == []
    assert item["author_count"] == 0
    assert item["primary_category"] is None
    assert item["venue"] is None
    assert item["why"] == {}


def test_digest_detail_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "get_digest", lambda *a: None)
    assert _client().get("/v1/digests/2026-w01").status_code == 404
