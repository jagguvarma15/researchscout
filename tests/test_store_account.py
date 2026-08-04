"""Per-account site state: the caps, the de-duplication, and the cascade.

Integration rather than unit, because the parts worth testing are the ones Postgres does: the
upsert that moves a repeated search up instead of duplicating it, the delete-all-but-the-newest
that keeps a cache from becoming a table, and the foreign key that makes account deletion reach
these rows without anything having to name them.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper
from researchscout.store import account
from researchscout.store.models import AccountDismissalRow, AccountSearchRow, UserRow
from researchscout.store.papers import upsert_paper
from researchscout.store.users import delete_user, export_user_data

pytestmark = pytest.mark.integration

SUB = "local"


def _paper(session: Session, pid: str) -> str:
    upsert_paper(
        session,
        Paper(
            id=pid,
            external_ids={"arxiv": pid.removeprefix("arxiv:")},
            title=f"Paper {pid}",
            abstract="An abstract.",
            authors=[Author(name="Jane Doe")],
            categories=["cs.LG"],
            primary_category="cs.LG",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            source="arxiv",
        ),
    )
    session.flush()
    return pid


def test_the_cache_tables_are_unlogged(session: Session) -> None:
    """The whole justification for putting a cache in Postgres rather than beside it.

    Unlogged means no WAL on write and truncation after an unclean stop, which is the
    durability this data should have. Logged, these would be four more tables to back up.
    """
    rows = session.execute(
        text(
            "SELECT relname, relpersistence FROM pg_class "
            "WHERE relkind = 'r' AND relname LIKE 'account\\_%'"
        )
    ).all()
    assert {name for name, _ in rows} == {
        "account_searches",
        "account_recent_papers",
        "account_dismissals",
        "account_filters",
    }
    assert {persistence for _, persistence in rows} == {"u"}


def test_searches_come_back_newest_first(session: Session) -> None:
    for query in ("mixture of experts", "state space models", "rotary embeddings"):
        account.record_search(session, SUB, query)
    assert account.recent_searches(session, SUB) == [
        "rotary embeddings",
        "state space models",
        "mixture of experts",
    ]


def test_repeating_a_search_moves_it_up_rather_than_duplicating(session: Session) -> None:
    account.record_search(session, SUB, "attention")
    account.record_search(session, SUB, "diffusion")
    account.record_search(session, SUB, "attention")
    assert account.recent_searches(session, SUB) == ["attention", "diffusion"]
    total = session.execute(
        select(func.count()).select_from(AccountSearchRow).where(AccountSearchRow.user_sub == SUB)
    ).scalar_one()
    assert total == 2


def test_searches_are_capped(session: Session) -> None:
    for index in range(8):
        account.record_search(session, SUB, f"query {index}")
    kept = account.recent_searches(session, SUB, limit=99)
    assert len(kept) == 8
    # Now with a cap: the oldest go, in the same write.
    account.record_search(session, SUB, "newest", cap=3)
    assert account.recent_searches(session, SUB, limit=99) == ["newest", "query 7", "query 6"]


def test_a_blank_search_is_not_recorded(session: Session) -> None:
    account.record_search(session, SUB, "   ")
    assert account.recent_searches(session, SUB) == []


def test_clearing_history_leaves_nothing(session: Session) -> None:
    account.record_search(session, SUB, "attention")
    assert account.clear_searches(session, SUB) == 1
    assert account.recent_searches(session, SUB) == []


def test_recent_papers_are_capped_and_deduplicated(session: Session) -> None:
    for index in range(3):
        _paper(session, f"arxiv:2601.0000{index}")
    account.record_view(session, SUB, "arxiv:2601.00000")
    account.record_view(session, SUB, "arxiv:2601.00001")
    account.record_view(session, SUB, "arxiv:2601.00000")  # again: moves up, no duplicate
    assert account.recent_papers(session, SUB) == ["arxiv:2601.00000", "arxiv:2601.00001"]
    account.record_view(session, SUB, "arxiv:2601.00002", cap=2)
    assert account.recent_papers(session, SUB) == ["arxiv:2601.00002", "arxiv:2601.00000"]


def test_an_unknown_paper_is_dropped_not_refused(session: Session) -> None:
    # A stale id from a tab left open overnight is a beacon, not an error.
    account.record_view(session, SUB, "arxiv:9999.99999")
    account.record_dismissal(session, SUB, "arxiv:9999.99999")
    assert account.recent_papers(session, SUB) == []
    assert account.dismissed_papers(session, SUB) == []


def test_dismissals_round_trip_and_restore(session: Session) -> None:
    _paper(session, "arxiv:2601.00010")
    _paper(session, "arxiv:2601.00011")
    account.record_dismissal(session, SUB, "arxiv:2601.00010")
    account.record_dismissal(session, SUB, "arxiv:2601.00011")
    assert set(account.dismissed_papers(session, SUB)) == {"arxiv:2601.00010", "arxiv:2601.00011"}

    assert account.restore_dismissed(session, SUB, ["arxiv:2601.00010"]) == 1
    assert account.dismissed_papers(session, SUB) == ["arxiv:2601.00011"]
    assert account.restore_dismissed(session, SUB) == 1
    assert account.dismissed_papers(session, SUB) == []


def test_a_dismissal_does_not_delete_the_paper(session: Session) -> None:
    """Dismiss moves a paper to the end of the feed; it stays reachable everywhere else."""
    from researchscout.store.papers import get_paper

    _paper(session, "arxiv:2601.00020")
    account.record_dismissal(session, SUB, "arxiv:2601.00020")
    assert get_paper(session, "arxiv:2601.00020") is not None


def test_filters_are_one_row_per_account(session: Session) -> None:
    assert account.saved_filters(session, SUB) is None
    account.save_filters(session, SUB, "subject=ai&days=7")
    account.save_filters(session, SUB, "subject=math&topic=rl")
    assert account.saved_filters(session, SUB) == "subject=math&topic=rl"


def test_the_export_carries_the_cache(session: Session) -> None:
    """Cached rather than kept, but still stored about a person."""
    _paper(session, "arxiv:2601.00030")
    account.record_search(session, SUB, "attention")
    account.record_view(session, SUB, "arxiv:2601.00030")
    account.record_dismissal(session, SUB, "arxiv:2601.00030")
    account.save_filters(session, SUB, "subject=ai")
    session.flush()

    state = export_user_data(session, SUB)["site_state"]
    assert [item["query"] for item in state["recent_searches"]] == ["attention"]
    assert [item["paper_id"] for item in state["recent_papers"]] == ["arxiv:2601.00030"]
    assert [item["paper_id"] for item in state["dismissed_papers"]] == ["arxiv:2601.00030"]
    assert state["saved_filters"] == "subject=ai"


def test_deleting_the_account_takes_the_cache_with_it(session: Session) -> None:
    session.add(UserRow(sub="someone-else"))
    session.flush()
    _paper(session, "arxiv:2601.00040")
    account.record_search(session, "someone-else", "attention")
    account.record_dismissal(session, "someone-else", "arxiv:2601.00040")
    session.flush()

    assert delete_user(session, "someone-else") is True
    session.flush()
    remaining = session.execute(
        select(func.count())
        .select_from(AccountDismissalRow)
        .where(AccountDismissalRow.user_sub == "someone-else")
    ).scalar_one()
    assert remaining == 0
    assert account.recent_searches(session, "someone-else") == []
