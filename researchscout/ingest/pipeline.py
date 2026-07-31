"""The ingest pipeline: fetch → normalize → strict dedup → store.

Wires a source connector to storage. Idempotent and replayable: raw payloads are kept, the
cursor is persisted, and a re-run over the same window writes zero new papers because dedup
collapses already-seen external ids onto the existing canonical record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from researchscout.schema import Paper, Signal, SignalType
from researchscout.sources.base import Source
from researchscout.store.papers import (
    find_by_external_id,
    link_external_ids,
    set_citation_count,
    upsert_paper,
)
from researchscout.store.raw import append_raw
from researchscout.store.signals import append_signal
from researchscout.store.state import get_state, save_state


@dataclass
class IngestSummary:
    source: str
    fetched: int = 0
    new_papers: int = 0
    collapsed: int = 0
    signals: int = 0
    raw_stored: int = 0


def resolve_existing(session: Session, paper: Paper) -> str | None:
    """Return the canonical id this paper already maps to (strict external-id match), if any."""
    for scheme, value in paper.external_ids.items():
        existing = find_by_external_id(session, scheme, value)
        if existing is not None:
            return existing
    return None


def run_ingest(
    session: Session,
    source: Source,
    since: datetime,
    *,
    max_items: int | None = None,
    resume: bool = False,
) -> IngestSummary:
    """Fetch a source page by page, normalize, dedup, and store; return a run summary.

    With ``resume``, continue from the persisted cursor — but only when the saved window
    matches ``since``: a cursor is an offset into one specific query, so a different window
    starts fresh at the beginning.
    """
    summary = IngestSummary(source=source.name)
    cursor: str | None = None
    if resume:
        saved_cursor, last_since = get_state(session, source.name)
        if saved_cursor is not None and last_since == since:
            cursor = saved_cursor
    while True:
        items, next_cursor = source.fetch(since, cursor)
        for raw in items:
            if max_items is not None and summary.fetched >= max_items:
                break
            summary.fetched += 1
            append_raw(session, source=raw.source, fetched_at=raw.fetched_at, payload=raw.payload)
            summary.raw_stored += 1

            obj = source.normalize(raw)
            if isinstance(obj, Signal):
                append_signal(session, obj)
                if obj.type is SignalType.citation:
                    set_citation_count(session, obj.paper_id, int(obj.value))
                summary.signals += 1
                continue
            existing = resolve_existing(session, obj)
            if existing is not None:
                link_external_ids(session, existing, obj.external_ids)
                if existing == obj.id:
                    # Same canonical paper seen again: refresh its fields from the source so a
                    # re-ingest can backfill metadata. A different id is a cross-source match
                    # and stays link-only.
                    upsert_paper(session, obj)
                summary.collapsed += 1
            else:
                upsert_paper(session, obj)
                summary.new_papers += 1

        save_state(session, source.name, next_cursor, since)
        reached_max = max_items is not None and summary.fetched >= max_items
        if next_cursor is None or reached_max:
            break
        cursor = next_cursor
    return summary
