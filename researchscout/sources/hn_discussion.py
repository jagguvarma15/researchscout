"""Hacker News discussion signals via the Algolia search API (keyless, unauthenticated).

Each fetch is a snapshot over the ``since`` window: stories linking arXiv papers are found with
``search_by_date`` and aggregated per paper, then two observations attach to papers already in
the store — ``social_mention`` (summed story points, community approval) and ``discussion``
(summed comment counts, contention). Run periodically, re-observing the same stories as their
points grow is what turns the series into velocity; the window bounds how far back a story
still counts.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType, normalize_arxiv_id
from researchscout.sources.base import HealthStatus, RawItem, Source, register
from researchscout.useragent import default_headers

_HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
_REQUEST_TIMEOUT = 30.0
_PAGE_SIZE = 100
_MAX_PAGES = 10

# arXiv ids in any common link shape (abs/pdf/html, optional version suffix): new-style
# YYMM.NNNNN and old-style archive[.subject]/YYMMNNN - math/0309136 keeps resurfacing on
# HN, and subject classes range from math.GT to cond-mat.str-el.
_ARXIV_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)


@register
class HackerNewsDiscussionSource(Source):
    name = "hn_discussion"
    kind = "signal"

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

    def _stories(self, since: datetime) -> list[dict[str, Any]]:
        since_epoch = int(since.timestamp())
        hits: list[dict[str, Any]] = []
        for page in range(_MAX_PAGES):
            resp = httpx.get(
                _HN_SEARCH,
                params={
                    "query": "arxiv.org",
                    "tags": "story",
                    "numericFilters": f"created_at_i>={since_epoch}",
                    "hitsPerPage": _PAGE_SIZE,
                    "page": page,
                },
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            payload = resp.json()
            page_hits = payload.get("hits") or []
            hits.extend(page_hits)
            if not page_hits or page + 1 >= int(payload.get("nbPages") or 0):
                break
        return hits

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        fetched_at = datetime.now(UTC)
        per_paper: dict[str, dict[str, Any]] = {}
        for hit in self._stories(since):
            haystack = " ".join(str(hit.get(key) or "") for key in ("url", "title", "story_text"))
            match = _ARXIV_RE.search(haystack)
            if not match:
                continue
            arxiv_id = normalize_arxiv_id(match.group(1))
            entry = per_paper.setdefault(arxiv_id, {"points": 0, "comments": 0, "stories": []})
            entry["points"] += int(hit.get("points") or 0)
            entry["comments"] += int(hit.get("num_comments") or 0)
            entry["stories"].append(str(hit.get("objectID") or ""))

        stored = self._match_stored(list(per_paper))
        items: list[RawItem] = []
        for arxiv_id, entry in per_paper.items():
            paper_id = stored.get(arxiv_id)
            if paper_id is None:
                continue
            for metric, value in (("points", entry["points"]), ("comments", entry["comments"])):
                items.append(
                    RawItem(
                        source=self.name,
                        fetched_at=fetched_at,
                        payload={
                            "paper_id": paper_id,
                            "metric": metric,
                            "value": value,
                            "stories": entry["stories"],
                        },
                    )
                )
        return items, None

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        signal_type = (
            SignalType.social_mention if payload["metric"] == "points" else SignalType.discussion
        )
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=signal_type,
            source=self.name,
            value=float(payload["value"]),
            metadata={"stories": payload.get("stories")},
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(
                _HN_SEARCH,
                params={"query": "arxiv.org", "tags": "story", "hitsPerPage": 1},
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
