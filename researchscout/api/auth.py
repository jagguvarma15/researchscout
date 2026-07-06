"""Bearer-token validation against Keycloak (stateless OIDC resource server).

The API never talks to Keycloak per request: signatures are checked against its JWKS (fetched
once and cached by ``PyJWKClient``), so a token is either valid locally or rejected. Identity is
the token's ``sub`` claim — there is no local user table.
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


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    settings = get_settings()
    url = settings.oidc_jwks_url or f"{settings.oidc_issuer}/protocol/openid-connect/certs"
    return PyJWKClient(url, cache_keys=True)


def require_user(request: Request) -> User:
    """FastAPI dependency: validate the Bearer token and return the caller's identity."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token", headers=_BEARER)

    settings = get_settings()
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
