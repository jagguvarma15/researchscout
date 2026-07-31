"""Idempotent Postgres sink: paper, signal, and fulltext packets land here.

One session per envelope; every write is a natural-key upsert (canonical paper id, the
signals unique observation index, delete-then-insert chunks), so at-least-once redelivery
converges. Lineage rows commit in the same transaction as the data they describe; when the
transaction fails, the failure stamp is recorded in its own session so it survives.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import update
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.ingest.pipeline import resolve_existing
from researchscout.schema import Paper, PaperLabel, Signal, SignalType
from researchscout.store.chunks import index_chunks_for
from researchscout.store.lineage import record_stages
from researchscout.store.models import PaperRow
from researchscout.store.papers import (
    link_external_ids,
    set_citation_count,
    set_enrichment,
    set_full_text,
    upsert_paper,
)
from researchscout.store.signals import append_signal_idempotent
from researchscout.store.vectors import upsert_embedding
from researchscout.stream.categorize import Categorized
from researchscout.stream.envelope import Envelope
from researchscout.stream.parse import recover_abstract

logger = logging.getLogger(__name__)


class Injector:
    """Per-process sink state: the embedder (for chunking) and a session factory."""

    def __init__(
        self,
        embedder: Embedder,
        session_factory: Callable[[], AbstractContextManager[Session]],
    ) -> None:
        self._embedder = embedder
        self._session_factory = session_factory

    def _inject_paper(
        self, session: Session, envelope: Envelope, vector: list[float] | None
    ) -> None:
        paper = Paper.model_validate(envelope.payload["paper"])
        existing = resolve_existing(session, paper)
        if existing is not None and existing != paper.id:
            # A cross-source duplicate: link the ids, never rewrite the canonical row.
            link_external_ids(session, existing, paper.external_ids)
            paper_id = existing
        else:
            paper_id = upsert_paper(session, paper)

        enrichment = envelope.payload.get("enrichment") or {}
        labels = []
        topic = enrichment.get("topic")
        if isinstance(topic, dict):
            labels.append(
                PaperLabel(label=topic["label"], source="topic", score=topic.get("similarity"))
            )
        labels.extend(
            PaperLabel(label=name, source="custom") for name in enrichment.get("labels") or []
        )
        keywords = enrichment.get("keywords")
        if keywords is not None or labels:
            set_enrichment(session, paper_id, keywords=keywords, labels=labels or None)
        if vector is not None:
            upsert_embedding(session, paper_id, self._embedder.model_id, vector)

    def _inject_signal(self, session: Session, envelope: Envelope) -> None:
        signal = Signal.model_validate(envelope.payload["signal"])
        append_signal_idempotent(session, signal)
        if signal.type is SignalType.citation:
            set_citation_count(session, signal.paper_id, int(signal.value))

    def _inject_fulltext(self, session: Session, envelope: Envelope) -> str | None:
        paper_id = envelope.payload["paper_id"]
        row = session.get(PaperRow, paper_id)
        if row is None:
            return "paper not stored yet"
        text = envelope.payload["text"]
        set_full_text(session, paper_id, text)
        sections = envelope.payload.get("sections")
        if sections:
            set_enrichment(session, paper_id, sections=sections)
        if not row.abstract.strip() and text:
            recovered = recover_abstract(text)
            if recovered:
                session.execute(
                    update(PaperRow).where(PaperRow.id == paper_id).values(abstract=recovered)
                )
        index_chunks_for(session, self._embedder, paper_id, text)
        return None

    def run(self, item: Categorized) -> Envelope:
        """Land one packet; the lineage stamp records ok, skipped, or the failure."""
        envelope = item.envelope
        stamp = envelope.begin("inject")
        try:
            with self._session_factory() as session:
                skipped: str | None = None
                if envelope.kind == "paper" and "paper" in envelope.payload:
                    self._inject_paper(session, envelope, item.vector)
                elif envelope.kind == "signal" and "signal" in envelope.payload:
                    self._inject_signal(session, envelope)
                elif envelope.kind == "fulltext" and "text" in envelope.payload:
                    skipped = self._inject_fulltext(session, envelope)
                else:
                    skipped = "nothing parsed to inject"
                if skipped is None:
                    envelope.finish(stamp)
                else:
                    envelope.finish(stamp, "skipped", skipped)
                record_stages(session, envelope)
        except Exception as exc:  # noqa: BLE001 - a bad packet must not stop the flow
            envelope.finish(stamp, "error", f"{type(exc).__name__}: {exc}")
            self._record_failure(envelope)
        return envelope

    def _record_failure(self, envelope: Envelope) -> None:
        """The data transaction rolled back; keep its lineage in a session of its own."""
        try:
            with self._session_factory() as session:
                record_stages(session, envelope)
        except Exception:  # noqa: BLE001 - lineage loss is survivable, crashing is not
            logger.warning("could not record failure lineage", exc_info=True)
