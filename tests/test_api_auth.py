from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import researchscout.api.auth as auth_mod
from researchscout.answer import Answer
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.main import create_app

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_ISSUER = "http://localhost:8080/realms/researchscout"


class _FakeSigningKey:
    key = _KEY.public_key()


class _FakeJWKClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey()


def _token(**overrides: Any) -> str:
    claims: dict[str, Any] = {
        "sub": "user-1",
        "preferred_username": "demo",
        "iss": _ISSUER,
        "aud": "api",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, _KEY, algorithm="RS256")


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RS_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("RS_OIDC_AUDIENCE", "api")
    monkeypatch.setattr(auth_mod, "_jwk_client", lambda: _FakeJWKClient())
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    return TestClient(app)


def _ask(client: TestClient, token: str | None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/v1/ask", json={"question": "q"}, headers=headers).status_code


def test_missing_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ask(_client(monkeypatch), None) == 401


def test_valid_token_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import researchscout.api.routers.ask as ask_router

    empty = Answer(text="No recent papers match this question.", cited=[], hallucinated=[], used=[])
    monkeypatch.setattr(ask_router, "answer", lambda *a, **k: empty)
    assert _ask(_client(monkeypatch), _token()) == 200


def test_wrong_audience_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ask(_client(monkeypatch), _token(aud="somewhere-else")) == 401


def test_wrong_issuer_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ask(_client(monkeypatch), _token(iss="http://evil.example/realms/researchscout")) == 401


def test_expired_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _token(exp=datetime.now(UTC) - timedelta(minutes=1))
    assert _ask(_client(monkeypatch), expired) == 401


def test_papers_stay_public(monkeypatch: pytest.MonkeyPatch) -> None:
    import researchscout.api.routers.papers as papers_router

    monkeypatch.setattr(papers_router, "list_papers", lambda *a, **k: [])
    assert _client(monkeypatch).get("/v1/papers").status_code == 200
