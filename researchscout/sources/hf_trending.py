"""Hugging Face daily-papers trending signal.

A fast ignition proxy: HF's daily-papers feed surfaces what the community is reading and upvoting
right now, days before citations move. This is a signal source, not a content source — it attaches a
``hf_trending_rank`` observation to papers already stored (matched by their arXiv id), and skips the
rest. One ``fetch`` is one snapshot of the current list; run periodically, the rank/upvote series
gives the momentum that later scoring turns into an ignition signal.

The stored value is the paper's 1-based position in the day's list (1 = most trending); upvote and
comment counts ride along in metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType, normalize_arxiv_id
from researchscout.sources.base import HealthStatus, RawItem, Source, register

_HF_DAILY = "https://huggingface.co/api/daily_papers"
_REQUEST_TIMEOUT = 30.0


@register
class HuggingFaceTrendingSource(Source):
    name = "hf_trending"
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

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        resp = httpx.get(_HF_DAILY, timeout=_REQUEST_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        entries: Any = resp.json()
        fetched_at = datetime.now(UTC)

        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for rank, entry in enumerate(entries, start=1):
            paper = entry.get("paper") or {}
            raw_id = paper.get("id")
            if not raw_id:
                continue
            ranked.append((rank, normalize_arxiv_id(str(raw_id)), {**entry, "paper": paper}))

        stored = self._match_stored([arxiv_id for _, arxiv_id, _ in ranked])
        items: list[RawItem] = []
        for rank, arxiv_id, entry in ranked:
            paper_id = stored.get(arxiv_id)
            if paper_id is None:
                continue
            paper = entry["paper"]
            items.append(
                RawItem(
                    source=self.name,
                    fetched_at=fetched_at,
                    payload={
                        "paper_id": paper_id,
                        "rank": rank,
                        "upvotes": paper.get("upvotes"),
                        "num_comments": entry.get("numComments"),
                    },
                )
            )
        return items, None

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=SignalType.hf_trending_rank,
            source=self.name,
            value=float(payload["rank"]),
            metadata={
                "upvotes": payload.get("upvotes"),
                "num_comments": payload.get("num_comments"),
            },
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(_HF_DAILY, timeout=_REQUEST_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
