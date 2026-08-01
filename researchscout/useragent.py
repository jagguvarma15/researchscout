"""How ResearchScout identifies itself to every upstream API.

One shared User-Agent on every outbound request: an upstream that needs to reach the
operator of a misbehaving client can, and one that only wants to throttle a client can do
that without blocking the whole host. The /about page states that requests are identified,
so every call site through httpx is expected to send these headers.
"""

from __future__ import annotations

from researchscout import __version__

_REPO_URL = "https://github.com/jagguvarma15/researchscout"

USER_AGENT = f"researchscout/{__version__} (+{_REPO_URL})"


def default_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The shared headers, with ``extra`` merged on top (keys there win)."""
    headers = {"User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    return headers
