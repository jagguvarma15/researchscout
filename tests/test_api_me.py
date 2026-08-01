from typing import Any

import pytest
from fastapi.testclient import TestClient

import researchscout.api.routers.me as me_router
from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.identity import IdentityDeletionUnavailable
from researchscout.store.models import UserRow

_VERSION = "2026-08-01"


class _Store:
    """Stands in for the users store: the router's logic is what is under test here."""

    def __init__(self, row: UserRow | None = None) -> None:
        self.row = row
        self.accepted: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def get_user(self, session: object, sub: str) -> UserRow | None:
        return self.row

    def upsert_user(self, session: object, sub: str, **claims: Any) -> None:
        if self.row is None:
            self.row = UserRow(sub=sub)

    def accept_terms(self, session: object, sub: str, version: str) -> UserRow:
        self.accepted.append((sub, version))
        row = self.row or UserRow(sub=sub)
        row.tos_version = version
        self.row = row
        return row

    def delete_user(self, session: object, sub: str) -> bool:
        self.deleted.append(sub)
        return True

    def export_user_data(self, session: object, sub: str) -> dict[str, Any]:
        return {"account": {"sub": sub}, "saved_papers": [], "interests": [], "reading_events": []}


class _FakeSession:
    """Just enough session for routes that flush after mutating a row."""

    def flush(self) -> None:
        return None


def _client(
    monkeypatch: pytest.MonkeyPatch, store: _Store, *, signed_in: bool = False
) -> TestClient:
    monkeypatch.setenv("RS_TERMS_VERSION", _VERSION)
    for name in ("get_user", "upsert_user", "accept_terms", "delete_user", "export_user_data"):
        monkeypatch.setattr(me_router, name, getattr(store, name))
    app = create_app()
    app.dependency_overrides[get_session] = _FakeSession
    if signed_in:
        # Token validation has its own tests; here we only care what the route does with an
        # identity it already trusts.
        app.dependency_overrides[require_user] = lambda: User(sub="auth0|abc", username="abc")
    return TestClient(app)


def test_me_reports_terms_outstanding(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _client(monkeypatch, _Store()).get("/v1/me").json()
    assert body["sub"] == "local"
    assert body["terms_required"] == _VERSION
    assert body["terms_accepted_version"] is None
    assert body["terms_accepted"] is False


def test_me_reports_terms_settled(monkeypatch: pytest.MonkeyPatch) -> None:
    row = UserRow(sub="local", email="a@example.com", display_name="Ada", tos_version=_VERSION)
    body = _client(monkeypatch, _Store(row)).get("/v1/me").json()
    assert (body["email"], body["display_name"]) == ("a@example.com", "Ada")
    assert body["terms_accepted"] is True


def test_stale_acceptance_reads_as_outstanding(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terms bump has to bring everyone back through the dialog."""
    row = UserRow(sub="local", tos_version="2020-01-01")
    body = _client(monkeypatch, _Store(row)).get("/v1/me").json()
    assert body["terms_accepted"] is False


def test_accepting_the_current_version_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    response = _client(monkeypatch, store).post("/v1/me/terms", json={"version": _VERSION})
    assert response.status_code == 200
    assert response.json()["terms_accepted"] is True
    assert store.accepted == [("local", _VERSION)]


def test_accepting_a_different_version_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    response = _client(monkeypatch, store).post("/v1/me/terms", json={"version": "2020-01-01"})
    assert response.status_code == 409
    assert store.accepted == []


def test_display_name_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    body = _client(monkeypatch, store).patch("/v1/me", json={"display_name": "  Ada  "}).json()
    assert body["display_name"] == "Ada"


def test_export_returns_the_stored_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _client(monkeypatch, _Store()).get("/v1/me/export").json()
    assert set(body) == {"account", "saved_papers", "interests", "reading_events"}


def test_delete_refuses_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no account to delete without an issuer, and the built-in user must survive."""
    store = _Store()
    monkeypatch.setenv("RS_OIDC_ISSUER", "")
    assert _client(monkeypatch, store).delete("/v1/me").status_code == 403
    assert store.deleted == []


def test_delete_refuses_when_the_provider_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half a deletion - rows gone, login left behind - would make the privacy page false."""
    store = _Store()
    monkeypatch.setenv("RS_OIDC_ISSUER", "https://example.auth0.com/")
    monkeypatch.setattr(me_router, "identity_deletion_configured", lambda: False)
    assert _client(monkeypatch, store, signed_in=True).delete("/v1/me").status_code == 503
    assert store.deleted == []


def test_delete_refuses_when_the_provider_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    monkeypatch.setenv("RS_OIDC_ISSUER", "https://example.auth0.com/")
    monkeypatch.setattr(me_router, "identity_deletion_configured", lambda: True)

    def boom(sub: str) -> None:
        raise IdentityDeletionUnavailable("provider returned 500")

    monkeypatch.setattr(me_router, "delete_identity", boom)
    assert _client(monkeypatch, store, signed_in=True).delete("/v1/me").status_code == 502
    assert store.deleted == []


def test_delete_removes_the_login_before_the_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    order: list[str] = []
    monkeypatch.setenv("RS_OIDC_ISSUER", "https://example.auth0.com/")
    monkeypatch.setattr(me_router, "identity_deletion_configured", lambda: True)
    client = _client(monkeypatch, store, signed_in=True)

    def delete_rows(session: object, sub: str) -> bool:
        order.append("rows")
        return store.delete_user(session, sub)

    # After _client, which installs the store's own functions.
    monkeypatch.setattr(me_router, "delete_identity", lambda sub: order.append("identity"))
    monkeypatch.setattr(me_router, "delete_user", delete_rows)
    response = client.delete("/v1/me")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert order == ["identity", "rows"]
    assert store.deleted == ["auth0|abc"]
