"""Single-paper import: fetch one arXiv entry, land it, save it, queue enrichment.

The synchronous half lands the paper row (through the same normalize-and-clean pipeline
the stream applies, so a later re-publish converges) and saves it to the Reading list -
which also puts it at the head of the fulltext priority queue. The asynchronous half is
one best-effort envelope onto the raw topic: the stream then enriches it exactly like any
polled paper. With Kafka down the import still succeeds; the producer dedup re-publishes
unenriched papers on the next poll, and scout index remains the manual backfill.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.embed.base import Embedder
from researchscout.ingest.pipeline import resolve_existing
from researchscout.sources.arxiv import _API_URL, _entry_payload, _normalize_payload
from researchscout.store.papers import link_external_ids, upsert_paper
from researchscout.store.raw import append_raw
from researchscout.store.saved import save_paper
from researchscout.store.vectors import upsert_embedding
from researchscout.stream.broker import KafkaBroker, StreamTopics
from researchscout.stream.envelope import Envelope, encode
from researchscout.stream.parse import clean_text, strip_structural_tex
from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_PUBLISH_FLUSH_SEC = 2.0


def fetch_arxiv_entry(arxiv_id: str, *, timeout: float = _TIMEOUT) -> dict[str, Any] | None:
    """One entry payload by bare arXiv id, or None when arXiv does not know the id."""
    resp = httpx.get(
        _API_URL,
        params={"id_list": arxiv_id, "max_results": "1"},
        headers=default_headers(),
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    if not feed.entries:
        return None
    payload = _entry_payload(feed.entries[0])
    # Unknown ids come back as a stub entry with no title or dates.
    if not payload.get("title") or not payload.get("published"):
        return None
    return payload


def import_paper(
    session: Session,
    user_sub: str,
    payload: dict[str, Any],
    embedder: Embedder | None = None,
) -> tuple[str, str, bool, bool]:
    """Land one paper synchronously and save it; returns (paper_id, title, already_known,
    embedded).

    Title and abstract pass through the same cleanup the stream's parse stage applies, so
    the row is identical whichever path wrote it first. With an ``embedder`` the paper's
    vector is written in the same transaction - the deployment has no stream to enrich it
    later, and an import should be retrievable by the vector leg immediately.
    """
    paper = _normalize_payload(payload)
    paper = paper.model_copy(
        update={
            "title": clean_text(strip_structural_tex(paper.title)),
            "abstract": clean_text(strip_structural_tex(paper.abstract)),
        }
    )
    existing = resolve_existing(session, paper)
    already_known = existing is not None
    if existing is not None and existing != paper.id:
        # A cross-source duplicate: link the ids, never rewrite the canonical row.
        link_external_ids(session, existing, paper.external_ids)
        paper_id = existing
    else:
        if not already_known:
            append_raw(session, source="arxiv", fetched_at=datetime.now(UTC), payload=payload)
        paper_id = upsert_paper(session, paper)
    embedded = False
    if embedder is not None:
        # The exact text index_papers and the categorize stage embed, so the row converges.
        vector = embedder.embed_documents([f"{paper.title}\n\n{paper.abstract}"])[0]
        upsert_embedding(session, paper_id, embedder.model_id, vector)
        embedded = True
    save_paper(session, user_sub, paper_id)
    return paper_id, paper.title, already_known, embedded


def publish_enrichment(settings: Settings, payload: dict[str, Any]) -> bool:
    """Queue the imported paper for stream enrichment, best-effort with a bounded flush.

    Returns False only on hard failures (broker construction, serialization); a down
    broker simply drops the packet after the bounded flush, and the producer dedup
    re-publishes unenriched papers on the next poll either way.
    """
    try:
        envelope = Envelope(
            kind="paper",
            source="arxiv",
            fetched_at=datetime.now(UTC),
            payload={"raw": payload},
        )
        envelope.finish(envelope.begin("produce"))
        topics = StreamTopics.for_prefix(settings.kafka_topic_prefix)
        broker = KafkaBroker(settings.kafka_bootstrap)
        broker.publish(topics.raw, envelope.key(), encode(envelope))
        broker.flush(timeout=_PUBLISH_FLUSH_SEC)
    except Exception:  # noqa: BLE001 - the paper is stored; enrichment self-heals
        logger.warning("enrichment publish failed; the next poll re-publishes", exc_info=True)
        return False
    return True
