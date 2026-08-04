"""The per-account state routes: they need an account, and they hand back the new state.

The store is exercised properly in test_store_account.py against a real database; these pin
the HTTP contract - the shapes, the status codes, and the fact that none of it is open to a
signed-out visitor, because "what did you search for" is not a public question.
"""

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.account as account_router
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.main import create_app


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[require_user] = lambda: User(sub="user-1", username="demo")
    return TestClient(app)


def _public_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The API as the public deployment runs it: an issuer set, so a token is required.

    Without an issuer the API is in local no-auth mode and every caller is the built-in user,
    which would make an unauthenticated test pass for the wrong reason.
    """
    monkeypatch.setenv("RS_OIDC_ISSUER", "https://example.us.auth0.com/")
    monkeypatch.setenv("RS_OIDC_AUDIENCE", "api")
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def _stub(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    for name, value in overrides.items():
        monkeypatch.setattr(account_router.account, name, value)


def test_history_reads_back_the_stored_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, recent_searches=lambda *a, **k: ["attention", "diffusion"])
    response = _client().get("/v1/me/history")
    assert response.status_code == 200
    assert response.json() == {"items": ["attention", "diffusion"]}


def test_recording_a_search_returns_the_new_history(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    _stub(
        monkeypatch,
        record_search=lambda session, sub, query: seen.append(query),
        recent_searches=lambda *a, **k: seen[::-1],
    )
    response = _client().post("/v1/me/history", json={"query": "mixture of experts"})
    assert response.status_code == 202
    assert response.json() == {"items": ["mixture of experts"]}
    assert seen == ["mixture of experts"]


def test_an_empty_search_is_refused_by_the_schema() -> None:
    assert _client().post("/v1/me/history", json={"query": ""}).status_code == 422


def test_clearing_history_answers_with_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared: list[str] = []
    _stub(monkeypatch, clear_searches=lambda session, sub: cleared.append(sub) or 0)
    response = _client().delete("/v1/me/history")
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert cleared == ["user-1"]


def test_dismissing_returns_the_dismissed_set(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[str] = []
    _stub(
        monkeypatch,
        record_dismissal=lambda session, sub, paper_id: stored.append(paper_id),
        dismissed_papers=lambda *a, **k: stored,
    )
    response = _client().post("/v1/me/dismissals", json={"paper_id": "arxiv:2401.00001"})
    assert response.status_code == 202
    assert response.json() == {"items": ["arxiv:2401.00001"]}


def test_restoring_one_dismissal_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def restore(session: object, sub: str, paper_ids: list[str] | None = None) -> int:
        seen["ids"] = paper_ids
        return 1

    _stub(monkeypatch, restore_dismissed=restore, dismissed_papers=lambda *a, **k: [])
    response = _client().delete("/v1/me/dismissals", params={"paper_id": "arxiv:2401.00001"})
    assert response.status_code == 200
    assert seen["ids"] == ["arxiv:2401.00001"]


def test_restoring_all_dismissals_names_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def restore(session: object, sub: str, paper_ids: list[str] | None = None) -> int:
        seen["ids"] = paper_ids
        return 3

    _stub(monkeypatch, restore_dismissed=restore, dismissed_papers=lambda *a, **k: [])
    assert _client().delete("/v1/me/dismissals").status_code == 200
    assert seen["ids"] is None


def test_recent_papers_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[str] = []
    _stub(
        monkeypatch,
        record_view=lambda session, sub, paper_id: stored.append(paper_id),
        recent_papers=lambda *a, **k: stored,
    )
    response = _client().post("/v1/me/recent", json={"paper_id": "arxiv:2401.00002"})
    assert response.status_code == 202
    assert response.json() == {"items": ["arxiv:2401.00002"]}


def test_filters_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}
    _stub(
        monkeypatch,
        save_filters=lambda session, sub, qs: stored.__setitem__(sub, qs),
        saved_filters=lambda session, sub: stored.get(sub),
    )
    response = _client().put("/v1/me/filters", json={"query_string": "subject=ai&days=7"})
    assert response.status_code == 200
    assert response.json() == {"query_string": "subject=ai&days=7"}


def test_no_filters_saved_reads_as_null(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, saved_filters=lambda session, sub: None)
    assert _client().get("/v1/me/filters").json() == {"query_string": None}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/me/history", None),
        ("POST", "/v1/me/history", {"query": "attention"}),
        ("DELETE", "/v1/me/history", None),
        ("GET", "/v1/me/recent", None),
        ("POST", "/v1/me/recent", {"paper_id": "arxiv:2401.00001"}),
        ("GET", "/v1/me/dismissals", None),
        ("POST", "/v1/me/dismissals", {"paper_id": "arxiv:2401.00001"}),
        ("DELETE", "/v1/me/dismissals", None),
        ("GET", "/v1/me/filters", None),
        ("PUT", "/v1/me/filters", {"query_string": "subject=ai"}),
    ],
)
def test_none_of_it_is_open_to_a_signed_out_visitor(
    method: str, path: str, body: dict[str, str] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "What did you search for" is not a public question, and neither is the rest of it.
    response = _public_client(monkeypatch).request(method, path, json=body)
    assert response.status_code == 401
