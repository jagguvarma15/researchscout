"""Daily reports: what arrived in the last day, where it moved, and the must-read five.

Reuses the digest store and pages under a daily slug (2026-07-30, disjoint from the weekly
2026-w31 namespace). The body is deterministic markdown, so the daily run never depends on
the LLM being up; the must-read ranking is the breakthrough momentum score, which already
blends citation level, velocity, and acceleration with the popularity signals.

"Arrived" means stored in the last day (``created_at``), not published: arXiv's published_at
is submission time, a day or more behind the announcement that actually lands a paper here,
and a report windowed on it was empty on almost every real day. Weekend reports still come
up empty legitimately - arXiv announces Sunday through Thursday evenings - and an empty
window publishes nothing rather than a hollow page.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from researchscout.digest import Digest, RankedPaper
from researchscout.score import breakthrough_many
from researchscout.store.papers import papers_arrived_since
from researchscout.store.topics import list_topics
from researchscout.taxonomy import group_for

_WINDOW_HOURS = 24
_CANDIDATE_POOL = 500
_MUST_READ = 5
_MOVEMENTS_SHOWN = 8
_MOVING_TRENDS = ("new", "rising", "fading")


def day_slug(moment: datetime) -> str:
    """Daily slug, e.g. ``2026-07-30`` — one report per day, re-runs replace it."""
    return f"{moment:%Y-%m-%d}"


def build_daily_report(session: Session, *, now: datetime | None = None) -> Digest | None:
    """The day's report, or None when nothing arrived in the window."""
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=_WINDOW_HOURS)
    papers = papers_arrived_since(session, since, limit=_CANDIDATE_POOL)
    if not papers:
        return None

    scores = breakthrough_many(session, [paper.id for paper in papers])
    ranked = [
        RankedPaper(
            paper=paper,
            score=scores[paper.id].total,
            citations=float(paper.citation_count),
        )
        for paper in papers
    ]
    # Momentum first; recency breaks the ties a fresh day of zero-signal papers produces.
    ranked.sort(key=lambda item: (item.score, item.paper.published_at), reverse=True)
    must_read = ranked[:_MUST_READ]

    groups = Counter(
        group.label if (group := group_for(paper.primary_category)) else "Other" for paper in papers
    )
    lines = [f"{len(papers)} papers arrived in the last {_WINDOW_HOURS} hours."]
    lines.append("")
    lines.append(
        "Volume by area: " + ", ".join(f"{label} {count}" for label, count in groups.most_common())
    )

    movements = [topic for topic in list_topics(session) if topic.trend in _MOVING_TRENDS]
    if movements:
        lines.append("")
        lines.append("Topic movements:")
        lines.extend(
            f"- {topic.label}: {topic.trend} (size {topic.size})"
            for topic in movements[:_MOVEMENTS_SHOWN]
        )

    lines.append("")
    lines.append("Must read today:")
    lines.extend(
        f"{index}. [{item.paper.id}] {item.paper.title}"
        for index, item in enumerate(must_read, start=1)
    )

    return Digest(
        slug=day_slug(now),
        title=f"Daily report {day_slug(now)}",
        period_start=now - timedelta(hours=_WINDOW_HOURS),
        period_end=now,
        body="\n".join(lines),
        cited=[item.paper.id for item in must_read],
        items=must_read,
    )
