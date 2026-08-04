"""In-process refresh loop for the derived products the stream does not build.

Three tasks always run: the weekly digest, the topic rebuild, and the daily report. Ingestion,
embedding, full text and signal refresh normally come from the streaming pipeline
(``scout stream serve``).

``RS_SCHEDULER_BATCH_PIPELINE`` adds those four here instead, driving the same batch functions
``scout ingest`` / ``index`` / ``fulltext`` use. That is for an install that does not run the
stream - a deployment where Kafka is not worth its memory, say - which would otherwise never
see another paper. Running both at once means two processes fetching from the same upstreams
on one address, which is what the three-second arXiv floor is not designed for; pick one.

A task runs either on an interval or at named times of day. Intervals are the default and are
what a local checkout wants; wall-clock times are for a deployment that should follow a
publisher's day rather than an arbitrary phase set by whenever the process last restarted. Set
``RS_SCHEDULER_PIPELINE_AT`` and the fetch-and-enrich tasks move onto the clock;
``RS_SCHEDULER_DAILY_AT`` does the same for the once-a-day products. There is deliberately no
catch-up for a missed slot: the ingest window is several days wide, so the next run covers
whatever a restart stepped over, and firing immediately on start-up would instead mean a
restart loop hammering arXiv.

The same task set backs both ``scout serve scheduler`` (a long-lived loop) and ``--once`` (a
single pass), so a host cron can drive it too. A task that raises is logged and skipped, so one
failure never stops the loop.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as clock_time
from functools import partial
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from researchscout.config import Settings
from researchscout.schedule import describe, parse_times, seconds_until

if TYPE_CHECKING:
    from researchscout.embed.base import Embedder

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Sleep = Callable[[float], None]
Wall = Callable[[], datetime]


@dataclass
class Task:
    """One unit of scheduled work plus the monotonic deadline for its next run.

    ``at`` turns an interval task into a wall-clock one: when it is set, ``interval_sec`` is
    ignored and each deadline is computed from the next of those times in ``zone``.
    """

    name: str
    interval_sec: float
    run: Callable[[], None]
    next_at: float = 0.0
    at: tuple[clock_time, ...] = ()
    zone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))

    def due(self, now: float) -> bool:
        """True once ``now`` (monotonic seconds) has reached the next-run deadline."""
        return now >= self.next_at


class Scheduler:
    """Runs tasks on their intervals or at their times of day, sequentially, in one process."""

    def __init__(
        self,
        tasks: list[Task],
        *,
        tick_sec: float = 30.0,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
        wall: Wall = lambda: datetime.now(UTC),
    ) -> None:
        self._tasks = tasks
        self._tick_sec = tick_sec
        self._clock = clock
        self._sleep = sleep
        self._wall = wall
        for task in tasks:
            if task.at:
                # An interval task starts due, so a fresh process does its work at once. A
                # wall-clock task starts at its next slot instead: waking at 15:02 must not be
                # read as "the 14:00 run has not happened", which on a restart loop would mean
                # a fetch every time the process came up.
                self._reschedule(task)
                logger.info("task %s runs at %s", task.name, describe(task.at, task.zone))

    def _reschedule(self, task: Task) -> None:
        if task.at:
            delay = seconds_until(task.at, self._wall(), task.zone)
            task.next_at = self._clock() + (delay if delay is not None else task.interval_sec)
        else:
            task.next_at = self._clock() + task.interval_sec

    def _run(self, task: Task) -> None:
        try:
            task.run()
        except Exception:  # noqa: BLE001 - a failing task must not stop the loop
            logger.warning("scheduled task %s failed", task.name, exc_info=True)
        self._reschedule(task)

    def run_pass(self) -> list[str]:
        """Run every task once regardless of interval; return the names run (backs ``--once``)."""
        for task in self._tasks:
            self._run(task)
        return [task.name for task in self._tasks]

    def run_due(self, now: float) -> list[str]:
        """Run the tasks whose interval has elapsed by ``now``; return their names."""
        ran: list[str] = []
        for task in self._tasks:
            if task.due(now):
                self._run(task)
                ran.append(task.name)
        return ran

    def run_forever(self, stop: Callable[[], bool]) -> None:
        """Loop until ``stop()`` returns true, running each task on its own interval."""
        while not stop():
            self.run_due(self._clock())
            if stop():
                return
            self._sleep(self._tick_sec)


def _embedder() -> Embedder:
    """One shared embedder so the model loads once across index cycles."""
    from researchscout.embed.factory import default_embedder

    return default_embedder()


def _ingest(settings: Settings) -> None:
    """Fetch every enabled content source over the recent window, resuming its cursor."""
    from datetime import UTC, datetime, timedelta

    import httpx

    from researchscout.ingest.pipeline import run_ingest
    from researchscout.sources import enabled_sources
    from researchscout.store.db import session_scope

    since = datetime.now(UTC) - timedelta(days=settings.scheduler_ingest_window_days)
    for source in enabled_sources("content"):
        try:
            with session_scope() as session:
                summary = run_ingest(session, source, since, resume=True)
        except httpx.HTTPError as exc:
            # One upstream being rate limited or down must not stop the others.
            logger.warning("ingest %s failed: %s", source.name, exc)
            continue
        logger.info(
            "ingest %s: fetched=%d new=%d signals=%d",
            summary.source,
            summary.fetched,
            summary.new_papers,
            summary.signals,
        )


def _signals(settings: Settings) -> None:
    """Refresh every enabled signal source: citations, upvotes, discussion, engagement."""
    from datetime import UTC, datetime, timedelta

    import httpx

    from researchscout.ingest.pipeline import run_ingest
    from researchscout.sources import enabled_sources
    from researchscout.store.db import session_scope

    since = datetime.now(UTC) - timedelta(days=settings.scheduler_ingest_window_days)
    for source in enabled_sources("signal"):
        try:
            with session_scope() as session:
                summary = run_ingest(session, source, since, resume=True)
        except httpx.HTTPError as exc:
            logger.warning("signals %s failed: %s", source.name, exc)
            continue
        logger.info("signals %s: %d observation(s)", summary.source, summary.signals)


def _index(settings: Settings) -> None:
    """Embed whatever is not embedded yet, and chunk full text when chunk retrieval is on."""
    from researchscout.store.chunks import index_chunks
    from researchscout.store.db import session_scope
    from researchscout.store.vectors import index_papers

    embedder = _embedder()
    with session_scope() as session:
        papers = index_papers(session, embedder)
        chunks = index_chunks(session, embedder) if settings.chunk_retrieval else 0
    if papers or chunks:
        logger.info("index: %d paper(s), %d chunk(s)", papers, chunks)


def _fulltext(settings: Settings) -> None:
    """Fetch article text for a modest batch, saved and read papers first.

    Full-content harvesting is not permitted, so this stays small and paced exactly like the
    ingest path - the batch size is the politeness, not an optimisation.
    """
    import time

    from sqlalchemy import select

    from researchscout.fulltext import fetch_full_text
    from researchscout.store.db import session_scope
    from researchscout.store.models import EventRow, SavedPaperRow
    from researchscout.store.papers import papers_missing_full_text, set_full_text

    delay = settings.arxiv_page_delay_sec
    fetched = 0
    with session_scope() as session:
        priority = set(session.execute(select(SavedPaperRow.paper_id)).scalars()) | set(
            session.execute(select(EventRow.paper_id).distinct()).scalars()
        )
        pending = papers_missing_full_text(
            session, limit=settings.scheduler_fulltext_batch, first=sorted(priority)
        )
        for position, (paper_id, arxiv_id) in enumerate(pending):
            if position and delay > 0:
                time.sleep(delay)
            text = fetch_full_text(arxiv_id)
            set_full_text(session, paper_id, text or "")
            fetched += 1 if text else 0
    if pending:
        logger.info("full text: %d of %d attempted", fetched, len(pending))


def _digest(settings: Settings) -> None:
    from researchscout.digest import build_digest
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.digests import upsert_digest

    with session_scope() as session:
        result = build_digest(
            session, OpenAICompatLLM(), days=settings.digest_days, k=settings.digest_top_k
        )
        if result is None:
            logger.info("digest: window empty, nothing to publish")
            return
        upsert_digest(session, result)
    logger.info("digest %s: %d papers, %d cited", result.slug, len(result.items), len(result.cited))


def _topics(settings: Settings) -> None:
    from researchscout.cluster import build_topics
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.topics import replace_topics

    with session_scope() as session:
        topics = build_topics(
            session,
            _embedder(),
            OpenAICompatLLM(),
            days=settings.cluster_window_days,
            threshold=settings.cluster_distance_threshold,
            algo=settings.cluster_algo,
        )
        replace_topics(session, topics)
    logger.info("built %d topic(s)", len(topics))


def _report(settings: Settings) -> None:
    from researchscout.report import build_daily_report
    from researchscout.store.db import session_scope
    from researchscout.store.digests import upsert_digest
    from researchscout.store.lineage import prune_lineage

    with session_scope() as session:
        result = build_daily_report(session)
        if result is None:
            logger.info("daily report: window empty, nothing to publish")
        else:
            upsert_digest(session, result)
            logger.info("daily report %s: %d must-read", result.slug, len(result.items))
        pruned = prune_lineage(session)
    if pruned:
        logger.info("pruned %d lineage rows", pruned)


def _catalog(settings: Settings) -> None:
    """Refresh the model and benchmark catalogue from its upstreams."""
    from researchscout.catalog import refresh_catalog
    from researchscout.store.db import session_scope

    with session_scope() as session:
        summary = refresh_catalog(session)
    logger.info(
        "catalog: %d model(s), %d benchmark(s), %d result(s), %d linked to papers",
        summary.models,
        summary.benchmarks,
        summary.results,
        summary.linked,
    )


def build_tasks(settings: Settings) -> list[Task]:
    """Construct the scheduler's tasks from ``settings``.

    Each task carries both an interval and, when the corresponding ``_at`` setting is present,
    a set of times of day; ``Task`` prefers the times when it has them. Keeping the interval
    on the task regardless means unsetting the times is all it takes to go back.
    """
    zone = ZoneInfo(settings.scheduler_timezone)
    pipeline_at = parse_times(settings.scheduler_pipeline_at)
    daily_at = parse_times(settings.scheduler_daily_at)

    def task(
        name: str, interval: float, run: Callable[[], None], at: tuple[clock_time, ...]
    ) -> Task:
        return Task(name, interval, run, at=at, zone=zone)

    tasks: list[Task] = []
    if settings.scheduler_batch_pipeline:
        # Ordered so a cycle flows the way a paper does: arrive, get embedded, get its text,
        # then collect the signals that rank it.
        tasks += [
            task(
                "ingest",
                settings.scheduler_ingest_interval_sec,
                partial(_ingest, settings),
                pipeline_at,
            ),
            task(
                "index",
                settings.scheduler_index_interval_sec,
                partial(_index, settings),
                pipeline_at,
            ),
            task(
                "fulltext",
                settings.scheduler_fulltext_interval_sec,
                partial(_fulltext, settings),
                pipeline_at,
            ),
            task(
                "signals",
                settings.scheduler_signals_interval_sec,
                partial(_signals, settings),
                pipeline_at,
            ),
        ]
    tasks += [
        task(
            "catalog",
            settings.scheduler_catalog_interval_sec,
            partial(_catalog, settings),
            daily_at,
        ),
        task(
            "digest", settings.scheduler_digest_interval_sec, partial(_digest, settings), daily_at
        ),
        task(
            "topics", settings.scheduler_topics_interval_sec, partial(_topics, settings), daily_at
        ),
        task(
            "report", settings.scheduler_report_interval_sec, partial(_report, settings), daily_at
        ),
    ]
    return tasks
