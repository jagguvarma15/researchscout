"""The keyword facet over real rows: JSONB any-element matching through list_papers."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.facets import PaperFacets
from researchscout.store.papers import count_papers, list_papers, upsert_paper

pytestmark = pytest.mark.integration


def _paper(arxiv: str, keywords: list[str] | None) -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=f"T {arxiv}",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime.now(UTC) - timedelta(days=1),
        source="arxiv",
        keywords=keywords,
    )


def test_keyword_facet_matches_any_listed_phrase(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001", ["sparse attention", "long context"]))
    upsert_paper(session, _paper("2401.00002", ["state space models"]))
    upsert_paper(session, _paper("2401.00003", None))
    session.commit()

    one = list_papers(session, facets=PaperFacets(keywords=["sparse attention"]))
    assert [paper.id for paper in one] == ["arxiv:2401.00001"]

    # Values within the axis widen, like categories.
    either = PaperFacets(keywords=["sparse attention", "state space models"])
    assert {paper.id for paper in list_papers(session, facets=either)} == {
        "arxiv:2401.00001",
        "arxiv:2401.00002",
    }
    assert count_papers(session, either) == 2


def test_keyword_facet_narrows_against_other_axes(session: Session) -> None:
    old = _paper("2401.00001", ["sparse attention"])
    old = old.model_copy(update={"published_at": datetime.now(UTC) - timedelta(days=90)})
    upsert_paper(session, old)
    upsert_paper(session, _paper("2401.00002", ["sparse attention"]))
    session.commit()

    fresh = list_papers(session, facets=PaperFacets(days=7, keywords=["sparse attention"]))
    assert [paper.id for paper in fresh] == ["arxiv:2401.00002"]
