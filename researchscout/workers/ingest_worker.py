"""Consume ``ingest.jobs``, run the pull pipeline, and publish new papers to ``papers.new``.

At-least-once: the offset is committed only after the DB transaction commits, and a replayed job
collapses onto existing external ids, so duplicates cost nothing.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from researchscout.events.schemas import TOPIC_INGEST_JOBS, TOPIC_PAPERS_NEW, IngestJob
from researchscout.events.sink import EventSink
from researchscout.ingest.pipeline import IngestSummary, run_ingest
from researchscout.sources import get_source
from researchscout.sources.arxiv import ArxivSource

logger = logging.getLogger(__name__)


def handle_job(session: Session, job: IngestJob, sink: EventSink) -> IngestSummary:
    """Run one ingest job inside the caller's transaction."""
    source = get_source(job.source)
    if job.categories and isinstance(source, ArxivSource):
        source.categories = job.categories
    return run_ingest(session, source, job.since, max_items=job.max_items, events=sink)


def run() -> None:  # pragma: no cover - composition loop, exercised live
    """The worker loop: poll, handle, commit."""
    from researchscout.events.kafka import consumer, ensure_topics
    from researchscout.events.sink import KafkaEventSink
    from researchscout.store.db import session_scope

    ensure_topics([TOPIC_INGEST_JOBS, TOPIC_PAPERS_NEW])
    client = consumer("rs-ingest", [TOPIC_INGEST_JOBS])
    sink = KafkaEventSink()
    logger.info("ingest worker consuming %s", TOPIC_INGEST_JOBS)
    while True:
        message = client.poll(1.0)
        if message is None:
            continue
        if message.error():
            logger.error("consumer error: %s", message.error())
            continue
        job = IngestJob.model_validate_json(message.value())
        try:
            with session_scope() as session:
                summary = handle_job(session, job, sink)
        except Exception:
            logger.exception("ingest job failed for %s; leaving offset uncommitted", job.source)
            continue
        client.commit(message)
        logger.info(
            "ingested %s: fetched=%d new=%d collapsed=%d signals=%d",
            summary.source,
            summary.fetched,
            summary.new_papers,
            summary.collapsed,
            summary.signals,
        )
