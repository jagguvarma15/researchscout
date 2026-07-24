"""GitHub code-adoption signal — stars on the repository implementing a paper.

Since Papers With Code shut down (July 2025), there is no single arXiv-to-repo index left, so this
resolves a paper's repository with a GitHub repository search on its arXiv id and records the top
match's star count as a ``code_stars`` observation. Recall is intentionally conservative: it only
matches when a repo names or describes the arXiv id, which avoids false positives at the cost of
missing repos that never mention it. Stars grow as a project catches on, so the series is an
adoption-momentum signal.

Set ``GITHUB_TOKEN`` (env) or ``token`` in the source config to lift the unauthenticated search
rate limit; the source is disabled by default because it is thin without one.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType
from researchscout.sources.base import HealthStatus, RawItem, Source, register, source_config

_GH_SEARCH = "https://api.github.com/search/repositories"
_REQUEST_TIMEOUT = 30.0


@register
class GitHubCodeAdoptionSource(Source):
    name = "code_adoption"
    kind = "signal"

    def __init__(self, token: str | None = None, page_size: int = 50) -> None:
        cfg = source_config(self.name)
        self._token: str | None = token or cfg.get("token") or os.environ.get("GITHUB_TOKEN")
        self.page_size = page_size

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

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

    def _top_repo(self, arxiv_id: str) -> dict[str, Any] | None:
        resp = httpx.get(
            _GH_SEARCH,
            params={"q": f'"{arxiv_id}"', "sort": "stars", "order": "desc", "per_page": 1},
            headers=self._headers(),
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        items = data.get("items") or []
        return items[0] if items else None

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        offset = int(cursor) if cursor else 0
        targets = self._target_papers(offset, self.page_size)
        fetched_at = datetime.now(UTC)
        items: list[RawItem] = []
        for paper_id, arxiv_id in targets:
            repo = self._top_repo(arxiv_id)
            if repo is None:
                continue
            items.append(
                RawItem(
                    source=self.name,
                    fetched_at=fetched_at,
                    payload={
                        "paper_id": paper_id,
                        "stars": repo.get("stargazers_count"),
                        "repo": repo.get("full_name"),
                        "url": repo.get("html_url"),
                    },
                )
            )
        next_cursor = str(offset + self.page_size) if len(targets) == self.page_size else None
        return items, next_cursor

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=SignalType.code_stars,
            source=self.name,
            value=float(payload.get("stars") or 0),
            metadata={"repo": payload.get("repo"), "url": payload.get("url")},
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(
                _GH_SEARCH,
                params={"q": "arxiv", "per_page": 1},
                headers=self._headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code in (403, 429):
            return "rate_limited"
        return "ok" if resp.is_success else "error"
