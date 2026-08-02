"""Finding the issuer's signing keys.

This used to guess Keycloak's path (`/protocol/openid-connect/certs`). Against any other
provider that 404s, every token is rejected, and the 401 says nothing about keys - so the
failure looks like a bad token rather than a misconfiguration. Discovery is the standard
answer and works for all of them.
"""

from typing import Any

import httpx
import pytest

import researchscout.api.auth as auth_mod

_ISSUER = "https://example.us.auth0.com/"
_JWKS = "https://example.us.auth0.com/.well-known/jwks.json"


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _fresh_client() -> None:
    auth_mod._jwk_client.cache_clear()


def test_the_issuer_is_asked_where_its_keys_are(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        asked.append(url)
        return _Resp({"jwks_uri": _JWKS})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert auth_mod._discover_jwks_url(_ISSUER) == _JWKS
    # One trailing slash, not two: the issuer carries one and the path adds another.
    assert asked == ["https://example.us.auth0.com/.well-known/openid-configuration"]


def test_an_issuer_without_a_jwks_uri_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp({}))
    with pytest.raises(Exception, match="jwks_uri"):
        auth_mod._discover_jwks_url(_ISSUER)


def test_an_explicit_url_skips_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured directly, nothing should go asking."""

    def refuse(url: str, **kwargs: Any) -> _Resp:  # pragma: no cover - must not be called
        raise AssertionError("discovery ran despite an explicit jwks url")

    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setenv("RS_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("RS_OIDC_JWKS_URL", _JWKS)
    assert auth_mod._jwk_client().uri == _JWKS


def test_discovery_feeds_the_key_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp({"jwks_uri": _JWKS}))
    monkeypatch.setenv("RS_OIDC_ISSUER", _ISSUER)
    monkeypatch.delenv("RS_OIDC_JWKS_URL", raising=False)
    assert auth_mod._jwk_client().uri == _JWKS
