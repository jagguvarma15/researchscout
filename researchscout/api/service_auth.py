"""Who is allowed to reach the API at all, before any question of who the user is.

Once the API has a public hostname, anything that learns it can call it - including the routes
that are deliberately open to signed-out visitors. This is the front door: one shared secret
that only the site's own server knows, checked before routing. The frontend proxies every
browser request server-side, so no token ever reaches a browser.

Unset (the default) leaves the door open, which is what a local install wants.

Two things ride along with a request that clears the door:

- ``request.state.trusted_proxy``, which is what lets the rate limiter believe a forwarded
  client address. Without it an attacker could set the header and get a fresh bucket per
  request.
- Nothing else. This is not authentication: it says the request came from the site, not who is
  making it. Accounts are still the token in the Authorization header.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from secrets import compare_digest

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from researchscout.config import get_settings

SERVICE_TOKEN_HEADER = "x-rs-service-token"
CLIENT_IP_HEADER = "x-rs-client-ip"

# The liveness probe stays open: the container healthcheck calls it from inside, it reveals
# nothing, and a probe that needs a secret is a probe that fails for the wrong reasons.
_OPEN_PATHS = frozenset({"/healthz"})


async def service_token_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Reject anything that does not carry the shared token, when one is configured."""
    expected = get_settings().service_token
    # Only a request that proved it came from the site may claim someone else's address.
    # Without a configured token there is nothing to prove, so nothing is trusted and the
    # limiter falls back to the socket address, which is right for a local install.
    request.state.trusted_proxy = False

    if not expected or request.url.path in _OPEN_PATHS:
        return await call_next(request)

    presented = request.headers.get(SERVICE_TOKEN_HEADER, "")
    if not compare_digest(presented, expected):
        # 404 rather than 403: the hostname is public, and there is no reason to confirm to a
        # scanner that an API lives here. The site's own requests never see this.
        return JSONResponse({"detail": "not found"}, status_code=404)

    request.state.trusted_proxy = True
    return await call_next(request)
