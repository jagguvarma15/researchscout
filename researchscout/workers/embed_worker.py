"""Consume ``papers.new`` and upsert an embedding per paper.

The document text mirrors ``store.vectors.index_papers`` (title + abstract) so batch indexing
and event-driven indexing produce identical vectors; the upsert PK makes replays free.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.events.schemas import TOPIC_PAPERS_NEW, PaperCreated
from researchscout.store.vectors import upsert_embedding

logger = logging.getLogger(__name__)


def handle_paper(session: Session, embedder: Embedder, event: PaperCreated) -> None:
    """Embed one paper inside the caller's transaction."""
    paper = event.paper
    text = f"{paper.title}\n\n{paper.abstract}"
    vector = embedder.embed_documents([text])[0]
    upsert_embedding(session, paper.id, embedder.model_id, vector)


def run() -> None:  # pragma: no cover - composition loop, exercised live
    """The worker loop: poll, embed, commit."""
    from researchscout.obs.otel import init_otel

    init_otel("researchscout-embed-worker")
    from researchscout.embed.local import LocalEmbedder
    from researchscout.events.kafka import consumer, ensure_topics
    from researchscout.store.db import session_scope

    ensure_topics([TOPIC_PAPERS_NEW])
    client = consumer("rs-embed", [TOPIC_PAPERS_NEW])
    embedder = LocalEmbedder()
    logger.info("embed worker consuming %s", TOPIC_PAPERS_NEW)
    while True:
        message = client.poll(1.0)
        if message is None:
            continue
        if message.error():
            logger.error("consumer error: %s", message.error())
            continue
        event = PaperCreated.model_validate_json(message.value())
        try:
            with session_scope() as session:
                handle_paper(session, embedder, event)
        except Exception:
            logger.exception("embed failed for %s; leaving offset uncommitted", event.paper.id)
            continue
        client.commit(message)
        logger.info("embedded %s", event.paper.id)
