"""The rolling corpus window: papers older than the retention period are removed.

``RS_RETENTION_DAYS`` (0 = off, keep everything) sets the window, measured on the arXiv
submission date (``papers.published_at``). A paper a reader saved or highlighted is exempt
and stays until they let it go. Everything hanging off a paper — embeddings, chunks,
external ids, signals, citation edges and watermarks, events, recents, dismissals — goes
with it through the schema's ``ON DELETE CASCADE`` keys; catalogue models only lose their
link (``SET NULL``). Digest issues whose period ended before the cutoff are removed in the
same pass, because their paper links would otherwise lead nowhere.

The ingest side keeps the window closed: ``within_retention`` is what stops the revisions
sweep and the trending auto-import from landing papers the next purge would delete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, delete, exists, func, select
from sqlalchemy.orm import Session

from researchscout.config import Settings
from researchscout.store.models import DigestRow, PaperRow, SavedPaperRow, UserHighlightRow

#: Papers per delete transaction. Each paper cascades into a few dozen child rows (chunks
#: dominate), so this keeps one transaction's locks and WAL bounded on the first purge.
_BATCH = 2000


@dataclass(frozen=True)
class PurgeSummary:
    papers: int
    digests: int
    exempt: int

    def note(self) -> str:
        return (
            f"{self.papers} paper(s), {self.digests} digest(s) removed; "
            f"{self.exempt} saved paper(s) kept"
        )


def retention_cutoff(settings: Settings, now: datetime | None = None) -> datetime | None:
    """The oldest submission date the corpus keeps, or None when retention is off."""
    if settings.retention_days <= 0:
        return None
    return (now or datetime.now(UTC)) - timedelta(days=settings.retention_days)


def within_retention(
    published_at: datetime, settings: Settings, now: datetime | None = None
) -> bool:
    """Would a paper submitted at ``published_at`` survive the next purge?"""
    cutoff = retention_cutoff(settings, now)
    return cutoff is None or published_at >= cutoff


def effective_window_days(days: int, settings: Settings) -> int:
    """A look-back clamped to the retention window: nothing older exists to look at."""
    if settings.retention_days <= 0:
        return days
    return min(days, settings.retention_days)


def _expired(cutoff: datetime) -> ColumnElement[bool]:
    """Papers past the cutoff that no reader is keeping."""
    kept = exists().where(SavedPaperRow.paper_id == PaperRow.id) | exists().where(
        UserHighlightRow.paper_id == PaperRow.id
    )
    return (PaperRow.published_at < cutoff) & ~kept


def count_expired(session: Session, cutoff: datetime) -> PurgeSummary:
    """What a purge at ``cutoff`` would remove, without removing it (the dry run)."""
    papers = session.execute(
        select(func.count()).select_from(PaperRow).where(_expired(cutoff))
    ).scalar_one()
    exempt = session.execute(
        select(func.count())
        .select_from(PaperRow)
        .where(PaperRow.published_at < cutoff, ~_expired(cutoff))
    ).scalar_one()
    digests = session.execute(
        select(func.count()).select_from(DigestRow).where(DigestRow.period_end < cutoff)
    ).scalar_one()
    return PurgeSummary(papers=papers, digests=digests, exempt=exempt)


def purge_expired(session: Session, cutoff: datetime, *, batch: int = _BATCH) -> PurgeSummary:
    """Delete expired papers in committed batches, then the digest issues they fed.

    Commits per batch so the first purge of a years-deep corpus never holds one giant
    transaction; an interruption keeps the batches already done and the next run finishes.
    """
    exempt = session.execute(
        select(func.count())
        .select_from(PaperRow)
        .where(PaperRow.published_at < cutoff, ~_expired(cutoff))
    ).scalar_one()
    removed = 0
    while True:
        ids = list(
            session.execute(
                select(PaperRow.id).where(_expired(cutoff)).order_by(PaperRow.id).limit(batch)
            ).scalars()
        )
        if not ids:
            break
        session.execute(delete(PaperRow).where(PaperRow.id.in_(ids)))
        session.commit()
        removed += len(ids)
    digests = session.execute(
        delete(DigestRow).where(DigestRow.period_end < cutoff).returning(DigestRow.slug)
    ).all()
    session.commit()
    return PurgeSummary(papers=removed, digests=len(digests), exempt=exempt)
