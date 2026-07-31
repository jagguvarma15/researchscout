from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.keywords import keyword_counts, merge_keywords
from researchscout.store.papers import set_enrichment, upsert_paper


def test_merge_keywords_casefolds_and_dedupes_within_a_paper() -> None:
    counts = merge_keywords(
        [
            ["Sparse Attention", "sparse attention", "routing"],
            ["SPARSE ATTENTION"],
            None,
            [],
        ]
    )
    assert counts == {"sparse attention": 2, "routing": 1}


def test_merge_keywords_drops_blank_entries() -> None:
    counts = merge_keywords([["  ", "", "routing "]])
    assert counts == {"routing": 1}


def _paper(pid: str) -> Paper:
    return Paper(
        id=pid,
        title="T",
        abstract="An abstract.",
        authors=[Author(name="Jane Doe")],
        categories=["cs.LG"],
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        source="arxiv",
    )


@pytest.mark.integration
def test_keyword_counts_ranks_by_papers_then_keyword(session: Session) -> None:
    for pid, keywords in (
        ("arxiv:2401.00001", ["Sparse Attention", "routing"]),
        ("arxiv:2401.00002", ["sparse attention"]),
        ("arxiv:2401.00003", None),
    ):
        upsert_paper(session, _paper(pid))
        if keywords is not None:
            set_enrichment(session, pid, keywords=keywords)
    session.flush()

    ranked, total = keyword_counts(session)
    assert ranked == [("sparse attention", 2), ("routing", 1)]
    assert total == 2

    top, total = keyword_counts(session, limit=1)
    assert top == [("sparse attention", 2)]
    assert total == 2
