"""Best-effort event publishing for request handlers.

A failed publish must never fail the user's request: the save is already committed, and the
consumer side is idempotent, so a lost event costs one Airtable row until the next touch.
(The transactional-outbox pattern is the upgrade path if that ever stops being acceptable.)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from researchscout.events.schemas import (
    TOPIC_DIGESTS_PUBLISHED,
    TOPIC_PAPERS_SAVED,
    DigestPublished,
    PaperSaved,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _producer() -> Any:
    from researchscout.events.kafka import producer

    return producer()


def _on_delivery(err: Any, message: Any) -> None:
    if err is not None:
        logger.warning("event delivery failed for %s: %s", message.topic(), err)


def _publish(topic: str, key: str, payload: str) -> None:
    try:
        client = _producer()
        client.produce(topic, key=key.encode(), value=payload.encode(), on_delivery=_on_delivery)
        client.poll(0)
    except Exception:  # noqa: BLE001 - best-effort by design
        logger.warning("could not publish %s for %s", topic, key, exc_info=True)


def publish_paper_saved(user_sub: str, paper_id: str, saved: bool) -> None:
    event = PaperSaved(user_sub=user_sub, paper_id=paper_id, saved=saved, at=datetime.now(UTC))
    _publish(TOPIC_PAPERS_SAVED, paper_id, event.model_dump_json())


def publish_digest_published(
    slug: str, title: str, period_start: datetime, period_end: datetime
) -> None:
    event = DigestPublished(
        slug=slug, title=title, period_start=period_start, period_end=period_end
    )
    _publish(TOPIC_DIGESTS_PUBLISHED, slug, event.model_dump_json())
