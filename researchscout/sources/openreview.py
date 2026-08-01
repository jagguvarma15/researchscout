"""OpenReview review scores, best-effort: the only source for the ``review_score`` signal.

Peer-review ratings are uniquely high-signal but hard to reach: as of 2026-07-28 the main
``/notes`` listing endpoint answers with a bot challenge (403 ChallengeRequired), leaving only
the search endpoint open. So this source searches each recent stored paper's title, verifies an
exact normalized-title match against a Submission note to learn its forum id, and averages the
``Official_Review`` ratings that surface in the same results for that forum. Reviews that do
not surface in the search are simply missed — a partial, level-only signal, bursty around
conference cycles. Ships disabled; flip it on around review season.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from researchscout.schema import Signal, SignalType
from researchscout.sources.base import HealthStatus, RawItem, Source, register
from researchscout.useragent import default_headers

_SEARCH = "https://api2.openreview.net/notes/search"
_REQUEST_TIMEOUT = 30.0
_PER_PAPER_CAP = 25
_PER_PAPER_DELAY_SEC = 1.0

_LEADING_INT_RE = re.compile(r"\d+")


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _content_value(content: dict[str, Any], key: str) -> Any:
    """API v2 wraps content values as {\"value\": ...}; v1 stores them bare."""
    value = content.get(key)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _rating_of(note: dict[str, Any]) -> float | None:
    """Parse a rating like 6, \"6\", or \"6: marginally above threshold\"."""
    raw = _content_value(note.get("content") or {}, "rating")
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        match = _LEADING_INT_RE.search(raw)
        if match:
            return float(match.group(0))
    return None


@register
class OpenReviewSource(Source):
    name = "openreview"
    kind = "signal"

    def _recent_stored(self, since: datetime) -> list[tuple[str, str]]:
        """(canonical id, title) for the newest stored papers published in the window."""
        from researchscout.store.db import session_scope
        from researchscout.store.models import PaperRow

        with session_scope() as session:
            rows = session.execute(
                select(PaperRow.id, PaperRow.title)
                .where(PaperRow.published_at >= since)
                .order_by(PaperRow.published_at.desc())
                .limit(_PER_PAPER_CAP)
            ).all()
        return [(paper_id, title) for paper_id, title in rows]

    @staticmethod
    def _score_from_notes(title: str, notes: list[dict[str, Any]]) -> float | None:
        """Mean Official_Review rating for the forum whose Submission title matches exactly."""
        wanted = _normalize_title(title)
        forum: str | None = None
        for note in notes:
            invitation = str((note.get("invitations") or [""])[0])
            note_title = _content_value(note.get("content") or {}, "title")
            if invitation.endswith("/-/Submission") and isinstance(note_title, str):
                if _normalize_title(note_title) == wanted:
                    forum = str(note.get("forum") or note.get("id") or "")
                    break
        if not forum:
            return None
        ratings = [
            rating
            for note in notes
            if "Official_Review" in str((note.get("invitations") or [""])[0])
            and str(note.get("forum") or "") == forum
            and (rating := _rating_of(note)) is not None
        ]
        return sum(ratings) / len(ratings) if ratings else None

    def fetch(self, since: datetime, cursor: str | None) -> tuple[list[RawItem], str | None]:
        fetched_at = datetime.now(UTC)
        items: list[RawItem] = []
        for paper_id, title in self._recent_stored(since):
            try:
                resp = httpx.get(
                    _SEARCH,
                    params={"term": title, "type": "terms"},
                    headers=default_headers(),
                    timeout=_REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                break
            if resp.status_code != 200:
                break  # challenge or throttle: stop the walk, keep what we have
            notes = resp.json().get("notes") or []
            score = self._score_from_notes(title, notes)
            if score is not None:
                items.append(
                    RawItem(
                        source=self.name,
                        fetched_at=fetched_at,
                        payload={"paper_id": paper_id, "score": score},
                    )
                )
            time.sleep(_PER_PAPER_DELAY_SEC)
        return items, None

    def normalize(self, raw: RawItem) -> Signal:
        payload = raw.payload
        return Signal(
            paper_id=str(payload["paper_id"]),
            type=SignalType.review_score,
            source=self.name,
            value=float(payload["score"]),
            metadata={},
            observed_at=raw.fetched_at,
        )

    def health(self) -> HealthStatus:
        try:
            resp = httpx.get(
                _SEARCH,
                params={"term": "attention", "type": "terms"},
                headers=default_headers(),
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return "error"
        if resp.status_code == 429:
            return "rate_limited"
        return "ok" if resp.is_success else "error"
