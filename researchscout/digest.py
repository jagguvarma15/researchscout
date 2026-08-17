"""Weekly digests: rank the window's papers by freshness and citation buzz, then summarize.

The summary follows the same grounded-citation contract as :func:`researchscout.answer.answer`:
the model may cite only the ranked papers, and any invented id is dropped by the post-check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from researchscout.answer import _CITATION_RE
from researchscout.llm.base import LLM
from researchscout.schema import Paper
from researchscout.score import breakthrough
from researchscout.store.papers import list_papers
from researchscout.store.signals import latest_value
from researchscout.trace import trace_span

_SYSTEM_PROMPT = (
    "You are writing a weekly research digest for a reader deciding what to read. "
    "Summarize the papers provided below, leading with the most important work. "
    "For every claim, cite the paper id in square brackets, e.g. [arxiv:2401.12345]. "
    "Never invent ids or facts."
)

_HALF_LIFE_DAYS = 14.0
_CANDIDATE_POOL = 200


@dataclass
class RankedPaper:
    paper: Paper
    score: float
    citations: float


@dataclass
class Digest:
    slug: str
    title: str
    period_start: datetime
    period_end: datetime
    body: str
    cited: list[str]
    items: list[RankedPaper]


def week_slug(end: datetime) -> str:
    """ISO-week slug, e.g. ``2026-w27`` — one digest per week, re-runs replace it."""
    iso = end.isocalendar()
    return f"{iso.year}-w{iso.week:02d}"


def _latest_citations(session: Session, paper_id: str) -> float:
    """The most recent cumulative citation count observed for a paper (0 when unobserved)."""
    return latest_value(session, paper_id, "citation")


def _breakthrough_boost(session: Session, paper_id: str) -> float:
    """The paper's momentum-aware ranking boost (a seam so tests can stub the score)."""
    return breakthrough(session, paper_id).total


def rank_window(session: Session, *, days: int = 7, k: int = 10) -> list[RankedPaper]:
    """Top ``k`` papers of the window: recency-decayed, breakthrough-boosted."""
    now = datetime.now(UTC)
    ranked: list[RankedPaper] = []
    for paper in list_papers(session, days=days, limit=_CANDIDATE_POOL):
        citations = _latest_citations(session, paper.id)
        boost = _breakthrough_boost(session, paper.id)
        age_days = max((now - paper.published_at).total_seconds() / 86400.0, 0.0)
        score = math.exp(-age_days / _HALF_LIFE_DAYS) * (1.0 + boost)
        ranked.append(RankedPaper(paper=paper, score=score, citations=citations))
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


def compose_digest(
    llm: LLM, items: list[RankedPaper], *, start: datetime, end: datetime
) -> Digest:
    """The LLM half: synthesize the digest body from already-ranked papers (no session)."""
    with trace_span("digest", k=len(items)) as span:
        context = "\n\n".join(
            f"[{item.paper.id}] {item.paper.title}\n{item.paper.abstract}" for item in items
        )
        user_prompt = f"Digest window: {start:%Y-%m-%d} to {end:%Y-%m-%d}\n\nPapers:\n{context}"
        body = llm.complete(_SYSTEM_PROMPT, user_prompt)
        span["model"] = llm.model

        found = list(dict.fromkeys(_CITATION_RE.findall(body)))
        valid = {item.paper.id for item in items}
        cited = [cid for cid in found if cid in valid]
        span["cited"] = len(cited)

        slug = week_slug(end)
        return Digest(
            slug=slug,
            title=f"Research radar, week {slug.split('-w')[1]} {end.year}",
            period_start=start,
            period_end=end,
            body=body,
            cited=cited,
            items=items,
        )


def build_digest(
    session: Session,
    llm: LLM,
    *,
    days: int = 7,
    k: int = 10,
) -> Digest | None:
    """Rank the window and synthesize the digest; None when the window is empty."""
    items, start, end = rank_digest(session, days=days, k=k)
    if not items:
        return None
    return compose_digest(llm, items, start=start, end=end)
