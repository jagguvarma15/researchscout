"""In-process refresh loop that keeps the radar fresh without manual runs.

Four tasks run on independent intervals: ingest enabled content sources, embed papers that lack a
vector, refresh enabled signal sources, and rebuild the weekly digest. The same task set backs both
``scout serve scheduler`` (a long-lived loop) and ``scout serve scheduler --once`` (a single pass),
so a host cron or a container job can drive it too. Work is synchronous and sequential on purpose: a
cycle ingests before it embeds before it refreshes signals, which is the order the data depends on.
A task that raises is logged and skipped, so one bad source never stops the loop.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Literal

from researchscout.config import Settings

if TYPE_CHECKING:
    from researchscout.embed.base import Embedder

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Sleep = Callable[[float], None]
_SourceKind = Literal["content", "signal"]


@dataclass
class Task:
    """One unit of scheduled work plus the monotonic deadline for its next run."""

    name: str
    interval_sec: float
    run: Callable[[], None]
    next_at: float = 0.0

    def due(self, now: float) -> bool:
        """True once ``now`` (monotonic seconds) has reached the next-run deadline."""
        return now >= self.next_at


class Scheduler:
    """Runs tasks on their intervals, sequentially, in a single process."""

    def __init__(
        self,
        tasks: list[Task],
        *,
        tick_sec: float = 30.0,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._tasks = tasks
        self._tick_sec = tick_sec
        self._clock = clock
        self._sleep = sleep

    def _run(self, task: Task) -> None:
        try:
            task.run()
        except Exception:  # noqa: BLE001 - a failing task must not stop the loop
            logger.warning("scheduled task %s failed", task.name, exc_info=True)
        task.next_at = self._clock() + task.interval_sec

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


def _ingest(kind: _SourceKind, window_days: int) -> None:
    from researchscout.ingest.pipeline import run_ingest
    from researchscout.sources.base import enabled_sources
    from researchscout.store.db import session_scope

    since = datetime.now(UTC) - timedelta(days=window_days)
    for source in enabled_sources(kind):
        try:
            with session_scope() as session:
                summary = run_ingest(session, source, since)
        except Exception:  # noqa: BLE001 - isolate one source's failure from the rest
            logger.warning("%s source %s failed", kind, source.name, exc_info=True)
            continue
        logger.info(
            "%s %s: fetched=%d new=%d collapsed=%d signals=%d",
            kind,
            source.name,
            summary.fetched,
            summary.new_papers,
            summary.collapsed,
            summary.signals,
        )


def _index() -> None:
    from researchscout.store.db import session_scope
    from researchscout.store.vectors import index_papers

    with session_scope() as session:
        count = index_papers(session, _embedder())
    if count:
        logger.info("indexed %d new paper(s)", count)


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


def build_tasks(settings: Settings) -> list[Task]:
    """Construct the scheduler's tasks with intervals drawn from ``settings``.

    Order matters: ingest new papers, embed them, refresh their signals, then rebuild the digest.
    """
    window = settings.scheduler_ingest_window_days
    ingest_content = partial(_ingest, "content", window)
    ingest_signals = partial(_ingest, "signal", window)
    return [
        Task("ingest", settings.scheduler_ingest_interval_sec, ingest_content),
        Task("index", settings.scheduler_index_interval_sec, _index),
        Task("signals", settings.scheduler_signals_interval_sec, ingest_signals),
        Task("digest", settings.scheduler_digest_interval_sec, partial(_digest, settings)),
        Task("topics", settings.scheduler_topics_interval_sec, partial(_topics, settings)),
        Task("report", settings.scheduler_report_interval_sec, partial(_report, settings)),
    ]
