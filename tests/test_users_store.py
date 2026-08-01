from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store.events import EventInput, append_events
from researchscout.store.interests import set_interests
from researchscout.store.models import (
    EventRow,
    PaperRow,
    SavedPaperRow,
    UserInterestRow,
    UserRow,
)
from researchscout.store.papers import upsert_paper
from researchscout.store.saved import save_paper
from researchscout.store.users import (
    accept_terms,
    delete_user,
    export_user_data,
    get_user,
    upsert_user,
)

_SUB = "auth0|abc"
_PAPER_ID = "arxiv:2401.00001"


def _paper() -> Paper:
    return Paper(
        id=_PAPER_ID,
        title="T",
        abstract="An abstract.",
        authors=[Author(name="Jane Doe")],
        categories=["cs.LG"],
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        source="arxiv",
    )


def _populate(session: Session) -> None:
    upsert_paper(session, _paper())
    upsert_user(session, _SUB, email="a@example.com")
    accept_terms(session, _SUB, "2026-08-01")
    save_paper(session, _SUB, _PAPER_ID)
    set_interests(session, _SUB, ["sparse attention"])
    append_events(session, _SUB, [EventInput(event="click", paper_id=_PAPER_ID)])
    session.flush()


@pytest.mark.integration
def test_upsert_creates_then_keeps_claims(session: Session) -> None:
    upsert_user(session, _SUB, email="a@example.com", display_name="Ada")
    session.flush()
    user = get_user(session, _SUB)
    assert user is not None
    assert (user.email, user.display_name) == ("a@example.com", "Ada")

    # A later visit inside the last-seen window must not blank the claims it did not send.
    upsert_user(session, _SUB)
    session.flush()
    session.refresh(user)
    assert (user.email, user.display_name) == ("a@example.com", "Ada")


@pytest.mark.integration
def test_accept_terms_records_version_and_moves_with_it(session: Session) -> None:
    upsert_user(session, _SUB)
    user = accept_terms(session, _SUB, "2026-08-01")
    assert user.tos_version == "2026-08-01"
    assert user.tos_accepted_at is not None

    assert accept_terms(session, _SUB, "2027-01-01").tos_version == "2027-01-01"


@pytest.mark.integration
def test_export_returns_everything_owned(session: Session) -> None:
    _populate(session)

    data = export_user_data(session, _SUB)
    assert data["account"]["email"] == "a@example.com"
    assert data["account"]["terms_version"] == "2026-08-01"
    assert [item["paper_id"] for item in data["saved_papers"]] == [_PAPER_ID]
    assert [item["interest"] for item in data["interests"]] == ["sparse attention"]
    assert [item["event"] for item in data["reading_events"]] == ["click"]


@pytest.mark.integration
def test_delete_takes_every_owned_row_and_leaves_the_corpus(session: Session) -> None:
    _populate(session)

    assert delete_user(session, _SUB) is True
    session.flush()

    # The built-in local user ships with migration 0019 and is not this account.
    assert session.execute(select(UserRow.sub)).scalars().all() == ["local"]
    assert session.execute(select(SavedPaperRow.paper_id)).scalars().all() == []
    assert session.execute(select(UserInterestRow.interest)).scalars().all() == []
    assert session.execute(select(EventRow.id)).scalars().all() == []
    # The paper is corpus, not account data: it stays.
    assert session.get(PaperRow, _PAPER_ID) is not None


@pytest.mark.integration
def test_delete_reports_false_for_an_unknown_account(session: Session) -> None:
    assert delete_user(session, "auth0|never-seen") is False
