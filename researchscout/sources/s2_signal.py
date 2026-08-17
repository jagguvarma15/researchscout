"""Semantic Scholar citation signals — the first signal source.

Matches papers by their arXiv id (a clean external-id lookup, no fuzzy matching), asks Semantic
Scholar for citation counts, and emits Signal observations. Citations lag by months, so this is a
baseline signal; the faster proxies — HF trending and GitHub code stars — carry the real ignition
signal, with social sources still deferred.

Counts come from the batch endpoint, 500 papers per request, so the whole corpus is a handful of
calls rather than one per paper — which is the only shape the unauthenticated shared pool actually
permits. The pool can still throttle: retries honor Retry-After, and a call that stays throttled
raises so the pipeline stops there and keeps what earlier pages already stored. An ``S2_API_KEY``
moves requests off the shared pool entirely.

The batch response is matched back to the requested papers by each entry's arXiv external id,
never by position: papers unknown to Semantic Scholar have come back both as null entries and as
nothing at all — a dropped entry shifts every later position, which would silently attach counts
to the wrong papers.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType
from researchscout.sources.base import (
    HealthStatus,
    RawItem,
    Source,
    register,
    retry_wait,
    source_config,
)
from researchscout.useragent import default_headers

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = "citationCount,influentialCitationCount,externalIds"
_REQUEST_TIMEOUT = 30.0
_RETRY_MAX = 2
_RETRY_WAIT_CAP = 60.0


@register
class SemanticScholarSource(Source):
    name = "semantic_scholar"
    kind = "signal"

    def __init__(
        self,
        api_key: str | None = None,
        page_size: int = 500,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        cfg = source_config(self.name)
        self._api_key: str | None = api_key or cfg.get("api_key") or os.environ.get("S2_API_KEY")
        # 500 is the batch endpoint's documented per-call ceiling.
        self.page_size = page_size
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return default_headers({"x-api-key": self._api_key} if self._api_key else None)

    def _target_papers(self, offset: int, limit: int) -> list[tuple[str, str]]:
        """(canonical_id, arxiv_id) for stored papers with an arXiv id, newest first.

        Newest first so a partial run refreshes the papers being ranked today; the tail of the
        corpus can wait for the next slot.
        """
        from researchscout.store.db import session_scope
        from researchscout.store.models import ExternalIdRow, PaperRow

        with session_scope() as session:
            rows = session.execute(
                select(ExternalIdRow.paper_id, ExternalIdRow.value)
                .join(PaperRow, PaperRow.id == ExternalIdRow.paper_id)
                .where(ExternalIdRow.scheme == "arxiv")
                .order_by(PaperRow.published_at.desc(), ExternalIdRow.paper_id)
                .offset(offset)
                .limit(limit)
            ).all()
        return [(paper_id, arxiv_id) for paper_id, arxiv_id in rows]

    def _fetch_batch(self, arxiv_ids: list[str]) -> list[dict[str, Any] | None]:
        """One POST for the whole page; entries carry their arXiv id for matching.

        A paper unknown to Semantic Scholar is a null entry or simply absent — never a 404 —
        so neither the length nor the order of the response is trusted. The call as a whole is
        what gets rate limited, so the retries live here, and a still-throttled pool raises
        for the pipeline to treat as stop-here-keep-progress.
        """
        for attempt in range(_RETRY_MAX + 1):
            resp = httpx.post(
                f"{_S2_BASE}/paper/batch",
                params={"fields": _FIELDS},
                json={"ids": [f"arXiv:{arxiv_id}" for arxiv_id in arxiv_ids]},
                headers=self._headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            if resp.status_code != 429 or attempt == _RETRY_MAX:
                resp.raise_for_status()
                data: list[dict[str, Any] | None] = resp.json()
                return data
            self._sleep(retry_wait(resp.headers.get("Retry-After"), attempt, cap=_RETRY_WAIT_CAP))
        raise AssertionError("unreachable")

    def _match_batch(
        self, targets: list[tuple[str, str]]
    ) -> list[tuple[str, dict[str, Any]]]:
        """One batch call, matched back to (paper_id, entry) pairs by arXiv external id."""
        entries = self._fetch_batch([arxiv_id for _, arxiv_id in targets])
        by_arxiv: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if entry is None:
                continue
            arxiv_id = (entry.get("externalIds") or {}).get("ArXiv")
            if arxiv_id:
                by_arxiv[arxiv_id] = entry
        matched: list[tuple[str, dict[str, Any]]] = []
        for paper_id, arxiv_id in targets:
            entry = by_arxiv.get(arxiv_id)
            if entry is not None:
                matched.append((paper_id, entry))
        return matched

    def citations_for(self, pairs: list[tuple[str, str]]) -> list[Signal]:
        """Citation signals for explicit (paper_id, arxiv_id) pairs — the walker's entry point.

        Papers Semantic Scholar does not know are simply absent from the result; the caller
        decides what that observation means. Raises ``httpx.HTTPError`` when the pool stays
        throttled, for the caller to treat as stop-here-keep-progress.
        """
        fetched_at = datetime.now(UTC)
        return [
            self.normalize(
                RawItem(
                    source=self.name, fetched_at=fetched_at, payload={"paper_id": paper_id, **entry}
                )
            )
            for paper_id, entry in self._match_batch(pairs)
        ]

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        """Offset-paged walk over the stored corpus — the manual ``scout ingest`` path.

        The scheduler drives the watermark walker (``ingest/citations.py``) instead, which
        calls ``citations_for`` with its own targets.
        """
        offset = int(cursor) if cursor else 0
        targets = self._target_papers(offset, self.page_size)
        if not targets:
            return [], None
        fetched_at = datetime.now(UTC)
        items = [
            RawItem(
                source=self.name, fetched_at=fetched_at, payload={"paper_id": paper_id, **entry}
            )
            for paper_id, entry in self._match_batch(targets)
        ]
        next_cursor = str(offset + self.page_size) if len(targets) == self.page_size else None
        return items, next_cursor

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(
                f"{_S2_BASE}/paper/arXiv:2301.00001",
                params={"fields": "citationCount"},
                headers=self._headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=SignalType.citation,
            source=self.name,
            value=float(payload.get("citationCount") or 0),
            metadata={"influential": payload.get("influentialCitationCount")},
            observed_at=raw.fetched_at,
        )
