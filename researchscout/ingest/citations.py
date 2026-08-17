"""The citation walker: refresh papers' citation counts stalest-first, one source per paper.

Semantic Scholar leads: batches of 500, newest never-fetched papers first, up to a daily
budget. Every paper a successful batch covers gets its watermark stamped — including papers
the response omitted, because "Semantic Scholar does not know this paper yet" is a real
observation and stamping sends it to the back of the queue instead of starving the tail.
OpenAlex then takes only papers whose watermark is absent or stale (S2 throttled out before
reaching them, or has not known them for a while), so at most one source refreshes a paper
per day and ``papers.citation_count`` — last writer wins — always carries the most recently
refreshed source's count. A throttled pass stops gracefully and keeps its progress: the
watermark is the cursor, so tomorrow's walk continues wherever coverage is thinnest.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.schema import Signal
from researchscout.sources.base import enabled_sources
from researchscout.store.citations import (
    mark_citations_refreshed,
    stale_fallback_targets,
    stalest_citation_targets,
)
from researchscout.store.db import session_scope
from researchscout.store.papers import set_citation_count
from researchscout.store.signals import append_signal_idempotent

logger = logging.getLogger(__name__)

_BATCH = 500
_FALLBACK_BATCH = 500
_BATCH_PAUSE_SEC = 1.0


def _write_batch(session: Session, signals: list[Signal]) -> None:
    for signal in signals:
        append_signal_idempotent(session, signal)
        set_citation_count(session, signal.paper_id, int(signal.value))


def _primary_pass(source: object, budget: int) -> str:
    refreshed = 0
    seen: set[str] = set()
    stopped: str | None = None
    while refreshed < budget:
        with session_scope() as session:
            targets = stalest_citation_targets(session, limit=min(_BATCH, budget - refreshed))
        targets = [(pid, aid) for pid, aid in targets if pid not in seen]
        if not targets:
            break
        seen.update(pid for pid, _ in targets)
        try:
            signals = source.citations_for(targets)  # type: ignore[attr-defined]
        except httpx.HTTPError as exc:
            stopped = str(exc) or exc.__class__.__name__
            break
        with session_scope() as session:
            _write_batch(session, signals)
            mark_citations_refreshed(
                session,
                (pid for pid, _ in targets),
                source="semantic_scholar",
                fetched_at=datetime.now(UTC),
            )
        refreshed += len(targets)
        if refreshed < budget:
            time.sleep(_BATCH_PAUSE_SEC)
    note = f"s2: {refreshed} paper(s)"
    return f"{note}, stopped early: {stopped}" if stopped else note


def _fallback_pass(source: object, *, older_than: datetime, budget: int) -> str:
    if not getattr(source, "has_key", True):
        return "openalex: skipped (no key)"
    refreshed = 0
    seen: set[str] = set()
    stopped: str | None = None
    while refreshed < budget:
        with session_scope() as session:
            targets = stale_fallback_targets(
                session, older_than=older_than, limit=min(_FALLBACK_BATCH, budget - refreshed)
            )
        targets = [(pid, aid) for pid, aid in targets if pid not in seen]
        if not targets:
            break
        seen.update(pid for pid, _ in targets)
        try:
            signals = source.citations_for(targets)  # type: ignore[attr-defined]
        except httpx.HTTPError as exc:
            stopped = str(exc) or exc.__class__.__name__
            break
        with session_scope() as session:
            _write_batch(session, signals)
            mark_citations_refreshed(
                session,
                (pid for pid, _ in targets),
                source="openalex",
                fetched_at=datetime.now(UTC),
            )
        refreshed += len(targets)
        if refreshed < budget:
            time.sleep(_BATCH_PAUSE_SEC)
    note = f"openalex: {refreshed} paper(s)"
    return f"{note}, stopped early: {stopped}" if stopped else note


def run_citation_refresh(settings: Settings) -> str:
    """One daily citation refresh; returns the ledger note."""
    by_name = {source.name: source for source in enabled_sources("signal")}
    notes: list[str] = []

    primary = by_name.get("semantic_scholar")
    if primary is not None:
        notes.append(_primary_pass(primary, settings.citations_daily_papers))
    else:
        notes.append("s2: disabled")

    fallback = by_name.get("openalex")
    if fallback is not None:
        older_than = datetime.now(UTC) - timedelta(days=settings.citations_fallback_days)
        notes.append(
            _fallback_pass(
                fallback, older_than=older_than, budget=settings.citations_fallback_papers
            )
        )
    else:
        notes.append("openalex: disabled")

    return "; ".join(notes)
