"""Wall-clock scheduling: when the next run of a daily-times task is due.

An interval says "every hour"; a wall clock says "at five in the morning, New York time", which
is what a pipeline tracking a publisher's day actually wants. The difference is entirely in
computing the deadline, so that is all this module does -- it hands back datetimes, and
``researchscout.scheduler`` compares them against the wall clock. Not against monotonic time:
on a host that sleeps, the monotonic clock stops with it, and a deadline stored as "so many
awake-seconds from now" slips by however long the lid was down.

Pure and dependency-free, because the interesting cases are the ones that are awkward to
reproduce on purpose: daylight saving moving the clock under a fixed local time, a list of
times wrapping past midnight, and a process starting between two slots. Each is a test rather
than a surprise.

Times are naive ``HH:MM`` in a named zone. Two edge cases fall out of that and are resolved the
way a person would expect:

* On the spring-forward day a time that does not exist locally (02:30 in New York) is pushed to
  the first moment that does. No configured time is silently skipped.
* On the autumn day a time that happens twice runs on the first pass, which is what ``fold=0``
  gives us for free.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_UTC = ZoneInfo("UTC")


def parse_times(raw: str) -> tuple[time, ...]:
    """Parse a comma-separated ``HH:MM`` list into sorted, de-duplicated times.

    An empty string is no times at all, which is how a task stays on its interval. A malformed
    entry raises: a schedule that silently drops the run you asked for is worse than one that
    refuses to start.
    """
    entries = [part.strip() for part in raw.split(",") if part.strip()]
    parsed = {time.fromisoformat(entry) for entry in entries}
    return tuple(sorted(parsed))


def _on(day: date, at: time, zone: ZoneInfo) -> datetime:
    """``at`` on ``day`` in ``zone``, moved forward if the clock skipped over it.

    On a spring-forward day 02:30 does not exist; Python still builds the datetime, but
    converting to UTC and back lands somewhere else. Comparing the round trip detects that, and
    the later of the two is the first real moment at or after the requested one.
    """
    naive = datetime.combine(day, at).replace(tzinfo=zone)
    round_tripped = naive.astimezone(_UTC).astimezone(zone)
    return max(naive, round_tripped)


def next_run(times: Sequence[time], now: datetime, zone: ZoneInfo) -> datetime | None:
    """The first configured time strictly after ``now``, or None when there are no times.

    Strictly after, so a task that has just run at 17:00 does not immediately qualify for the
    17:00 slot again.
    """
    if not times:
        return None
    local = now.astimezone(zone)
    for day_offset in (0, 1, 2):
        day = local.date() + timedelta(days=day_offset)
        for at in times:
            candidate = _on(day, at, zone)
            if candidate > local:
                return candidate
    # Unreachable with a non-empty times tuple: two days always contain one of them.
    raise AssertionError("no next run found")  # pragma: no cover


def previous_run(times: Sequence[time], now: datetime, zone: ZoneInfo) -> datetime | None:
    """The most recent configured time at or before ``now``, or None when there are no times.

    At or before, so exactly-on-the-slot reads as "this slot is due now". This is the question
    a health check asks: which slot should have run by now, so its absence can be named.
    """
    if not times:
        return None
    local = now.astimezone(zone)
    for day_offset in (0, -1, -2):
        day = local.date() + timedelta(days=day_offset)
        for at in reversed(times):
            candidate = _on(day, at, zone)
            if candidate <= local:
                return candidate
    # Unreachable with a non-empty times tuple: two days always contain one of them.
    raise AssertionError("no previous run found")  # pragma: no cover


def describe(times: Iterable[time], zone: ZoneInfo) -> str:
    """A one-line summary for the scheduler's start-up log."""
    listed = ", ".join(at.strftime("%H:%M") for at in times)
    return f"{listed} {zone.key}" if listed else "never"
