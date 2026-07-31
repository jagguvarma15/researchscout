from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from researchscout.schema import Author, Paper, Signal, SignalType
from researchscout.store.papers import upsert_paper
from researchscout.store.signals import append_signal, append_signal_idempotent, series, velocity

pytestmark = pytest.mark.integration

PID = "arxiv:2401.00001"


def _paper() -> Paper:
    return Paper(
        id=PID,
        external_ids={"arxiv": "2401.00001"},
        title="T",
        abstract="A",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="arxiv",
    )


def _sig(value: float, observed_at: datetime) -> Signal:
    return Signal(
        paper_id=PID,
        type=SignalType.citation,
        source="semantic_scholar",
        value=value,
        observed_at=observed_at,
    )


def test_idempotent_append_converges_on_redelivery(session: Session) -> None:
    upsert_paper(session, _paper())
    now = datetime.now(UTC)

    assert append_signal_idempotent(session, _sig(5, now)) is True
    assert append_signal_idempotent(session, _sig(5, now)) is False  # exact redelivery
    assert append_signal_idempotent(session, _sig(6, now + timedelta(hours=6))) is True

    points = series(session, PID, "citation", now - timedelta(days=1))
    assert [value for _, value in points] == [5, 6]  # replays converge, new observations append


def test_append_is_append_only(session: Session) -> None:
    upsert_paper(session, _paper())
    now = datetime.now(UTC)
    append_signal(session, _sig(10.0, now - timedelta(days=2)))
    append_signal(session, _sig(12.0, now - timedelta(days=1)))

    points = series(session, PID, "citation", now - timedelta(days=30))
    assert len(points) == 2  # two rows, not overwritten
    assert [value for _, value in points] == [10.0, 12.0]  # oldest first


def test_velocity_positive_for_increasing(session: Session) -> None:
    upsert_paper(session, _paper())
    now = datetime.now(UTC)
    append_signal(session, _sig(10.0, now - timedelta(days=10)))
    append_signal(session, _sig(30.0, now))

    assert velocity(session, PID, "citation", timedelta(days=30)) == pytest.approx(2.0, abs=0.05)


def test_velocity_zero_for_flat(session: Session) -> None:
    upsert_paper(session, _paper())
    now = datetime.now(UTC)
    append_signal(session, _sig(5.0, now - timedelta(days=5)))
    append_signal(session, _sig(5.0, now - timedelta(days=1)))

    assert velocity(session, PID, "citation", timedelta(days=30)) == pytest.approx(0.0, abs=1e-6)


def test_velocity_zero_for_single_point(session: Session) -> None:
    upsert_paper(session, _paper())
    append_signal(session, _sig(5.0, datetime.now(UTC)))

    assert velocity(session, PID, "citation", timedelta(days=30)) == 0.0
