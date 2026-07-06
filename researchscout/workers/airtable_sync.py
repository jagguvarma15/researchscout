"""Mirror the event plane into Airtable: reading lists (``papers.saved``) and the digest
archive (``digests.published``).

Rows are keyed — (user, paper id) for saves, slug for digests — so at-least-once replays are
free: creates skip existing rows, unsaves delete whatever matches. Enrichment (title/link)
comes from the paper store at sync time; if the paper is somehow gone, the row still lands
with the id.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from researchscout.events.schemas import (
    TOPIC_DIGESTS_PUBLISHED,
    TOPIC_PAPERS_SAVED,
    DigestPublished,
    PaperSaved,
)
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


def handle_digest(table: Any, event: DigestPublished) -> None:
    """Archive one digest run (idempotent: the slug row is created once, then updated)."""
    from pyairtable.formulas import match

    fields = {
        "Slug": event.slug,
        "Title": event.title,
        "From": event.period_start.isoformat(),
        "To": event.period_end.isoformat(),
    }
    existing = table.all(formula=match({"Slug": event.slug}))
    if existing:
        table.update(existing[0]["id"], fields)
    else:
        table.create(fields)


def run() -> None:  # pragma: no cover - composition loop, exercised live
    """The worker loop: poll both topics, mirror, commit."""
    from pyairtable import Api

    from researchscout.config import get_settings
    from researchscout.events.kafka import consumer, ensure_topics
    from researchscout.store.db import session_scope

    settings = get_settings()
    if not settings.airtable_api_key or not settings.airtable_base_id:
        raise SystemExit("RS_AIRTABLE_API_KEY and RS_AIRTABLE_BASE_ID are required")
    api = Api(settings.airtable_api_key)
    saved_table = api.table(settings.airtable_base_id, settings.airtable_saved_table)
    digest_table = api.table(settings.airtable_base_id, settings.airtable_digest_table)

    ensure_topics([TOPIC_PAPERS_SAVED, TOPIC_DIGESTS_PUBLISHED])
    client = consumer("rs-airtable", [TOPIC_PAPERS_SAVED, TOPIC_DIGESTS_PUBLISHED])
    logger.info("airtable sync consuming %s, %s", TOPIC_PAPERS_SAVED, TOPIC_DIGESTS_PUBLISHED)
    while True:
        message = client.poll(1.0)
        if message is None:
            continue
        if message.error():
            logger.error("consumer error: %s", message.error())
            continue
        try:
            if message.topic() == TOPIC_PAPERS_SAVED:
                saved = PaperSaved.model_validate_json(message.value())
                with session_scope() as session:
                    handle_saved(session, saved_table, saved)
                logger.info("synced %s saved=%s", saved.paper_id, saved.saved)
            else:
                digest = DigestPublished.model_validate_json(message.value())
                handle_digest(digest_table, digest)
                logger.info("archived digest %s", digest.slug)
        except Exception:
            logger.exception("airtable sync failed; leaving offset uncommitted")
            continue
        client.commit(message)
