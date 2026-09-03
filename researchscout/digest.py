"""Weekly digests: rank the week's arrivals by freshness and citation buzz, then summarize.

The window is arrival (``created_at``), not publication: arXiv's published_at is submission
time, a day or more behind the announcement that actually lands a paper here, and a window
on it under-fills after ingest gaps and weekends - the same reasoning the daily report
documents. Recency decay still reads publication age, so a late-arriving older paper joins
the pool but does not outrank the week's genuinely fresh work.

The summary follows the same grounded-citation contract as :func:`researchscout.answer.answer`:
the model may cite only the ranked papers, and any invented id is dropped by the post-check.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from researchscout.answer import _CITATION_RE
from researchscout.llm.base import LLM
from researchscout.llm.usage import PURPOSE_DIGEST, llm_purpose
from researchscout.schema import Paper
from researchscout.score import breakthrough_many
from researchscout.store.papers import papers_arrived_since
from researchscout.trace import trace_span

_SYSTEM_PROMPT = (
    "You are writing a weekly research digest for a reader deciding what to read. "
    "Summarize the papers provided below, leading with the most important work. "
    "For every claim, cite the paper id in square brackets, e.g. [arxiv:2401.12345]. "
    "Never invent ids or facts."
)

_HALF_LIFE_DAYS = 14.0
# Arrivals come newest-first, so a small cap would silently truncate the week's tail; a
# thousand covers a heavy week and the scoring is one batched query either way.
_CANDIDATE_POOL = 1000

logger = logging.getLogger(__name__)


@dataclass
class RankedPaper:
    paper: Paper
    score: float
    citations: float
    # Per-signal-type breakthrough breakdown - the "why this paper is here" data.
    contributions: dict[str, float] = field(default_factory=dict)


@dataclass
class Digest:
    slug: str
    title: str
    period_start: datetime
    period_end: datetime
    body: str
    cited: list[str]
    items: list[RankedPaper]
    llm_ok: bool = True
    kind: str = "weekly"
    # One deterministic sentence for delivery notices; derived, never persisted.
    summary: str = ""


def week_slug(end: datetime) -> str:
    """ISO-week slug, e.g. ``2026-w27`` — one digest per week, re-runs replace it."""
    iso = end.isocalendar()
    return f"{iso.year}-w{iso.week:02d}"


def rank_window(session: Session, *, days: int = 7, k: int = 10) -> list[RankedPaper]:
    """Top ``k`` of the window's arrivals: recency-decayed, breakthrough-boosted."""
    now = datetime.now(UTC)
    papers = papers_arrived_since(session, now - timedelta(days=days), limit=_CANDIDATE_POOL)
    boosts = breakthrough_many(session, [paper.id for paper in papers])
    ranked: list[RankedPaper] = []
    for paper in papers:
        boost = boosts[paper.id]
        age_days = max((now - paper.published_at).total_seconds() / 86400.0, 0.0)
        score = math.exp(-age_days / _HALF_LIFE_DAYS) * (1.0 + boost.total)
        ranked.append(
            RankedPaper(
                paper=paper,
                score=score,
                citations=float(paper.citation_count),
                contributions=dict(boost.contributions),
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:k]


def rank_digest(
    session: Session, *, days: int = 7, k: int = 10
) -> tuple[list[RankedPaper], datetime, datetime]:
    """The DB half: rank the window and return (items, start, end).

    Split from composition so the caller can close its session before the LLM call — an
    Ollama round-trip must not hold a database transaction open.
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    return rank_window(session, days=days, k=k), start, end


def _fallback_body(items: list[RankedPaper]) -> str:
    """Deterministic stand-in when the model is unavailable: the ranked list, ids citable."""
    lines = ["The digest model was unavailable this week; the window's top papers, ranked:", ""]
    lines.extend(
        f"{index}. [{item.paper.id}] {item.paper.title}" for index, item in enumerate(items, 1)
    )
    return "\n".join(lines)


def compose_digest(llm: LLM, items: list[RankedPaper], *, start: datetime, end: datetime) -> Digest:
    """The LLM half: synthesize the digest body from already-ranked papers (no session)."""
    with trace_span("digest", k=len(items)) as span:
        context = "\n\n".join(
            f"[{item.paper.id}] {item.paper.title}\n{item.paper.abstract}" for item in items
        )
        user_prompt = f"Digest window: {start:%Y-%m-%d} to {end:%Y-%m-%d}\n\nPapers:\n{context}"
        llm_ok = True
        try:
            with llm_purpose(PURPOSE_DIGEST):
                body = llm.complete(_SYSTEM_PROMPT, user_prompt)
        except Exception:  # noqa: BLE001 - the ranked list is the safe floor
            logger.warning("digest prose failed; publishing the ranked list", exc_info=True)
            body = _fallback_body(items)
            llm_ok = False
            span["fallback"] = True
        span["model"] = llm.model

        found = list(dict.fromkeys(_CITATION_RE.findall(body)))
        valid = {item.paper.id for item in items}
        cited = [cid for cid in found if cid in valid]
        span["cited"] = len(cited)

        # The ISO year, not the calendar year: a Dec-31 end can already sit in next year's
        # week 01, and the title must agree with the slug about which year that is.
        iso = end.isocalendar()
        return Digest(
            slug=week_slug(end),
            title=f"Research radar, week {iso.week:02d} {iso.year}",
            period_start=start,
            period_end=end,
            body=body,
            cited=cited,
            items=items,
            llm_ok=llm_ok,
            kind="weekly",
            summary=f"The week's top {len(items)} papers, ranked.",
        )


def build_digest(
    session: Session,
    llm: LLM,
    *,
    days: int = 7,
    k: int = 10,
) -> Digest | None:
    """Rank the window and synthesize the digest; None when the window is empty.

    Convenience for tests only: production callers (scheduler and CLI) use the
    rank_digest/compose_digest split so no session stays open across the LLM round-trip.
    """
    items, start, end = rank_digest(session, days=days, k=k)
    if not items:
        return None
    return compose_digest(llm, items, start=start, end=end)
