"""Caller identity: a built-in local user, or Bearer-token validation against any OIDC issuer.

With ``RS_OIDC_ISSUER`` unset (the default) the API trusts a single built-in local user — the
right posture for a single-person, local-only install. Setting an issuer turns the API into a
stateless OIDC resource server: signatures are checked against the issuer's JWKS (fetched once
and cached by ``PyJWKClient``), so a token is either valid locally or rejected. Identity is the
token's ``sub`` claim — there is no local user table.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import InvalidTokenError, PyJWKClient
from jwt.exceptions import PyJWKClientError

from researchscout.config import get_settings

_BEARER = {"WWW-Authenticate": "Bearer"}


@dataclass
class User:
    sub: str
    username: str


_LOCAL_USER = User(sub="local", username="local")


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    settings = get_settings()
    url = settings.oidc_jwks_url or f"{settings.oidc_issuer}/protocol/openid-connect/certs"
    return PyJWKClient(url, cache_keys=True)


def require_user(request: Request) -> User:
    """FastAPI dependency: return the caller's identity.

    Local mode (no issuer configured) short-circuits to the built-in user; any Authorization
    header is ignored. With an issuer, the Bearer token is validated as before.
    """
    settings = get_settings()
    if not settings.oidc_issuer:
        return _LOCAL_USER

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token", headers=_BEARER)

    try:
        key = _jwk_client().get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
        )
    except (InvalidTokenError, PyJWKClientError) as exc:
        raise HTTPException(status_code=401, detail="invalid token", headers=_BEARER) from exc
    return User(sub=claims["sub"], username=claims.get("preferred_username", claims["sub"]))
