"""Idempotent Postgres sink: paper, signal, and fulltext packets land here.

One transaction per batch (or per envelope on the serial path); every write is a
natural-key upsert (canonical paper id, the signals unique observation index,
delete-then-insert chunks), so at-least-once redelivery converges. Lineage rows commit in
the same transaction as the data they describe; a packet that fails inside a batch rolls
back only its savepoint while its failure stamp still commits with the batch.
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
from researchscout.store.lineage import record_stages, record_stages_many
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

    def _inject_one(self, session: Session, item: Categorized) -> str | None:
        """Dispatch one packet's writes; returns a skip reason, or None when it landed."""
        envelope = item.envelope
        if envelope.kind == "paper" and "paper" in envelope.payload:
            self._inject_paper(session, envelope, item.vector)
        elif envelope.kind == "signal" and "signal" in envelope.payload:
            self._inject_signal(session, envelope)
        elif envelope.kind == "fulltext" and "text" in envelope.payload:
            return self._inject_fulltext(session, envelope)
        else:
            return "nothing parsed to inject"
        return None

    def run(self, item: Categorized) -> Envelope:
        """Land one packet; the lineage stamp records ok, skipped, or the failure."""
        envelope = item.envelope
        stamp = envelope.begin("inject")
        try:
            with self._session_factory() as session:
                skipped = self._inject_one(session, item)
                if skipped is None:
                    envelope.finish(stamp)
                else:
                    envelope.finish(stamp, "skipped", skipped)
                record_stages(session, envelope)
        except Exception as exc:  # noqa: BLE001 - a bad packet must not stop the flow
            envelope.finish(stamp, "error", f"{type(exc).__name__}: {exc}")
            self._record_failure(envelope)
        return envelope

    def run_batch(self, items: list[Categorized]) -> list[Envelope]:
        """Land a batch in one transaction; a bad packet rolls back only its savepoint.

        Failure lineage commits with the batch (better than the serial path's separate
        session). A batch-level failure (connection loss, the outer commit) degrades to
        the serial path, whose upserts make the partial replay converge; the duplicate
        inject stamp that path adds upserts onto the same lineage row.
        """
        if not items:
            return []
        try:
            with self._session_factory() as session:
                for item in items:
                    envelope = item.envelope
                    stamp = envelope.begin("inject")
                    try:
                        with session.begin_nested():
                            skipped = self._inject_one(session, item)
                    except Exception as exc:  # noqa: BLE001 - isolate the one packet
                        envelope.finish(stamp, "error", f"{type(exc).__name__}: {exc}")
                    else:
                        if skipped is None:
                            envelope.finish(stamp)
                        else:
                            envelope.finish(stamp, "skipped", skipped)
                record_stages_many(session, [item.envelope for item in items])
        except Exception:  # noqa: BLE001 - degrade to per-item sessions
            logger.warning("batch inject failed; retrying serially", exc_info=True)
            return [self.run(item) for item in items]
        return [item.envelope for item in items]

    def _record_failure(self, envelope: Envelope) -> None:
        """The data transaction rolled back; keep its lineage in a session of its own."""
        try:
            with self._session_factory() as session:
                record_stages(session, envelope)
        except Exception:  # noqa: BLE001 - lineage loss is survivable, crashing is not
            logger.warning("could not record failure lineage", exc_info=True)
