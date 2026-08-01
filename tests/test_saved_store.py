from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.papers import upsert_paper
from researchscout.store.saved import list_saved, save_paper, saved_ids, unsave_paper
from researchscout.store.users import upsert_user

pytestmark = pytest.mark.integration


def _paper(arxiv: str, title: str = "T") -> Paper:
    return Paper(
        id=f"arxiv:{arxiv}",
        external_ids={"arxiv": arxiv},
        title=title,
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def _account(session: Session, sub: str) -> None:
    """Saving requires the account to exist (migration 0019); in the API the authentication
    dependency creates it before any route runs."""
    upsert_user(session, sub)


def test_save_unsave_roundtrip(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    _account(session, "user-1")
    assert save_paper(session, "user-1", "arxiv:2401.00001") is True
    assert save_paper(session, "user-1", "arxiv:2401.00001") is False  # idempotent
    assert [p.id for p in list_saved(session, "user-1")] == ["arxiv:2401.00001"]
    assert unsave_paper(session, "user-1", "arxiv:2401.00001") is True
    assert unsave_paper(session, "user-1", "arxiv:2401.00001") is False
    assert list_saved(session, "user-1") == []


def test_lists_are_per_user(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    upsert_paper(session, _paper("2401.00002"))
    _account(session, "user-1")
    _account(session, "user-2")
    save_paper(session, "user-1", "arxiv:2401.00001")
    save_paper(session, "user-2", "arxiv:2401.00002")
    assert [p.id for p in list_saved(session, "user-1")] == ["arxiv:2401.00001"]
    assert [p.id for p in list_saved(session, "user-2")] == ["arxiv:2401.00002"]


def test_saved_ids_annotates_a_page(session: Session) -> None:
    upsert_paper(session, _paper("2401.00001"))
    upsert_paper(session, _paper("2401.00002"))
    _account(session, "user-1")
    save_paper(session, "user-1", "arxiv:2401.00001")
    page = ["arxiv:2401.00001", "arxiv:2401.00002", "arxiv:9999.99999"]
    assert saved_ids(session, "user-1", page) == {"arxiv:2401.00001"}
    assert saved_ids(session, "user-1", []) == set()
