"""Deleting the login itself, not just the rows it owns.

Erasing an account locally while the identity provider keeps the email, password and profile
would make the promise on the privacy page false. This is the provider side of DELETE /v1/me:
a client-credentials token against the Auth0 Management API, then a delete of that user.

Kept behind config so a local no-auth install never touches the network, and separate from
``api/auth.py`` (which only ever reads tokens) so the write path is easy to find.
"""

from __future__ import annotations

import httpx

from researchscout.config import get_settings
from researchscout.useragent import default_headers

_REQUEST_TIMEOUT = 15.0


class IdentityDeletionUnavailable(RuntimeError):
    """The provider cannot be reached or is not configured, so nothing was deleted."""


def identity_deletion_configured() -> bool:
    """True when the provider credentials needed to delete a login are present."""
    settings = get_settings()
    return bool(
        settings.auth0_domain
        and settings.auth0_mgmt_client_id
        and settings.auth0_mgmt_client_secret
    )


def delete_identity(sub: str) -> None:
    """Delete the provider-side user for ``sub``.

    Raises IdentityDeletionUnavailable when it is not configured or the provider refuses, so
    the caller can fail the whole deletion instead of half-completing it. A user the provider
    has already forgotten (404) counts as deleted.
    """
    settings = get_settings()
    if not identity_deletion_configured():
        raise IdentityDeletionUnavailable("identity provider deletion is not configured")

    base = f"https://{settings.auth0_domain}"
    try:
        token_response = httpx.post(
            f"{base}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": settings.auth0_mgmt_client_id,
                "client_secret": settings.auth0_mgmt_client_secret,
                "audience": f"{base}/api/v2/",
            },
            headers=default_headers(),
            timeout=_REQUEST_TIMEOUT,
        )
        token_response.raise_for_status()
        access_token = str(token_response.json()["access_token"])

        response = httpx.delete(
            f"{base}/api/v2/users/{sub}",
            headers=default_headers({"Authorization": f"Bearer {access_token}"}),
            timeout=_REQUEST_TIMEOUT,
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise IdentityDeletionUnavailable(str(exc)) from exc

    if response.status_code == 404:
        return
    if not response.is_success:
        raise IdentityDeletionUnavailable(f"provider returned {response.status_code}")
