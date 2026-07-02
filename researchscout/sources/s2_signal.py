"""Semantic Scholar citation signals — the first signal source.

Matches papers by their arXiv id (a clean external-id lookup, no fuzzy matching), asks Semantic
Scholar for citation counts, and emits Signal observations. Citations lag by months, so this is a
baseline signal that also exercises the signal pipeline; faster proxies (HF trending in PR 10, and
later GitHub stars / Bluesky) carry the real ignition signal.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType
from researchscout.sources.base import HealthStatus, RawItem, Source, register, source_config

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = "citationCount,influentialCitationCount"
_REQUEST_TIMEOUT = 30.0


@register
class SemanticScholarSource(Source):
    name = "semantic_scholar"
    kind = "signal"

    def __init__(self, api_key: str | None = None, page_size: int = 100) -> None:
        cfg = source_config(self.name)
        self._api_key: str | None = api_key or cfg.get("api_key") or os.environ.get("S2_API_KEY")
        self.page_size = page_size

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key} if self._api_key else {}

    def _target_papers(self, offset: int, limit: int) -> list[tuple[str, str]]:
        """(canonical_id, arxiv_id) for stored papers with an arXiv external id — the match set."""
        from researchscout.store.db import session_scope
        from researchscout.store.models import ExternalIdRow

        with session_scope() as session:
            rows = session.execute(
                select(ExternalIdRow.paper_id, ExternalIdRow.value)
                .where(ExternalIdRow.scheme == "arxiv")
                .order_by(ExternalIdRow.paper_id)
                .offset(offset)
                .limit(limit)
            ).all()
        return [(paper_id, arxiv_id) for paper_id, arxiv_id in rows]

    def _fetch_citations(self, arxiv_id: str) -> dict[str, Any] | None:
        resp = httpx.get(
            f"{_S2_BASE}/paper/arXiv:{arxiv_id}",
            params={"fields": _FIELDS},
            headers=self._headers(),
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return None  # paper unknown to Semantic Scholar
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        offset = int(cursor) if cursor else 0
        targets = self._target_papers(offset, self.page_size)
        fetched_at = datetime.now(UTC)
        items: list[RawItem] = []
        for paper_id, arxiv_id in targets:
            data = self._fetch_citations(arxiv_id)
            if data is None:
                continue
            items.append(
                RawItem(
                    source=self.name, fetched_at=fetched_at, payload={"paper_id": paper_id, **data}
                )
            )
        next_cursor = str(offset + self.page_size) if len(targets) == self.page_size else None
        return items, next_cursor

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
