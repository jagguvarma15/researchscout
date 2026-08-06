"""Wall-clock scheduling, and the four awkward days a year that make it worth its own module.

The properties that matter: a configured time is never silently skipped, a restart between two
slots waits for the next one rather than firing the one it missed, and the answer follows the
local clock across daylight saving instead of drifting an hour twice a year.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from researchscout.schedule import describe, next_run, parse_times, previous_run

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
PIPELINE = (time(5, 0), time(10, 0), time(14, 0), time(17, 0))


def test_parse_times_sorts_and_deduplicates() -> None:
    assert parse_times("17:00, 05:00,10:00 , 14:00, 05:00") == PIPELINE


def test_parse_times_of_nothing_is_no_times() -> None:
    # Which is how a task stays on its interval rather than acquiring a schedule.
    assert parse_times("") == ()
    assert parse_times("  ,  ") == ()


def test_parse_times_refuses_nonsense() -> None:
    # Better to fail at start-up than to run on a schedule nobody asked for.
    with pytest.raises(ValueError):
        parse_times("05:00,banana")


def test_next_run_finds_the_next_slot_today() -> None:
    now = datetime(2026, 8, 4, 11, 30, tzinfo=NY)
    assert next_run(PIPELINE, now, NY) == datetime(2026, 8, 4, 14, 0, tzinfo=NY)


def test_next_run_wraps_to_tomorrow_after_the_last_slot() -> None:
    now = datetime(2026, 8, 4, 23, 45, tzinfo=NY)
    assert next_run(PIPELINE, now, NY) == datetime(2026, 8, 5, 5, 0, tzinfo=NY)


def test_a_slot_just_passed_is_not_run_again() -> None:
    # A process restarting at 15:02 must wait for 17:00, not immediately fire the 14:00 run.
    now = datetime(2026, 8, 4, 15, 2, tzinfo=NY)
    assert next_run(PIPELINE, now, NY) == datetime(2026, 8, 4, 17, 0, tzinfo=NY)


def test_the_current_slot_is_not_reused() -> None:
    # Strictly after: having just run at 17:00, the next one is tomorrow morning.
    now = datetime(2026, 8, 4, 17, 0, tzinfo=NY)
    assert next_run(PIPELINE, now, NY) == datetime(2026, 8, 5, 5, 0, tzinfo=NY)


def test_no_times_means_no_next_run() -> None:
    assert next_run((), datetime(2026, 8, 4, 11, 30, tzinfo=NY), NY) is None
    assert previous_run((), datetime(2026, 8, 4, 11, 30, tzinfo=NY), NY) is None


def test_the_caller_may_be_in_any_zone() -> None:
    # The scheduler works in UTC; the schedule is expressed in New York time.
    now = datetime(2026, 8, 4, 15, 30, tzinfo=UTC)  # 11:30 EDT
    assert next_run(PIPELINE, now, NY) == datetime(2026, 8, 4, 14, 0, tzinfo=NY)
    assert previous_run(PIPELINE, now, NY) == datetime(2026, 8, 4, 10, 0, tzinfo=NY)


def test_previous_run_finds_the_latest_slot_already_due() -> None:
    now = datetime(2026, 8, 6, 10, 38, tzinfo=NY)
    assert previous_run(PIPELINE, now, NY) == datetime(2026, 8, 6, 10, 0, tzinfo=NY)


def test_previous_run_reaches_back_to_yesterday_before_the_first_slot() -> None:
    now = datetime(2026, 8, 6, 4, 59, tzinfo=NY)
    assert previous_run(PIPELINE, now, NY) == datetime(2026, 8, 5, 17, 0, tzinfo=NY)


def test_previous_run_counts_a_slot_landing_exactly_now() -> None:
    # At or before, unlike next_run: a health check asking at 14:00 sharp should hold the
    # 14:00 slot answerable rather than pointing at this morning.
    now = datetime(2026, 8, 6, 14, 0, tzinfo=NY)
    assert previous_run(PIPELINE, now, NY) == datetime(2026, 8, 6, 14, 0, tzinfo=NY)


def test_spring_forward_keeps_the_local_time() -> None:
    # 8 March 2026, 02:00 EST becomes 03:00 EDT. A 05:00 run stays at 05:00 local - the whole
    # reason for a named zone instead of a fixed offset.
    before = datetime(2026, 3, 7, 6, 0, tzinfo=NY)
    assert next_run((time(5, 0),), before, NY) == datetime(2026, 3, 8, 5, 0, tzinfo=NY)


def test_a_time_the_clock_skips_over_still_runs() -> None:
    # 02:30 does not exist on the spring-forward day. Rather than skipping the run entirely,
    # it lands on the first moment that does exist.
    before = datetime(2026, 3, 8, 0, 30, tzinfo=NY)
    upcoming = next_run((time(2, 30),), before, NY)
    assert upcoming is not None
    assert upcoming > before
    # Local wall clock reads 03:30, because 02:30 was never on it.
    assert upcoming.astimezone(NY).hour == 3


def test_autumn_fall_back_keeps_the_local_time() -> None:
    # 1 November 2026: 01:00 EDT happens, then 01:00 EST happens again. A 05:00 run is still
    # 05:00 local, whatever the offset underneath it did overnight.
    before = datetime(2026, 10, 31, 6, 0, tzinfo=NY)
    assert next_run((time(5, 0),), before, NY) == datetime(2026, 11, 1, 5, 0, tzinfo=NY)


def test_describe_reads_back_the_schedule() -> None:
    assert describe(PIPELINE, NY) == "05:00, 10:00, 14:00, 17:00 America/New_York"
    assert describe((), NY) == "never"
