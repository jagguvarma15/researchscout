"""Hugging Face daily-papers trending signal, plus per-paper upvote polling.

A fast ignition proxy: HF's daily-papers feed surfaces what the community is reading and upvoting
right now, days before citations move. This is a signal source, not a content source — it attaches a
``hf_trending_rank`` observation to papers already stored (matched by their arXiv id), and skips the
rest. One ``fetch`` is one snapshot of the current list; run periodically, the rank/upvote series
gives the momentum that later scoring turns into an ignition signal.

The stored value is the paper's 1-based position in the day's list (1 = most trending); upvote and
comment counts ride along in metadata. Separately, the per-paper endpoint is polled for recently
published stored papers (capped, paced): papers that never make the daily list still collect HF
upvotes and comments, observed as ``social_mention`` and ``discussion`` under this source.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.config import get_settings
from researchscout.schema import Signal, SignalType, normalize_arxiv_id
from researchscout.sources.base import HealthStatus, RawItem, Source, register
from researchscout.useragent import default_headers

_HF_DAILY = "https://huggingface.co/api/daily_papers"
_HF_PAPER = "https://huggingface.co/api/papers/{arxiv_id}"
_REQUEST_TIMEOUT = 30.0
_PER_PAPER_CAP = 50
_PER_PAPER_DELAY_SEC = 0.3


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
        resp = httpx.get(
            _HF_DAILY,
            headers=default_headers(),
            timeout=_REQUEST_TIMEOUT,
            follow_redirects=True,
        )
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
        unknown = [arxiv_id for _, arxiv_id, _ in ranked if arxiv_id not in stored]
        if unknown and get_settings().signal_auto_import:
            # The curated daily list is trustworthy enough to ingest from: land the
            # in-scope unknowns now so their first trending observations attach instead
            # of being dropped until the nightly ingest catches up.
            from researchscout.ingest.autoimport import land_unknown_papers

            stored.update(land_unknown_papers(unknown))
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
        items.extend(self._per_paper_items(since, fetched_at))
        return items, None

    def _recent_stored(self, since: datetime) -> dict[str, str]:
        """arXiv id -> canonical id for the newest stored papers published in the window."""
        from researchscout.store.db import session_scope
        from researchscout.store.models import ExternalIdRow, PaperRow

        with session_scope() as session:
            rows = session.execute(
                select(ExternalIdRow.value, ExternalIdRow.paper_id)
                .join(PaperRow, PaperRow.id == ExternalIdRow.paper_id)
                .where(ExternalIdRow.scheme == "arxiv", PaperRow.published_at >= since)
                .order_by(PaperRow.published_at.desc())
                .limit(_PER_PAPER_CAP)
            ).all()
        return {value: paper_id for value, paper_id in rows}

    def _per_paper_items(self, since: datetime, fetched_at: datetime) -> list[RawItem]:
        """Upvote/comment observations for recent stored papers, listed on the daily feed or
        not; papers unknown to HF (404) are skipped, and any error ends the walk quietly."""
        items: list[RawItem] = []
        for arxiv_id, paper_id in self._recent_stored(since).items():
            try:
                resp = httpx.get(
                    _HF_PAPER.format(arxiv_id=arxiv_id),
                    headers=default_headers(),
                    timeout=_REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                break
            if resp.status_code != 200:
                time.sleep(_PER_PAPER_DELAY_SEC)
                continue
            payload: Any = resp.json()
            if not isinstance(payload, dict):
                continue
            upvotes = payload.get("upvotes")
            comments = payload.get("numComments")
            if upvotes is None and comments is None:
                continue
            for metric, value in (("upvotes", upvotes), ("comments", comments)):
                if value is None:
                    continue
                items.append(
                    RawItem(
                        source=self.name,
                        fetched_at=fetched_at,
                        payload={
                            "paper_id": paper_id,
                            "metric": metric,
                            "value": int(value),
                            "arxiv_id": arxiv_id,
                        },
                    )
                )
            time.sleep(_PER_PAPER_DELAY_SEC)
        return items

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        if "rank" in payload:
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
        signal_type = (
            SignalType.social_mention if payload["metric"] == "upvotes" else SignalType.discussion
        )
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=signal_type,
            source=self.name,
            value=float(payload["value"]),
            metadata={"arxiv_id": payload.get("arxiv_id")},
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(
                _HF_DAILY,
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
