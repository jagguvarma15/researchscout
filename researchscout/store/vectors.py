"""pgvector adapter: store paper embeddings and run cosine ANN search."""

from __future__ import annotations

from sqlalchemy import ColumnElement, select
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


def search(
    session: Session,
    query_vector: list[float],
    *,
    k: int = 10,
    where: ColumnElement[bool] | None = None,
) -> list[tuple[str, float]]:
    """Return up to ``k`` (paper_id, cosine_distance) pairs nearest to the query vector."""
    distance = PaperEmbeddingRow.embedding.cosine_distance(query_vector)
    stmt = select(PaperEmbeddingRow.paper_id, distance.label("distance"))
    if where is not None:
        stmt = stmt.join(PaperRow, PaperRow.id == PaperEmbeddingRow.paper_id).where(where)
    stmt = stmt.order_by(distance).limit(k)
    return [(paper_id, float(dist)) for paper_id, dist in session.execute(stmt)]
