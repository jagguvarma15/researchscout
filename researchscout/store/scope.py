"""Finding and removing papers that fall outside what this radar covers.

The scope rule (``researchscout.taxonomy.in_scope``) decides what is stored from now on, and
the arXiv query narrows to the same set so nothing out of scope is fetched at all. This module
is for the corpus that predates it -- papers gathered when the ingest reached across the whole
of arXiv.

Deletion lives behind ``scout db prune-scope`` and never runs from a scheduled task. It is the
one irreversible step in this codebase: the rows go, and their embeddings, chunks and signals go
with them by cascade. ``count_out_of_scope`` exists so you can look before you leap, which is
what ``--dry-run`` does.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, cast, delete, func, not_, select
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy.orm import Session

from researchscout.store.models import PaperRow
from researchscout.taxonomy import SCOPE_ARCHIVES


def _out_of_scope() -> ColumnElement[bool]:
    """Papers touching none of the archives this radar covers.

    Runs against the same GIN expression index the subject filters use (migration 0020), so
    counting is a bitmap scan rather than a walk of the table.
    """
    archives = cast(sorted(SCOPE_ARCHIVES), ARRAY(TEXT))
    overlaps: ColumnElement[bool] = func.paper_archives(PaperRow.categories).op("&&")(archives)
    return not_(overlaps)


def count_out_of_scope(session: Session) -> int:
    """How many stored papers the scope rule would now reject."""
    return session.execute(
        select(func.count()).select_from(PaperRow).where(_out_of_scope())
    ).scalar_one()


def sample_out_of_scope(session: Session, *, limit: int = 10) -> list[tuple[str, str, str | None]]:
    """A handful of (id, title, primary category) rows, so a dry run shows what it means."""
    rows = session.execute(
        select(PaperRow.id, PaperRow.title, PaperRow.primary_category)
        .where(_out_of_scope())
        .order_by(PaperRow.published_at.desc())
        .limit(limit)
    ).all()
    return [(paper_id, title, primary) for paper_id, title, primary in rows]


def delete_out_of_scope(session: Session) -> int:
    """Delete every out-of-scope paper; returns how many went.

    Embeddings, chunks, signals, citation edges, external ids, saved rows and events all carry
    ``ON DELETE CASCADE`` from ``papers``, so this one statement is the whole removal.
    """
    result = session.execute(delete(PaperRow).where(_out_of_scope()))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
