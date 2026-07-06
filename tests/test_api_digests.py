from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.digests as digests_router
from researchscout.api.deps import get_session
from researchscout.api.main import create_app


class FakeDigestRow:
    slug = "2026-w28"
    title = "Research radar, week 28 2026"
    period_start = datetime(2026, 6, 29, tzinfo=UTC)
    period_end = datetime(2026, 7, 6, tzinfo=UTC)
    body = "Big week [arxiv:2401.00001]."
    items: list[dict[str, Any]] = [
        {"paper_id": "arxiv:2401.00001", "title": "T", "score": 0.9, "citations": 12.0}
    ]


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_digests_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "list_digests", lambda *a, **k: [FakeDigestRow()])
    response = _client().get("/v1/digests")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["slug"] == "2026-w28"
    assert "body" not in items[0]


def test_digest_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "get_digest", lambda *a: FakeDigestRow())
    response = _client().get("/v1/digests/2026-w28")
    assert response.status_code == 200
    body = response.json()
    assert body["body"].startswith("Big week")
    assert body["items"][0]["paper_id"] == "arxiv:2401.00001"


def test_digest_detail_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(digests_router, "get_digest", lambda *a: None)
    assert _client().get("/v1/digests/2026-w01").status_code == 404
