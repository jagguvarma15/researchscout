from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.facets import PaperFacets
from researchscout.store.models import PaperRow
from researchscout.store.papers import (
    count_papers,
    find_by_external_id,
    get_paper,
    link_external_ids,
    list_papers,
    set_citation_count,
    upsert_paper,
)
from researchscout.store.raw import append_raw

pytestmark = pytest.mark.integration


def _paper(
    pid: str = "arxiv:2401.00001",
    arxiv: str = "2401.00001",
    title: str = "T",
    *,
    categories: list[str] | None = None,
    primary_category: str | None = None,
    venue: str | None = None,
    author: str = "Jane Doe",
    published_at: datetime | None = None,
) -> Paper:
    cats = categories or ["cs.LG"]
    return Paper(
        id=pid,
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="An abstract.",
        authors=[Author(name=author)],
        categories=cats,
        primary_category=primary_category or cats[0],
        venue=venue,
        published_at=published_at or datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(PaperRow)).scalar_one()


def test_upsert_is_idempotent(session: Session) -> None:
    upsert_paper(session, _paper(title="First"))
    upsert_paper(session, _paper(title="Second"))  # same id, updated content
    session.flush()

    assert _count(session) == 1
    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.title == "Second"  # upsert updated in place
    assert got.external_ids == {"arxiv": "2401.00001"}
    assert [a.name for a in got.authors] == ["Jane Doe"]


def test_external_ids_collapse_to_one_paper(session: Session) -> None:
    upsert_paper(session, _paper())
    link_external_ids(session, "arxiv:2401.00001", {"doi": "10.1000/xyz"})
    session.flush()

    assert find_by_external_id(session, "arxiv", "2401.00001") == "arxiv:2401.00001"
    assert find_by_external_id(session, "doi", "10.1000/xyz") == "arxiv:2401.00001"
    assert _count(session) == 1


def test_find_by_external_id_missing_returns_none(session: Session) -> None:
    assert find_by_external_id(session, "arxiv", "9999.99999") is None


def test_facet_fields_round_trip(session: Session) -> None:
    paper = _paper()
    paper = paper.model_copy(
        update={
            "primary_category": "cs.LG",
            "comment": "9 pages, accepted at NeurIPS 2025",
            "venue": "NeurIPS 2025",
        }
    )
    upsert_paper(session, paper)
    session.flush()

    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.primary_category == "cs.LG"
    assert got.comment == "9 pages, accepted at NeurIPS 2025"
    assert got.venue == "NeurIPS 2025"
    assert got.citation_count == 0


def test_citation_count_survives_reupsert(session: Session) -> None:
    upsert_paper(session, _paper(title="First"))
    set_citation_count(session, "arxiv:2401.00001", 5)
    upsert_paper(session, _paper(title="Second"))  # content re-ingest
    session.flush()

    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.title == "Second"
    assert got.citation_count == 5  # materialized state untouched by the conflict update


def _seed_mixed(session: Session) -> None:
    upsert_paper(
        session,
        _paper(
            "arxiv:2401.00001",
            "2401.00001",
            "Tech Jan",
            categories=["cs.LG"],
            author="Ada Lovelace",
            published_at=datetime(2024, 1, 15, tzinfo=UTC),
        ),
    )
    upsert_paper(
        session,
        _paper(
            "arxiv:2402.00002",
            "2402.00002",
            "Tech Feb",
            categories=["stat.ML"],
            venue="NeurIPS 2024",
            published_at=datetime(2024, 2, 15, tzinfo=UTC),
        ),
    )
    upsert_paper(
        session,
        _paper(
            "arxiv:2401.00003",
            "2401.00003",
            "Physics Jan",
            categories=["hep-th"],
            published_at=datetime(2024, 1, 20, tzinfo=UTC),
        ),
    )
    upsert_paper(
        session,
        _paper(
            "arxiv:2501.00004",
            "2501.00004",
            "Math 2025",
            categories=["math.OC"],
            published_at=datetime(2025, 1, 5, tzinfo=UTC),
        ),
    )
    session.flush()


def _ids(papers: list[Paper]) -> list[str]:
    return [paper.id for paper in papers]


def test_kind_facet_partitions(session: Session) -> None:
    _seed_mixed(session)
    tech = list_papers(session, facets=PaperFacets(kind="tech"))
    non_tech = list_papers(session, facets=PaperFacets(kind="non_tech"))
    assert set(_ids(tech)) == {"arxiv:2401.00001", "arxiv:2402.00002"}
    assert set(_ids(non_tech)) == {"arxiv:2401.00003", "arxiv:2501.00004"}
    assert count_papers(session, PaperFacets(kind="tech")) == 2


def test_group_facet_intersects_kind(session: Session) -> None:
    _seed_mixed(session)
    physics = list_papers(session, facets=PaperFacets(groups=["physics"]))
    assert _ids(physics) == ["arxiv:2401.00003"]
    empty = list_papers(session, facets=PaperFacets(kind="tech", groups=["physics"]))
    assert empty == []


def test_year_month_window(session: Session) -> None:
    _seed_mixed(session)
    jan = list_papers(session, facets=PaperFacets(year=2024, month=1))
    assert set(_ids(jan)) == {"arxiv:2401.00001", "arxiv:2401.00003"}
    year_2025 = list_papers(session, facets=PaperFacets(year=2025))
    assert _ids(year_2025) == ["arxiv:2501.00004"]


def test_author_and_venue_facets(session: Session) -> None:
    _seed_mixed(session)
    by_author = list_papers(session, facets=PaperFacets(author="lovelace"))
    assert _ids(by_author) == ["arxiv:2401.00001"]
    by_venue = list_papers(session, facets=PaperFacets(venue="neurips"))
    assert _ids(by_venue) == ["arxiv:2402.00002"]


def test_citation_facet_and_sort(session: Session) -> None:
    _seed_mixed(session)
    set_citation_count(session, "arxiv:2401.00003", 50)
    set_citation_count(session, "arxiv:2401.00001", 5)
    session.flush()

    cited = list_papers(session, facets=PaperFacets(min_citations=5))
    assert set(_ids(cited)) == {"arxiv:2401.00001", "arxiv:2401.00003"}

    ranked = list_papers(session, sort="citations", limit=2)
    assert _ids(ranked) == ["arxiv:2401.00003", "arxiv:2401.00001"]


def test_newest_sort_and_offset(session: Session) -> None:
    _seed_mixed(session)
    page1 = list_papers(session, limit=2)
    page2 = list_papers(session, limit=2, offset=2)
    assert _ids(page1) == ["arxiv:2501.00004", "arxiv:2402.00002"]
    assert _ids(page2) == ["arxiv:2401.00003", "arxiv:2401.00001"]


def test_legacy_kwargs_still_filter(session: Session) -> None:
    _seed_mixed(session)
    by_category = list_papers(session, categories=["hep-th"])
    assert _ids(by_category) == ["arxiv:2401.00003"]


def test_raw_append_returns_id(session: Session) -> None:
    rid = append_raw(
        session,
        source="arxiv",
        fetched_at=datetime(2024, 1, 1, tzinfo=UTC),
        payload={"id": "http://arxiv.org/abs/2401.00001"},
        external_id="2401.00001",
    )
    assert rid > 0
