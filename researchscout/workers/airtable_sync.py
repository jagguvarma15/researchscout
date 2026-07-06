"""Consume ``papers.saved`` and mirror each user's reading list into Airtable.

Rows are keyed by (user, paper id): a save creates the row if missing, an unsave deletes it —
so at-least-once replays are free. Enrichment (title/link) comes from the paper store at sync
time; if the paper is somehow gone, the row still lands with the id.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from researchscout.events.schemas import TOPIC_PAPERS_SAVED, PaperSaved
from researchscout.store.papers import get_paper

logger = logging.getLogger(__name__)


def handle_saved(session: Session, table: Any, event: PaperSaved) -> None:
    """Apply one save/unsave to the Airtable table (idempotent)."""
    from pyairtable.formulas import match

    formula = match({"User": event.user_sub, "Paper": event.paper_id})
    existing = table.all(formula=formula)
    if not event.saved:
        for row in existing:
            table.delete(row["id"])
        return
    if existing:
        return
    paper = get_paper(session, event.paper_id)
    table.create(
        {
            "User": event.user_sub,
            "Paper": event.paper_id,
            "Title": paper.title if paper else event.paper_id,
            "Link": (paper.url if paper else None) or "",
            "Saved at": event.at.isoformat(),
        }
    )


def run() -> None:  # pragma: no cover - composition loop, exercised live
    """The worker loop: poll, mirror, commit."""
    from pyairtable import Api

    from researchscout.config import get_settings
    from researchscout.events.kafka import consumer, ensure_topics
    from researchscout.store.db import session_scope

    settings = get_settings()
    if not settings.airtable_api_key or not settings.airtable_base_id:
        raise SystemExit("RS_AIRTABLE_API_KEY and RS_AIRTABLE_BASE_ID are required")
    table = Api(settings.airtable_api_key).table(
        settings.airtable_base_id, settings.airtable_saved_table
    )

    ensure_topics([TOPIC_PAPERS_SAVED])
    client = consumer("rs-airtable", [TOPIC_PAPERS_SAVED])
    logger.info("airtable sync consuming %s", TOPIC_PAPERS_SAVED)
    while True:
        message = client.poll(1.0)
        if message is None:
            continue
        if message.error():
            logger.error("consumer error: %s", message.error())
            continue
        event = PaperSaved.model_validate_json(message.value())
        try:
            with session_scope() as session:
                handle_saved(session, table, event)
        except Exception:
            logger.exception(
                "airtable sync failed for %s; leaving offset uncommitted", event.paper_id
            )
            continue
        client.commit(message)
        logger.info("synced %s saved=%s", event.paper_id, event.saved)
