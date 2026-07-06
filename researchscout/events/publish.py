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

from researchscout.events.schemas import TOPIC_PAPERS_SAVED, PaperSaved

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _producer() -> Any:
    from researchscout.events.kafka import producer

    return producer()


def _on_delivery(err: Any, message: Any) -> None:
    if err is not None:
        logger.warning("event delivery failed for %s: %s", message.topic(), err)


def publish_paper_saved(user_sub: str, paper_id: str, saved: bool) -> None:
    event = PaperSaved(user_sub=user_sub, paper_id=paper_id, saved=saved, at=datetime.now(UTC))
    try:
        client = _producer()
        client.produce(
            TOPIC_PAPERS_SAVED,
            key=paper_id.encode(),
            value=event.model_dump_json().encode(),
            on_delivery=_on_delivery,
        )
        client.poll(0)
    except Exception:  # noqa: BLE001 - best-effort by design
        logger.warning("could not publish papers.saved for %s", paper_id, exc_info=True)
