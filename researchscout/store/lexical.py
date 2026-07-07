"""Full-text search leg over the generated ``papers.search_tsv`` column (migration 0007).

The column weights title lexemes above abstract lexemes, so ``ts_rank_cd`` favours papers whose
titles carry the query terms. ``websearch_to_tsquery`` parses free-form user input (quoted
phrases, ``-`` negation) and never raises on odd input — a query with no usable lexemes simply
matches nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnClause, ColumnElement, func, literal_column, select
from sqlalchemy.orm import Session

from researchscout.store.models import PaperRow

_SEARCH_TSV: ColumnClause[Any] = literal_column("papers.search_tsv")


def lexical_search(
    session: Session,
    query: str,
    *,
    k: int = 10,
    where: ColumnElement[bool] | None = None,
) -> list[tuple[str, float]]:
    """Return up to ``k`` (paper_id, ts_rank_cd) pairs matching the query, best first.

    Returns an empty list when the query yields no tsquery lexemes (stopwords only,
    punctuation, and so on), so callers can treat "nothing lexical" uniformly.
    """
    tsquery = func.websearch_to_tsquery("english", query)
    lexemes = session.execute(select(func.numnode(tsquery))).scalar_one()
    if int(lexemes) == 0:
        return []

    rank = func.ts_rank_cd(_SEARCH_TSV, tsquery)
    stmt = select(PaperRow.id, rank.label("rank")).where(_SEARCH_TSV.op("@@")(tsquery))
    if where is not None:
        stmt = stmt.where(where)
    stmt = stmt.order_by(rank.desc()).limit(k)
    return [(paper_id, float(score)) for paper_id, score in session.execute(stmt)]
