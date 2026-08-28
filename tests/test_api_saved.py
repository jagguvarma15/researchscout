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


def test_save_returns_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "get_paper", lambda *a: _paper())
    monkeypatch.setattr(saved_router, "save_paper", lambda *a: True)
    response = _client().post("/v1/papers/arxiv:2401.00001/save")
    assert response.status_code == 200
    assert response.json() == {"saved": True}


def test_resave_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "get_paper", lambda *a: _paper())
    monkeypatch.setattr(saved_router, "save_paper", lambda *a: False)
    response = _client().post("/v1/papers/arxiv:2401.00001/save")
    assert response.status_code == 200
    assert response.json() == {"saved": True}


def test_save_unknown_paper_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "get_paper", lambda *a: None)
    assert _client().post("/v1/papers/arxiv:0000.00000/save").status_code == 404


def test_unsave_returns_unsaved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "unsave_paper", lambda *a: True)
    response = _client().delete("/v1/papers/arxiv:2401.00001/save")
    assert response.status_code == 200
    assert response.json() == {"saved": False}


def _entry(pid: str = "arxiv:2401.00001") -> object:
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from researchscout.store.saved import SavedEntry

    return SavedEntry(
        paper=_paper(pid),
        status="reading",
        tags=["rl"],
        note="half read",
        saved_at=_dt(2026, 8, 1, tzinfo=_UTC),
    )


def test_my_saved_lists_the_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "list_saved", lambda *a, **k: [_entry()])
    monkeypatch.setattr(saved_router, "saved_tags", lambda *a: ["rl"])
    response = _client().get("/v1/me/saved")
    assert response.status_code == 200
    body = response.json()
    assert [p["id"] for p in body["items"]] == ["arxiv:2401.00001"]
    assert body["items"][0]["status"] == "reading"
    assert body["items"][0]["tags"] == ["rl"]
    assert body["items"][0]["note"] == "half read"
    assert body["tags"] == ["rl"]


def test_patch_applies_only_provided_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_update(session: object, sub: str, pid: str, changes: dict) -> bool:
        seen.update(changes)
        return True

    monkeypatch.setattr(saved_router, "update_saved", fake_update)
    response = _client().patch(
        "/v1/papers/arxiv:2401.00001/save", json={"status": "done", "note": None}
    )
    assert response.status_code == 200
    # note was carried explicitly (a clear); tags never was, so it must not appear.
    assert seen == {"status": "done", "note": None}


def test_patch_unsaved_paper_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "update_saved", lambda *a, **k: False)
    response = _client().patch("/v1/papers/arxiv:2401.00001/save", json={"status": "done"})
    assert response.status_code == 404


def test_export_bibtex_and_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saved_router, "list_saved", lambda *a, **k: [_entry()])
    client = _client()
    bib = client.get("/v1/me/saved/export")
    assert bib.status_code == 200
    assert "@article{arxiv-2401-00001," in bib.text
    assert "attachment" in bib.headers["content-disposition"]
    sheet = client.get("/v1/me/saved/export", params={"format": "csv"})
    assert sheet.status_code == 200
    assert sheet.text.splitlines()[0].startswith("id,title,authors")
    assert "arxiv:2401.00001" in sheet.text


def test_saved_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_OIDC_ISSUER", "http://localhost:8080/realms/researchscout")
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    assert client.post("/v1/papers/arxiv:2401.00001/save").status_code == 401
    assert client.get("/v1/me/saved").status_code == 401
