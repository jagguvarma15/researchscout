"""Aggregate per-paper keyword lists into a corpus-wide dictionary.

The chat drawer loads the dictionary once per open to pattern-match prompts against
what the corpus actually contains. The aggregation is a plain uncached read: a few
thousand rows of at most six keywords each is milliseconds of work, and a cache would
only add a staleness knob nobody needs locally.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.store.models import PaperRow


def merge_keywords(rows: Iterable[list[str] | None]) -> Counter[str]:
    """Casefolded keyword counts, each paper counted at most once per keyword.

    The statistical extractor lowercases its phrases but the LLM fallback does not,
    so casefolding here is what makes "Mixture of Experts" and "mixture of experts"
    one dictionary entry.
    """
    counts: Counter[str] = Counter()
    for keywords in rows:
        if not keywords:
            continue
        counts.update({cleaned for keyword in keywords if (cleaned := keyword.strip().casefold())})
    return counts


def keyword_counts(session: Session, *, limit: int = 500) -> tuple[list[tuple[str, int]], int]:
    """The top ``limit`` keywords by paper count plus the distinct-keyword total.

    Ties break alphabetically so the ranking is deterministic.
    """
    rows = session.scalars(select(PaperRow.keywords).where(PaperRow.keywords.is_not(None)))
    counts = merge_keywords(rows)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit], len(counts)
