"""pgvector adapter: store paper embeddings and run cosine ANN search."""

from __future__ import annotations

from sqlalchemy import ColumnElement, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.store.models import PaperEmbeddingRow, PaperRow


def papers_missing_embedding(session: Session, model_id: str) -> list[PaperRow]:
    """Papers that have no embedding for ``model_id`` yet."""
    have = select(PaperEmbeddingRow.paper_id).where(PaperEmbeddingRow.model_id == model_id)
    return list(session.execute(select(PaperRow).where(PaperRow.id.not_in(have))).scalars())


def upsert_embedding(session: Session, paper_id: str, model_id: str, vector: list[float]) -> None:
    """Insert or replace one paper's embedding for a given model."""
    stmt = insert(PaperEmbeddingRow).values(paper_id=paper_id, model_id=model_id, embedding=vector)
    stmt = stmt.on_conflict_do_update(
        index_elements=["paper_id", "model_id"], set_={"embedding": vector}
    )
    session.execute(stmt)


def index_papers(session: Session, embedder: Embedder, *, batch_size: int = 64) -> int:
    """Embed papers lacking a vector for the embedder's model; return how many were embedded."""
    pending = papers_missing_embedding(session, embedder.model_id)
    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        texts = [f"{paper.title}\n\n{paper.abstract}" for paper in batch]
        vectors = embedder.embed_documents(texts)
        for paper, vector in zip(batch, vectors, strict=True):
            upsert_embedding(session, paper.id, embedder.model_id, vector)
            embedded += 1
        session.flush()
    return embedded


def _configure_scan(session: Session) -> None:
    """Widen and iterate the HNSW scan so filtered queries still fill their candidate pool.

    Post-filtered HNSW returns ``ef_search`` candidates BEFORE the WHERE clause, so a selective
    filter (and retrieval always filters, via the freshness window) can starve the pool.
    Iterative scans (pgvector >= 0.8) keep pulling until the LIMIT is satisfied. SET LOCAL
    scopes both knobs to the current transaction; on older pgvector the unknown parameter is a
    harmless placeholder.
    """
    session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))


def search(
    session: Session,
    query_vector: list[float],
    *,
    model_id: str,
    k: int = 10,
    where: ColumnElement[bool] | None = None,
) -> list[tuple[str, float]]:
    """Return up to ``k`` (paper_id, cosine_distance) pairs nearest to the query vector.

    Only vectors from ``model_id`` are searched: distances from different embedding spaces are
    not comparable, so mixing models would silently corrupt the ranking.
    """
    _configure_scan(session)
    distance = PaperEmbeddingRow.embedding.cosine_distance(query_vector)
    stmt = select(PaperEmbeddingRow.paper_id, distance.label("distance")).where(
        PaperEmbeddingRow.model_id == model_id
    )
    if where is not None:
        stmt = stmt.join(PaperRow, PaperRow.id == PaperEmbeddingRow.paper_id).where(where)
    stmt = stmt.order_by(distance).limit(k)
    rows = [(paper_id, float(dist)) for paper_id, dist in session.execute(stmt)]
    # relaxed_order may emit slightly out-of-distance-order rows; re-sort so ranks are exact.
    rows.sort(key=lambda item: item[1])
    return rows
