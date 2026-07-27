from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.models import PaperRow
from researchscout.store.papers import (
    find_by_external_id,
    get_paper,
    link_external_ids,
    set_citation_count,
    upsert_paper,
)
from researchscout.store.raw import append_raw

pytestmark = pytest.mark.integration


def _paper(pid: str = "arxiv:2401.00001", arxiv: str = "2401.00001", title: str = "T") -> Paper:
    return Paper(
        id=pid,
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="An abstract.",
        authors=[Author(name="Jane Doe")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
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


def test_raw_append_returns_id(session: Session) -> None:
    rid = append_raw(
        session,
        source="arxiv",
        fetched_at=datetime(2024, 1, 1, tzinfo=UTC),
        payload={"id": "http://arxiv.org/abs/2401.00001"},
        external_id="2401.00001",
    )
    assert rid > 0
