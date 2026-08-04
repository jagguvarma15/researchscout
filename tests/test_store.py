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
    set_full_text,
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


def test_reupsert_preserves_full_text(session: Session) -> None:
    upsert_paper(session, _paper(title="First"))
    set_full_text(session, "arxiv:2401.00001", "## Introduction\n\nBody text.")
    upsert_paper(session, _paper(title="Refreshed"))  # a re-ingest carries full_text=None
    session.flush()

    row = session.get(PaperRow, "arxiv:2401.00001")
    assert row is not None
    assert row.title == "Refreshed"
    assert row.full_text == "## Introduction\n\nBody text."


def test_listing_leaves_the_article_text_in_the_database(session: Session) -> None:
    """Averaging 18 kB a row, it was over 90% of what a feed page fetched and used none of.

    Pinned as behaviour rather than left to a comment: reinstating the column would be a
    one-word change and nothing else in the suite would notice.
    """
    from researchscout.store.papers import get_papers

    upsert_paper(session, _paper())
    set_full_text(session, "arxiv:2401.00001", "## Introduction\n\nBody text.")
    session.flush()

    assert list_papers(session)[0].full_text is None
    assert get_papers(session, ["arxiv:2401.00001"])["arxiv:2401.00001"].full_text is None
    # The detail read is the one that hands back a whole paper.
    detail = get_paper(session, "arxiv:2401.00001")
    assert detail is not None and detail.full_text == "## Introduction\n\nBody text."


def test_enrichment_round_trips_and_survives_reupsert(session: Session) -> None:
    from researchscout.schema import PaperLabel
    from researchscout.store.papers import set_enrichment

    upsert_paper(session, _paper(title="First"))
    set_enrichment(
        session,
        "arxiv:2401.00001",
        keywords=["state space models", "long context"],
        labels=[PaperLabel(label="efficiency", source="custom", score=0.9)],
    )
    set_enrichment(session, "arxiv:2401.00001", sections=["Introduction", "Method"])
    upsert_paper(session, _paper(title="Refreshed"))  # a re-ingest carries no enrichment
    session.flush()

    got = get_paper(session, "arxiv:2401.00001")
    assert got is not None
    assert got.title == "Refreshed"
    assert got.keywords == ["state space models", "long context"]  # partial update kept these
    assert got.sections == ["Introduction", "Method"]
    assert got.labels == [PaperLabel(label="efficiency", source="custom", score=0.9)]


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


def test_subject_facet_selects_a_field(session: Session) -> None:
    _seed_mixed(session)
    ai = list_papers(session, facets=PaperFacets(subjects=["ai"]))
    assert set(_ids(ai)) == {"arxiv:2401.00001", "arxiv:2402.00002"}
    assert count_papers(session, PaperFacets(subjects=["ai"])) == 2
    # Archive-defined subjects go through the paper_archives index rather than the code list.
    assert _ids(list_papers(session, facets=PaperFacets(subjects=["math"]))) == ["arxiv:2501.00004"]
    assert _ids(list_papers(session, facets=PaperFacets(subjects=["physical"]))) == [
        "arxiv:2401.00003"
    ]


def test_subjects_within_the_axis_widen(session: Session) -> None:
    _seed_mixed(session)
    both = list_papers(session, facets=PaperFacets(subjects=["math", "physical"]))
    assert set(_ids(both)) == {"arxiv:2401.00003", "arxiv:2501.00004"}


def test_an_unknown_subject_matches_nothing(session: Session) -> None:
    # The API rejects these before they reach here; the compiler must not fall back to
    # matching everything if one ever slips through.
    _seed_mixed(session)
    assert list_papers(session, facets=PaperFacets(subjects=["notreal"])) == []


def test_full_text_batch_prioritizes_and_skips_checked(session: Session) -> None:
    from researchscout.store.papers import papers_missing_full_text, set_full_text

    for pid, arxiv, title, month in [
        ("arxiv:2404.00001", "2404.00001", "Newest", 4),
        ("arxiv:2403.00002", "2403.00002", "Saved", 3),
        ("arxiv:2402.00003", "2402.00003", "Checked", 2),
    ]:
        upsert_paper(
            session,
            _paper(pid, arxiv, title, published_at=datetime(2024, month, 1, tzinfo=UTC)),
        )
    session.flush()
    set_full_text(session, "arxiv:2402.00003", "")  # checked: no HTML available

    pending = papers_missing_full_text(session, limit=10, first=["arxiv:2403.00002"])
    assert [paper_id for paper_id, _ in pending] == ["arxiv:2403.00002", "arxiv:2404.00001"]

    set_full_text(session, "arxiv:2403.00002", "## S\n\nbody")
    pending = papers_missing_full_text(session, limit=10)
    assert [paper_id for paper_id, _ in pending] == ["arxiv:2404.00001"]


def test_subjects_read_the_whole_category_list(session: Session) -> None:
    _seed_mixed(session)
    upsert_paper(
        session,
        _paper(
            "arxiv:2502.00005",
            "2502.00005",
            "Crosslisted OC",
            categories=["math.OC", "cs.LG"],
            primary_category="math.OC",
            published_at=datetime(2025, 2, 1, tzinfo=UTC),
        ),
    )
    session.flush()

    # A cross-list puts one paper in two subjects, which is the point of overlapping lenses.
    assert "arxiv:2502.00005" in _ids(list_papers(session, facets=PaperFacets(subjects=["ai"])))
    assert "arxiv:2502.00005" in _ids(list_papers(session, facets=PaperFacets(subjects=["math"])))
    # The axes narrow each other: AI papers that are also physics is nothing here.
    assert list_papers(session, facets=PaperFacets(subjects=["ai"], topics=["rl"])) == []


def test_topic_facet_matches_by_category_or_by_phrase(session: Session) -> None:
    for pid, arxiv, title, cats, abstract in [
        ("arxiv:2601.00001", "2601.00001", "Translating", ["cs.CL"], "An abstract."),
        ("arxiv:2601.00002", "2601.00002", "Segmenting", ["cs.CV"], "An abstract."),
        ("arxiv:2601.00003", "2601.00003", "Control", ["cs.LG"], "We use reinforcement learning."),
        ("arxiv:2601.00004", "2601.00004", "Kernels", ["cs.LG"], "An abstract."),
    ]:
        paper = _paper(pid, arxiv, title, categories=cats)
        upsert_paper(session, paper.model_copy(update={"abstract": abstract}))
    session.flush()

    assert _ids(list_papers(session, facets=PaperFacets(topics=["nlp"]))) == ["arxiv:2601.00001"]
    assert _ids(list_papers(session, facets=PaperFacets(topics=["cv"]))) == ["arxiv:2601.00002"]
    # arXiv has no RL category, so this one is found in the text and the plain cs.LG paper is not.
    assert _ids(list_papers(session, facets=PaperFacets(topics=["rl"]))) == ["arxiv:2601.00003"]
    both = list_papers(session, facets=PaperFacets(topics=["nlp", "rl"]))
    assert set(_ids(both)) == {"arxiv:2601.00001", "arxiv:2601.00003"}


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
