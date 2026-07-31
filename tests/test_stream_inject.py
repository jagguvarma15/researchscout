from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.schema import Author, Paper
from researchscout.store.db import session_scope
from researchscout.store.models import (
    PaperChunkRow,
    PaperEmbeddingRow,
    PaperRow,
    PipelineLineageRow,
    SignalRow,
)
from researchscout.store.papers import get_paper, upsert_paper
from researchscout.stream.categorize import Categorized
from researchscout.stream.envelope import Envelope
from researchscout.stream.inject import Injector

pytestmark = pytest.mark.integration

DIM = 384
VECTOR = [1.0] + [0.0] * (DIM - 1)
PID = "arxiv:2607.00001"


class MockEmbedder(Embedder):
    model_id = "mock-v1"
    dim = DIM

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [VECTOR for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return VECTOR


def _injector() -> Injector:
    return Injector(MockEmbedder(), session_scope)


def _paper_payload(abstract: str = "An abstract.") -> dict:
    return {
        "id": PID,
        "external_ids": {"arxiv": "2607.00001"},
        "title": "T",
        "abstract": abstract,
        "authors": [{"name": "X", "affiliation": None}],
        "categories": ["cs.LG"],
        "published_at": "2026-07-01T00:00:00+00:00",
        "source": "arxiv",
    }


def _paper_envelope(event_id: str = "e1") -> Categorized:
    envelope = Envelope(
        event_id=event_id,
        kind="paper",
        source="arxiv",
        fetched_at=datetime.now(UTC),
        payload={
            "paper": _paper_payload(),
            "enrichment": {
                "group": "cs",
                "tech": True,
                "topic": {"key": "t-1", "label": "Efficient attention", "similarity": 0.8},
                "keywords": ["sparse attention"],
                "keyword_method": "statistical",
                "labels": ["efficiency"],
            },
        },
    )
    return Categorized(envelope, list(VECTOR))


def _seed_paper(session: Session, abstract: str = "An abstract.") -> None:
    upsert_paper(
        session,
        Paper(
            id=PID,
            external_ids={"arxiv": "2607.00001"},
            title="T",
            abstract=abstract,
            authors=[Author(name="X")],
            categories=["cs.LG"],
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            source="arxiv",
        ),
    )
    session.commit()


def test_paper_packet_lands_and_converges(session: Session) -> None:
    _injector().run(_paper_envelope())
    _injector().run(_paper_envelope())  # redelivery

    session.expire_all()
    assert session.execute(select(func.count()).select_from(PaperRow)).scalar_one() == 1
    paper = get_paper(session, PID)
    assert paper is not None
    assert paper.keywords == ["sparse attention"]
    assert paper.labels is not None
    assert [(label.label, label.source) for label in paper.labels] == [
        ("Efficient attention", "topic"),
        ("efficiency", "custom"),
    ]
    embedding = session.get(PaperEmbeddingRow, (PID, "mock-v1"))
    assert embedding is not None
    lineage = session.get(PipelineLineageRow, ("e1", "inject"))
    assert lineage is not None and lineage.outcome == "ok" and lineage.paper_id == PID


def test_signal_packet_converges_and_materializes_citations(session: Session) -> None:
    _seed_paper(session)
    observed = "2026-07-30T06:00:00+00:00"
    signal = {
        "paper_id": PID,
        "type": "citation",
        "source": "semantic_scholar",
        "value": 12.0,
        "metadata": {},
        "observed_at": observed,
    }

    for _ in range(2):  # redelivery converges
        envelope = Envelope(
            event_id="s1",
            kind="signal",
            source="semantic_scholar",
            fetched_at=datetime.now(UTC),
            payload={"signal": signal},
        )
        _injector().run(Categorized(envelope, None))

    session.expire_all()
    assert session.execute(select(func.count()).select_from(SignalRow)).scalar_one() == 1
    row = session.get(PaperRow, PID)
    assert row is not None and row.citation_count == 12


def test_fulltext_packet_stores_text_sections_chunks_and_recovers_abstract(
    session: Session,
) -> None:
    _seed_paper(session, abstract="")
    text = "## Abstract\n\nRecovered abstract text.\n\n## Method\n\n" + "word " * 200

    def chunk_count() -> int:
        session.expire_all()
        return session.execute(
            select(func.count()).select_from(PaperChunkRow).where(PaperChunkRow.paper_id == PID)
        ).scalar_one()

    counts = []
    for _ in range(2):  # redelivery replaces, never duplicates
        envelope = Envelope(
            event_id="f1",
            kind="fulltext",
            source="arxiv",
            fetched_at=datetime.now(UTC),
            payload={"paper_id": PID, "text": text, "sections": ["Abstract", "Method"]},
        )
        _injector().run(Categorized(envelope, None))
        counts.append(chunk_count())

    session.expire_all()
    row = session.get(PaperRow, PID)
    assert row is not None
    assert row.full_text == text
    assert row.sections == ["Abstract", "Method"]
    assert row.abstract == "Recovered abstract text."
    # The chunker is section-aware (one chunk per section here); what matters is that the
    # redelivery replaced the set instead of appending to it.
    assert counts[0] > 0 and counts[1] == counts[0]


def test_fulltext_for_unknown_paper_is_skipped(session: Session) -> None:
    envelope = Envelope(
        event_id="f9",
        kind="fulltext",
        source="arxiv",
        fetched_at=datetime.now(UTC),
        payload={"paper_id": "arxiv:9999.99999", "text": "## S\n\nbody"},
    )
    _injector().run(Categorized(envelope, None))

    session.expire_all()
    lineage = session.get(PipelineLineageRow, ("f9", "inject"))
    assert lineage is not None and lineage.outcome == "skipped"


def test_failed_injection_still_records_lineage(session: Session) -> None:
    envelope = Envelope(
        event_id="bad1",
        kind="paper",
        source="arxiv",
        fetched_at=datetime.now(UTC),
        payload={"paper": {"id": "arxiv:2607.2"}},  # missing required fields
    )
    _injector().run(Categorized(envelope, None))

    session.expire_all()
    lineage = session.get(PipelineLineageRow, ("bad1", "inject"))
    assert lineage is not None and lineage.outcome == "error"
    assert session.get(PaperRow, "arxiv:2607.2") is None  # the data write rolled back
