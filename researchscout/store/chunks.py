"""Chunk-level vectors: index full-text chunks and search them, pooled back to papers.

The chunk index is the retrieval depth the abstract-only index cannot give: a paper whose key
method never appears in its abstract still surfaces when a chunk matches. Search pools chunk
hits to their papers by best (minimum) distance — the standard max-pool baseline — so the
caller keeps working in paper space.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.orm import Session

from researchscout.chunking import chunk_text
from researchscout.embed.base import Embedder
from researchscout.store.models import PaperChunkRow, PaperRow
from researchscout.store.vectors import _configure_scan


def papers_missing_chunks(session: Session, model_id: str) -> list[tuple[str, str]]:
    """(paper_id, full_text) for papers with real full text but no chunks for the model."""
    have = select(PaperChunkRow.paper_id).where(PaperChunkRow.model_id == model_id)
    rows = session.execute(
        select(PaperRow.id, PaperRow.full_text).where(
            PaperRow.full_text.is_not(None),
            PaperRow.full_text != "",  # empty string marks "checked, no HTML"
            PaperRow.id.not_in(have),
        )
    ).all()
    return [(paper_id, full_text) for paper_id, full_text in rows]


def index_chunks_for(
    session: Session, embedder: Embedder, paper_id: str, full_text: str, *, batch_size: int = 64
) -> int:
    """Replace one paper's chunks for this model (delete then insert, so replays converge)."""
    session.execute(
        delete(PaperChunkRow).where(
            PaperChunkRow.paper_id == paper_id, PaperChunkRow.model_id == embedder.model_id
        )
    )
    chunks = chunk_text(full_text)
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = embedder.embed_documents([chunk.text for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            session.add(
                PaperChunkRow(
                    paper_id=paper_id,
                    model_id=embedder.model_id,
                    chunk_index=chunk.index,
                    section=chunk.section,
                    text=chunk.text,
                    embedding=vector,
                )
            )
    session.flush()
    return len(chunks)


def index_chunks(session: Session, embedder: Embedder, *, batch_size: int = 64) -> int:
    """Chunk and embed every full-text paper lacking chunks; returns chunks written."""
    written = 0
    for paper_id, full_text in papers_missing_chunks(session, embedder.model_id):
        chunks = chunk_text(full_text)
        if not chunks:
            continue
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = embedder.embed_documents([chunk.text for chunk in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                session.add(
                    PaperChunkRow(
                        paper_id=paper_id,
                        model_id=embedder.model_id,
                        chunk_index=chunk.index,
                        section=chunk.section,
                        text=chunk.text,
                        embedding=vector,
                    )
                )
                written += 1
        session.flush()
    return written


def search_chunks(
    session: Session,
    query_vector: list[float],
    *,
    model_id: str,
    k: int = 10,
    where: ColumnElement[bool] | None = None,
    probe: int = 40,
) -> list[tuple[str, float]]:
    """Up to ``k`` (paper_id, best cosine distance) pairs from chunk-level ANN.

    ``probe`` chunk hits pool down to papers (several chunks of one paper often match), so it
    stays a few times larger than ``k``.
    """
    _configure_scan(session)
    distance = PaperChunkRow.embedding.cosine_distance(query_vector)
    stmt = select(PaperChunkRow.paper_id, distance.label("distance")).where(
        PaperChunkRow.model_id == model_id
    )
    if where is not None:
        stmt = stmt.join(PaperRow, PaperRow.id == PaperChunkRow.paper_id).where(where)
    stmt = stmt.order_by(distance).limit(max(probe, k))
    best: dict[str, float] = {}
    for paper_id, dist in session.execute(stmt):
        value = float(dist)
        if paper_id not in best or value < best[paper_id]:
            best[paper_id] = value
    pooled = sorted(best.items(), key=lambda item: item[1])
    return pooled[:k]


def best_chunk_texts(
    session: Session,
    query_vector: list[float],
    paper_ids: list[str],
    *,
    model_id: str,
) -> dict[str, str]:
    """The single closest chunk text per requested paper (for quoting in answers)."""
    if not paper_ids:
        return {}
    distance = PaperChunkRow.embedding.cosine_distance(query_vector)
    stmt = (
        select(PaperChunkRow.paper_id, PaperChunkRow.text, distance.label("distance"))
        .where(PaperChunkRow.model_id == model_id, PaperChunkRow.paper_id.in_(paper_ids))
        .order_by(distance)
    )
    best: dict[str, str] = {}
    for paper_id, text, _dist in session.execute(stmt):
        if paper_id not in best:
            best[paper_id] = text
        if len(best) == len(paper_ids):
            break
    return best
