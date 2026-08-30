"""Caller identity: a built-in local user, or Bearer-token validation against any OIDC issuer.

With ``RS_OIDC_ISSUER`` unset (the default) the API trusts a single built-in local user — the
right posture for a single-person, local-only install. Setting an issuer turns the API into a
stateless OIDC resource server: signatures are checked against the issuer's JWKS (fetched once
and cached by ``PyJWKClient``), so a token is either valid locally or rejected. Identity is the
token's ``sub`` claim.

Validation stays stateless; the only database touch is keeping an account row alive for a
validated sub (migration 0019), which is what account settings, terms acceptance and deletion
hang off. ``optional_user`` is the same check without the 401, for routes a signed-out visitor
may still use.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from jwt import InvalidTokenError, PyJWKClient
from jwt.exceptions import PyJWKClientError
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.config import get_settings
from researchscout.store.users import upsert_user
from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

_BEARER = {"WWW-Authenticate": "Bearer"}
_DISCOVERY_TIMEOUT = 10.0


# Frozen because the local-mode instance below is returned by reference to every request;
# a mutable identity shared across concurrent requests would corrupt all of them at once.
@dataclass(frozen=True)
class User:
    sub: str
    username: str


_LOCAL_USER = User(sub="local", username="local")


def owner_tag(sub: str | None) -> str | None:
    """A short pseudonymous tag for an account, or None for anonymous callers.

    Byte-for-byte the web's ``lib/owner.ts`` derivation - twelve characters of the
    base64url sha256 of the sub - so a metrics row and a client-side envelope stamped for
    the same account carry the same tag, while neither ever holds the sub itself. Twelve
    characters answer "same account or not" and nothing more.
    """
    if not sub:
        return None
    digest = hashlib.sha256(sub.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")[:12]


def _discover_jwks_url(issuer: str) -> str:
    """Ask the issuer where its signing keys are, the way OIDC says to.

    This used to guess Keycloak's path, which is wrong for every other provider - against
    Auth0 it 404s and every token is rejected as invalid, with nothing in the response to
    suggest the keys were the problem. The discovery document is the one place that is
    correct for all of them.
    """
    resp = httpx.get(
        f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        headers=default_headers(),
        timeout=_DISCOVERY_TIMEOUT,
    )
    resp.raise_for_status()
    jwks_uri = resp.json().get("jwks_uri")
    if not jwks_uri:
        raise PyJWKClientError(f"{issuer} published no jwks_uri")
    return str(jwks_uri)


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    settings = get_settings()
    url = settings.oidc_jwks_url or _discover_jwks_url(settings.oidc_issuer)
    return PyJWKClient(url, cache_keys=True)


def require_user(
    request: Request,
    session: Annotated[Session | None, Depends(get_session)] = None,
) -> User:
    """FastAPI dependency: return the caller's identity, 401 when there is none.

    Local mode (no issuer configured) short-circuits to the built-in user, whose account row
    ships with migration 0019; any Authorization header is ignored. With an issuer, the Bearer
    token is validated and the account row is kept current.

    ``session`` defaults to None so the function stays callable directly (and so a test that
    stubs the session dependency simply skips the bookkeeping).
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
        # The 401 body stays deliberately vague - a caller holding a bad token learns nothing
        # useful from a detailed reason. The operator needs it though: audience mismatch,
        # wrong signing algorithm and an expired token are three very different problems that
        # otherwise look identical from the outside.
        logger.warning("rejected a bearer token: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=401, detail="invalid token", headers=_BEARER) from exc

    user = User(sub=claims["sub"], username=claims.get("preferred_username", claims["sub"]))
    if session is not None:
        upsert_user(
            session,
            user.sub,
            email=claims.get("email"),
            display_name=claims.get("name") or claims.get("nickname"),
        )
    return user


def optional_user(
    request: Request,
    session: Annotated[Session | None, Depends(get_session)] = None,
) -> User | None:
    """The caller's identity, or None when they are signed out.

    For routes a visitor may use without an account. A malformed or expired token is still a
    401: quietly downgrading a broken session to anonymous would hide the real problem.
    """
    if not get_settings().oidc_issuer:
        return _LOCAL_USER
    if not request.headers.get("authorization"):
        return None
    return require_user(request, session)
