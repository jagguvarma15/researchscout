"""Producer tasks: poll sources on the scheduler loop and publish raw packets to Kafka.

The battle-tested Scheduler drives these (interval pacing, failure isolation), and cursors
stay in the ingest_state store shared with the manual scout ingest fallback, so batch and
stream never fight. Producing deliberately lives outside the dataflow: network pacing
(arXiv page delays, per-paper fulltext fetches) must never stall the processing worker.
Raw payloads still land in raw_items before publishing, keeping replay parity with batch.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.fulltext import fetch_full_text
from researchscout.scheduler import Task
from researchscout.sources.base import Source, SourceKind, enabled_sources
from researchscout.store.db import session_scope
from researchscout.store.models import EventRow, SavedPaperRow
from researchscout.store.papers import papers_missing_full_text, set_full_text
from researchscout.store.raw import append_raw
from researchscout.store.state import save_state
from researchscout.stream.broker import Broker, StreamTopics
from researchscout.stream.envelope import Envelope, Kind, encode

logger = logging.getLogger(__name__)

# Defensive cap under the raw topic's 5MB max.message.bytes.
_FULLTEXT_TEXT_CAP = 2_000_000


def publish_source(
    broker: Broker, topics: StreamTopics, source: Source, since: datetime, *, kind: Kind
) -> int:
    """Page through one source, landing raw items and publishing packets; returns count."""
    published = 0
    cursor: str | None = None
    while True:
        items, next_cursor = source.fetch(since, cursor)
        with session_scope() as session:
            for raw in items:
                append_raw(
                    session, source=raw.source, fetched_at=raw.fetched_at, payload=raw.payload
                )
                envelope = Envelope(
                    kind=kind,
                    source=source.name,
                    fetched_at=raw.fetched_at,
                    payload={"raw": raw.payload},
                )
                envelope.finish(envelope.begin("produce"))
                broker.publish(topics.raw, envelope.key(), encode(envelope))
                published += 1
            save_state(session, source.name, next_cursor, since)
        cursor = next_cursor
        if cursor is None:
            break
    broker.flush()
    return published


def _poll_kind(settings: Settings, broker: Broker, topics: StreamTopics, kind: Kind) -> None:
    source_kind: SourceKind = "content" if kind == "paper" else "signal"
    since = datetime.now(UTC) - timedelta(days=settings.scheduler_ingest_window_days)
    for source in enabled_sources(source_kind):
        try:
            published = publish_source(broker, topics, source, since, kind=kind)
        except Exception:  # noqa: BLE001 - isolate one source's failure from the rest
            logger.warning("producer for %s failed", source.name, exc_info=True)
            continue
        logger.info("%s: published %d %s packet(s)", source.name, published, kind)


def _priority_ids(session: Session) -> set[str]:
    """Saved or interacted-with papers jump the fulltext queue (mirrors scout fulltext)."""
    return set(session.execute(select(SavedPaperRow.paper_id)).scalars()) | set(
        session.execute(select(EventRow.paper_id).distinct()).scalars()
    )


def poll_fulltext(settings: Settings, broker: Broker, topics: StreamTopics) -> None:
    """Fetch full text for a modest batch and publish fulltext packets, politely paced.

    Papers with no HTML anywhere are marked checked here (an empty string), the one
    producer-side data write: there is nothing downstream to process, and without the mark
    the batch would re-fetch the PDF-only tail forever.
    """
    delay = settings.arxiv_page_delay_sec
    published = unavailable = 0
    with session_scope() as session:
        priority = _priority_ids(session)
        pending = papers_missing_full_text(
            session, limit=settings.stream_fulltext_batch, first=sorted(priority)
        )
        for index, (paper_id, arxiv_id) in enumerate(pending):
            if index and delay > 0:
                time.sleep(delay)
            text = fetch_full_text(arxiv_id)
            if text is None:
                set_full_text(session, paper_id, "")
                unavailable += 1
                continue
            envelope = Envelope(
                kind="fulltext",
                source="arxiv",
                fetched_at=datetime.now(UTC),
                payload={
                    "paper_id": paper_id,
                    "arxiv_id": arxiv_id,
                    "text": text[:_FULLTEXT_TEXT_CAP],
                },
            )
            envelope.finish(envelope.begin("produce"))
            broker.publish(topics.raw, envelope.key(), encode(envelope))
            published += 1
    broker.flush()
    if published or unavailable:
        logger.info("fulltext: published=%d unavailable=%d", published, unavailable)


def build_producer_tasks(settings: Settings, broker: Broker, topics: StreamTopics) -> list[Task]:
    """The three polling tasks the producer scheduler runs."""
    return [
        Task(
            "produce-content",
            settings.stream_poll_interval_sec,
            partial(_poll_kind, settings, broker, topics, "paper"),
        ),
        Task(
            "produce-signals",
            settings.scheduler_signals_interval_sec,
            partial(_poll_kind, settings, broker, topics, "signal"),
        ),
        Task(
            "produce-fulltext",
            settings.stream_fulltext_interval_sec,
            partial(poll_fulltext, settings, broker, topics),
        ),
    ]
