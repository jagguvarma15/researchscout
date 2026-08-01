"""Bluesky discussion signals via the unauthenticated searchPosts endpoint.

Post engagement around arXiv links is the fastest social read on a paper now that academic
posting has moved here. Each fetch snapshots the ``since`` window with ``q=domain:arxiv.org``
sorted newest-first (the window is cut client-side — the server rejects since/until filters
unauthenticated): the arXiv id comes from the post's external-link embed (a dict lookup, not
text scraping) with the post text as a fallback, engagement is aggregated per paper, and two
observations attach to papers already in the store — ``social_mention`` (likes + reposts +
quotes) and ``discussion`` (reply counts).

Operational facts (probed live 2026-07-28): the search must hit ``api.bsky.app`` — the
documented ``public.api.bsky.app`` host returns 403 for searchPosts; unauthenticated bursts
cap at ~10 requests before a short 403, so pages are paced at ~1 request/second. Aggregator
bots would dominate mention counts and are excluded by handle (configurable in sources.yaml).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType, normalize_arxiv_id
from researchscout.sources.base import HealthStatus, RawItem, Source, register, source_config
from researchscout.useragent import default_headers

_SEARCH = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_REQUEST_TIMEOUT = 30.0
_PAGE_SIZE = 100
_MAX_PAGES = 8
_PAGE_DELAY_SEC = 1.1

_DEFAULT_EXCLUDED = ("arxiv-daily-bot.bsky.social", "ai-firehose.column.social")

# New-style arXiv ids in any common link shape (abs/pdf/html, optional version suffix).
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)


@register
class BlueskySource(Source):
    name = "bluesky"
    kind = "signal"

    def _excluded_accounts(self) -> set[str]:
        configured = source_config(self.name).get("exclude_accounts")
        if isinstance(configured, list):
            return {str(handle) for handle in configured}
        return set(_DEFAULT_EXCLUDED)

    def _match_stored(self, arxiv_ids: list[str]) -> dict[str, str]:
        """Map each known arXiv id to its canonical paper id; unknown ids are dropped."""
        from researchscout.store.db import session_scope
        from researchscout.store.models import ExternalIdRow

        if not arxiv_ids:
            return {}
        with session_scope() as session:
            rows = session.execute(
                select(ExternalIdRow.value, ExternalIdRow.paper_id).where(
                    ExternalIdRow.scheme == "arxiv",
                    ExternalIdRow.value.in_(arxiv_ids),
                )
            ).all()
        return {value: paper_id for value, paper_id in rows}

    @staticmethod
    def _created_at(post: dict[str, Any]) -> datetime | None:
        raw = (post.get("record") or {}).get("createdAt") or post.get("indexedAt")
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _arxiv_id_of(post: dict[str, Any]) -> str | None:
        external = (post.get("embed") or {}).get("external") or {}
        record = post.get("record") or {}
        for text in (external.get("uri"), record.get("text")):
            match = _ARXIV_RE.search(str(text or ""))
            if match:
                return normalize_arxiv_id(match.group(1))
        return None

    def _posts(self, since: datetime) -> list[dict[str, Any]]:
        # The server rejects every since/until filter shape unauthenticated (probed live), so
        # sort=latest streams newest-first and the window is cut client-side: the first post
        # older than ``since`` ends the walk. Naive datetimes (the CLI) are read as UTC.
        window_start = since if since.tzinfo else since.replace(tzinfo=UTC)
        posts: list[dict[str, Any]] = []
        cursor: str | None = None
        for _page in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "q": "domain:arxiv.org",
                "sort": "latest",
                "limit": _PAGE_SIZE,
            }
            if cursor:
                params["cursor"] = cursor
            resp = httpx.get(
                _SEARCH,
                params=params,
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            if resp.status_code in (403, 429) and posts:
                break  # burst cap mid-walk: a partial snapshot beats an error
            resp.raise_for_status()
            payload = resp.json()
            page_posts = payload.get("posts") or []
            exhausted = False
            for post in page_posts:
                created = self._created_at(post)
                if created is not None and created < window_start:
                    exhausted = True
                    break
                posts.append(post)
            cursor = payload.get("cursor")
            if exhausted or not cursor or not page_posts:
                break
            time.sleep(_PAGE_DELAY_SEC)  # unauthenticated burst cap: ~10 requests, then 403
        return posts

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        fetched_at = datetime.now(UTC)
        excluded = self._excluded_accounts()
        per_paper: dict[str, dict[str, Any]] = {}
        for post in self._posts(since):
            handle = str((post.get("author") or {}).get("handle") or "")
            if handle in excluded:
                continue
            arxiv_id = self._arxiv_id_of(post)
            if arxiv_id is None:
                continue
            entry = per_paper.setdefault(arxiv_id, {"engagement": 0, "replies": 0, "posts": 0})
            entry["engagement"] += (
                int(post.get("likeCount") or 0)
                + int(post.get("repostCount") or 0)
                + int(post.get("quoteCount") or 0)
            )
            entry["replies"] += int(post.get("replyCount") or 0)
            entry["posts"] += 1

        stored = self._match_stored(list(per_paper))
        items: list[RawItem] = []
        for arxiv_id, entry in per_paper.items():
            paper_id = stored.get(arxiv_id)
            if paper_id is None:
                continue
            for metric, value in (
                ("engagement", entry["engagement"]),
                ("replies", entry["replies"]),
            ):
                items.append(
                    RawItem(
                        source=self.name,
                        fetched_at=fetched_at,
                        payload={
                            "paper_id": paper_id,
                            "metric": metric,
                            "value": value,
                            "posts": entry["posts"],
                        },
                    )
                )
        return items, None

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        signal_type = (
            SignalType.social_mention
            if payload["metric"] == "engagement"
            else SignalType.discussion
        )
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=signal_type,
            source=self.name,
            value=float(payload["value"]),
            metadata={"posts": payload.get("posts")},
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(
                _SEARCH,
                params={"q": "domain:arxiv.org", "limit": 1},
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
