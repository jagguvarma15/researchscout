"""In-process refresh loop: fetch, enrich, and publish on the publisher's clock.

The task groups follow the day arXiv actually has. Announcements land Sunday through Thursday
at 20:00 ET and the search API refreshes once around midnight ET, so the pipeline set
(ingest, index, full text) runs once in the early morning; the citation walker runs before
the daily report so the morning read carries fresh counts; the fast signal proxies (HF
trending, HN, Bluesky) poll on their own times; the daily set (catalogue, digest, topics)
keeps the afternoon; and a health task self-checks on a short interval throughout.

A task runs either on an interval or at named times of day. Intervals are the default and are
what a local checkout wants; wall-clock times are for a deployment that should follow a
publisher's day rather than an arbitrary phase set by whenever the process last restarted.
There is deliberately no catch-up for a slot missed while the process was down: the ingest
window derives from the source's own watermark, so the next run covers whatever a restart
stepped over, and firing immediately on start-up would instead mean a restart loop hammering
arXiv.

A slot missed while the process was *suspended* is different, and wall-clock deadlines are
held as datetimes rather than monotonic offsets because of it. On a Mac that sleeps, the
container's monotonic clock stops with the host, so "so many awake-seconds from now" drifts
by however long the lid was down. Judged against the wall clock instead, a slept-over
deadline is simply due on wake: the task runs once, covering the backlog, and reschedules
onto the next future slot.

The same task set backs both ``scout serve scheduler`` (a long-lived loop) and ``--once`` (a
single pass), so a host cron can drive it too. A task that raises is logged and skipped, so
one failure never stops the loop; every run opens a ledger row before it starts and completes
it after, so even a task that hangs leaves evidence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from functools import partial
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from researchscout import observe
from researchscout.config import Settings
from researchscout.llm.errors import is_quota_status
from researchscout.schedule import describe, next_run, parse_times

if TYPE_CHECKING:
    from researchscout.embed.base import Embedder
    from researchscout.llm.base import LLM

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Sleep = Callable[[float], None]
Wall = Callable[[], datetime]
Heartbeat = Callable[[], None]
TaskFn = Callable[[], str | None]

# Citation sources belong to the walker (the ``citations`` task), not the fast-signal poll.
_CITATION_SOURCES = frozenset({"semantic_scholar", "openalex"})

# The revisions sweep's window: a couple of days of lastUpdatedDate nightly, capped so a
# long outage becomes a deliberate backfill rather than a giant catch-up walk.
_REVISIONS_OVERLAP_DAYS = 2
_REVISIONS_MAX_WINDOW_DAYS = 7


@dataclass
class Task:
    """One unit of scheduled work plus the deadline for its next run.

    ``at`` turns an interval task into a wall-clock one: when it is set, ``interval_sec`` is
    ignored and each deadline is the next of those times in ``zone``, held in ``next_wall``.
    Interval tasks keep a monotonic deadline in ``next_at`` - elapsed awake time is the right
    measure for "every ten minutes", and exactly the wrong one for "at five in the morning".
    """

    name: str
    interval_sec: float
    run: Callable[[], None]
    next_at: float = 0.0
    at: tuple[clock_time, ...] = ()
    zone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    next_wall: datetime | None = None
    #: A failed wall-clock run re-arms after ``retry_delay_sec`` up to ``max_retries``
    #: times per slot, instead of conceding the whole day to a transient upstream error.
    max_retries: int = 2
    retry_delay_sec: float = 1800.0
    retries_left: int = 0

    def due(self, now: float, wall: datetime) -> bool:
        """True once the relevant clock has reached the next-run deadline.

        ``wall`` decides for wall-clock tasks and ``now`` (monotonic seconds) for interval
        ones. Both deadlines are aware datetimes or floats set by the scheduler; a wall-clock
        task with no deadline yet is never due rather than always due.
        """
        if self.at:
            return self.next_wall is not None and wall >= self.next_wall
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
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self._tasks = tasks
        self._tick_sec = tick_sec
        self._clock = clock
        self._sleep = sleep
        self._wall = wall
        self._heartbeat = heartbeat or (lambda: None)
        for task in tasks:
            if task.at:
                # An interval task starts due, so a fresh process does its work at once. A
                # wall-clock task starts at its next slot instead: waking at 15:02 must not be
                # read as "the 14:00 run has not happened", which on a restart loop would mean
                # a fetch every time the process came up.
                self._reschedule(task)
        # One line naming every task and when it next runs. A scheduler that says nothing on
        # start-up looks identical whether it has eight tasks or four, and a deployment that
        # had quietly stopped fetching papers is exactly the case worth being able to read off
        # the first page of the log.
        logger.info(
            "scheduler: %d task(s) - %s",
            len(tasks),
            "; ".join(f"{task.name} {self._schedule_of(task)}" for task in tasks),
        )

    def _schedule_of(self, task: Task) -> str:
        """How this task's next run is decided, for the start-up summary."""
        if not task.at:
            return f"every {int(task.interval_sec)}s, first run now"
        upcoming = next_run(task.at, self._wall(), task.zone)
        when = upcoming.strftime("%Y-%m-%d %H:%M") if upcoming else "never"
        return f"at {describe(task.at, task.zone)}, next {when}"

    def _reschedule(self, task: Task) -> None:
        task.retries_left = task.max_retries
        if task.at:
            # Stored as a datetime, not converted to monotonic seconds: the monotonic clock
            # pauses while the host sleeps, and a converted deadline would slip by exactly
            # that long. The wall clock is resynced on wake, so a slept-over slot fires then.
            task.next_wall = next_run(task.at, self._wall(), task.zone)
        else:
            task.next_at = self._clock() + task.interval_sec

    def _run(self, task: Task) -> None:
        try:
            task.run()
        except Exception as exc:  # noqa: BLE001 - a failing task must not stop the loop
            logger.warning("scheduled task %s failed", task.name, exc_info=True)
            # The loop swallows the failure by design, so this is the one place a task
            # error can reach the error reporter (a no-op when reporting is off).
            observe.capture_exception(exc)
            if task.at and task.retries_left > 0 and not is_quota_status(exc):
                # Re-arm within the day rather than concede the slot: arXiv being down at
                # half past midnight should cost half an hour, not twenty-four. Capped at
                # the next real slot so a retry can only ever move work earlier, and the
                # budget resets whenever a run succeeds or the slot rolls over. A bare 429
                # concedes instead: a spent daily quota does not come back in half an hour,
                # and retrying it only stacks failed rows into a streak.
                task.retries_left -= 1
                wall = self._wall()
                slot = next_run(task.at, wall, task.zone)
                retry_at = wall + timedelta(seconds=task.retry_delay_sec)
                task.next_wall = min(retry_at, slot) if slot is not None else retry_at
                self._heartbeat()
                return
        self._reschedule(task)
        self._heartbeat()

    def run_pass(self) -> list[str]:
        """Run every task once regardless of interval; return the names run (backs ``--once``)."""
        for task in self._tasks:
            self._run(task)
        return [task.name for task in self._tasks]

    def run_due(self, now: float) -> list[str]:
        """Run the tasks whose deadline has passed by ``now``; return their names."""
        wall = self._wall()
        ran: list[str] = []
        for task in self._tasks:
            if task.due(now, wall):
                self._run(task)
                ran.append(task.name)
        self._heartbeat()
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


def _run_sources(
    settings: Settings, kind: Literal["content", "signal"], names: frozenset[str] | None
) -> str:
    """Fetch a set of sources, isolating each one's failure; the note names every outcome.

    Any exception in one source — HTTP, parse, database — must not cost the sources after
    it. The healthy sources' work stands either way; if anything failed, the aggregate
    raises so the ledger row reads failed while still carrying who succeeded.
    """
    from researchscout.ingest.pipeline import run_ingest, window_start
    from researchscout.sources import enabled_sources
    from researchscout.store.db import session_scope

    parts: list[str] = []
    failed = False
    for source in enabled_sources(kind):
        if names is not None and source.name not in names:
            continue
        try:
            with session_scope() as session:
                since = window_start(
                    session,
                    source.name,
                    overlap_days=settings.scheduler_ingest_window_days,
                    max_window_days=settings.scheduler_ingest_max_window_days,
                )
                summary = run_ingest(
                    session,
                    source,
                    since,
                    resume=True,
                    stop_after_known_pages=(
                        (settings.scheduler_ingest_early_stop_pages or None)
                        if kind == "content"
                        else None
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - one source must not stop the others
            logger.warning("%s %s failed", kind, source.name, exc_info=True)
            parts.append(f"{source.name}: failed ({str(exc) or exc.__class__.__name__})")
            failed = True
            continue
        suffix = f", stopped early: {summary.stopped_early}" if summary.stopped_early else ""
        if kind == "content":
            part = f"{summary.source}: fetched={summary.fetched} new={summary.new_papers}{suffix}"
        else:
            part = f"{summary.source}: {summary.signals} observation(s){suffix}"
        logger.info("%s %s", kind, part)
        parts.append(part)
        if summary.stopped_by_error:
            # The committed pages stand, but the ledger row must not read ok: a walk that
            # lands one page and then rate-limits out every day is quietly thinning
            # coverage, and task_streaks only notices if the row fails.
            failed = True
    note = "; ".join(parts) if parts else "no sources enabled"
    if failed:
        raise RuntimeError(note)
    return note


def _ingest(settings: Settings) -> str:
    """Fetch every enabled content source from its watermark-derived window."""
    return _run_sources(settings, "content", None)


def _signals(settings: Settings) -> str:
    """Refresh the fast signal proxies: trending rank, upvotes, discussion, engagement."""
    from researchscout.sources import enabled_sources

    fast = frozenset(
        source.name for source in enabled_sources("signal") if source.name not in _CITATION_SOURCES
    )
    return _run_sources(settings, "signal", fast)


def _citations(settings: Settings) -> str:
    """Walk citation coverage stalest-first: Semantic Scholar leads, OpenAlex takes the rest."""
    from researchscout.ingest.citations import run_citation_refresh

    note = run_citation_refresh(settings)
    logger.info("citations: %s", note)
    return note


def _revisions(settings: Settings) -> str:
    """Walk recently revised papers so v2s, DOIs, and journal refs re-enter the corpus.

    The nightly ingest windows on submittedDate, which a revision never re-enters; this
    sweep runs the same pipeline over lastUpdatedDate with its own watermark and cursor.
    Nearly every entry is already stored, so pages count as collapsed refreshes - which
    is the point - and the known-pages early stop stays off because it would fire on the
    first page.
    """
    from researchscout.ingest.pipeline import run_ingest, window_start
    from researchscout.sources.arxiv import ArxivUpdatesSource
    from researchscout.store.db import session_scope

    source = ArxivUpdatesSource()
    with session_scope() as session:
        since = window_start(
            session,
            source.name,
            overlap_days=_REVISIONS_OVERLAP_DAYS,
            max_window_days=_REVISIONS_MAX_WINDOW_DAYS,
        )
        summary = run_ingest(session, source, since, resume=True)
    suffix = f", stopped early: {summary.stopped_early}" if summary.stopped_early else ""
    note = (
        f"fetched={summary.fetched} refreshed={summary.collapsed} new={summary.new_papers}{suffix}"
    )
    logger.info("revisions %s", note)
    if summary.stopped_by_error:
        raise RuntimeError(note)
    return note


def _index(settings: Settings) -> str:
    """Embed whatever is not embedded yet, and chunk full text when chunk retrieval is on.

    Papers and chunks take separate sessions so a chunking failure cannot roll back the
    paper embeddings that already committed.
    """
    from researchscout.store.chunks import index_chunks
    from researchscout.store.db import session_scope
    from researchscout.store.vectors import index_papers

    embedder = _embedder()
    with session_scope() as session:
        papers = index_papers(session, embedder)
    chunks = 0
    if settings.chunk_retrieval:
        with session_scope() as session:
            chunks = index_chunks(session, embedder)
    if papers or chunks:
        logger.info("index: %d paper(s), %d chunk(s)", papers, chunks)
    return f"{papers} paper(s), {chunks} chunk(s)"


def run_categorize(
    settings: Settings,
    *,
    limit: int,
    llm_fallback: bool | None = None,
    embedder: Embedder | None = None,
    llm: LLM | None = None,
) -> str:
    """Enrich papers that lack keywords, sharing the stream's categorize core.

    The streaming deployment does this per packet; this is the batch equivalent, so both
    paths converge on identical rows. The Categorizer is constructed per call so its
    topic-centroid cache is fresh each run, and the doc vector computed for topic
    matching is stored as the paper embedding too - index has nothing left to do for
    these papers. ``llm_fallback=None`` follows the settings; the CLI passes an explicit
    value so a large backfill cannot silently spend the provider quota.
    """
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.schema import PaperLabel
    from researchscout.store.db import session_scope
    from researchscout.store.papers import papers_missing_keywords, set_enrichment
    from researchscout.store.vectors import upsert_embedding
    from researchscout.stream.categorize import Categorizer, load_labels

    embedder = embedder or _embedder()
    labels = load_labels(settings.labels_config_path) if settings.stream_labels_enabled else []
    categorizer = Categorizer(
        embedder,
        llm or OpenAICompatLLM(),
        session_scope,
        topic_match_min=settings.stream_topic_match_min,
        keyword_min_similarity=settings.stream_keyword_min_similarity,
        keywords_llm_fallback=(
            settings.stream_keywords_llm_fallback if llm_fallback is None else llm_fallback
        ),
        labels=labels,
        keyword_candidate_cap=settings.stream_keyword_candidates,
    )
    with session_scope() as session:
        pending = papers_missing_keywords(session, limit=limit)
    done = 0
    by_llm = 0
    # Sub-batches bound the merged embeds' memory and commit as they go, so an
    # interruption keeps everything already enriched.
    for start in range(0, len(pending), 50):
        chunk = pending[start : start + 50]
        enriched = categorizer.enrich_batch(
            [(title, abstract, primary) for _, title, abstract, primary in chunk]
        )
        with session_scope() as session:
            for (paper_id, _, _, _), (enrichment, vector) in zip(chunk, enriched, strict=True):
                row_labels = []
                topic = enrichment.get("topic")
                if isinstance(topic, dict):
                    row_labels.append(
                        PaperLabel(
                            label=topic["label"], source="topic", score=topic.get("similarity")
                        )
                    )
                row_labels.extend(
                    PaperLabel(label=name, source="custom")
                    for name in enrichment.get("labels") or []
                )
                set_enrichment(
                    session,
                    paper_id,
                    keywords=enrichment.get("keywords"),
                    labels=row_labels or None,
                )
                upsert_embedding(session, paper_id, embedder.model_id, vector)
                done += 1
                if enrichment.get("keyword_method") == "llm":
                    by_llm += 1
    if done:
        logger.info("categorize: %d paper(s), %d by llm", done, by_llm)
    return f"{done} paper(s), {by_llm} by llm"


def _categorize(settings: Settings) -> str:
    """Batch keyword and label enrichment for whatever ingest landed unprocessed."""
    return run_categorize(settings, limit=settings.scheduler_categorize_batch)


def _fulltext(settings: Settings, heartbeat: Heartbeat | None = None) -> str:
    """Fetch article text for a modest batch, saved and read papers first.

    Full-content harvesting is not permitted, so this stays small and paced exactly like the
    ingest path - the batch size is the politeness, not an optimisation. Each paper commits
    on its own, so an interruption keeps everything already fetched; the heartbeat ticks
    between items because this is the one task long enough to look like a hang from outside.
    """
    import time

    from sqlalchemy import select

    from researchscout.chunking import section_headings
    from researchscout.fulltext import fetch_full_text
    from researchscout.store.db import session_scope
    from researchscout.store.models import EventRow, SavedPaperRow
    from researchscout.store.papers import (
        papers_missing_full_text,
        record_full_text_result,
        set_enrichment,
    )

    beat = heartbeat or (lambda: None)
    delay = settings.arxiv_page_delay_sec
    fetched = 0
    with session_scope() as session:
        priority = set(session.execute(select(SavedPaperRow.paper_id)).scalars()) | set(
            session.execute(select(EventRow.paper_id).distinct()).scalars()
        )
        pending = papers_missing_full_text(
            session, limit=settings.scheduler_fulltext_batch, first=sorted(priority)
        )
        for position, (paper_id, arxiv_id, published_at) in enumerate(pending):
            if position and delay > 0:
                time.sleep(delay)
            text = fetch_full_text(arxiv_id)
            record_full_text_result(session, paper_id, text, published_at=published_at)
            if text:
                sections = section_headings(text)
                if sections:
                    set_enrichment(session, paper_id, sections=sections)
            session.commit()
            fetched += 1 if text else 0
            beat()
    if pending:
        logger.info("full text: %d of %d attempted", fetched, len(pending))
    return f"{fetched} of {len(pending)} attempted"


def _digest(settings: Settings) -> str:
    """Rank in one session, compose with no transaction open, publish in another.

    The LLM round-trip is the slowest and least reliable step here; it must not hold a
    database connection while it thinks.
    """
    from researchscout.digest import compose_digest, rank_digest
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.digests import upsert_digest

    with session_scope() as session:
        items, start, end = rank_digest(session, days=settings.digest_days, k=settings.digest_top_k)
    if not items:
        logger.info("digest: window empty, nothing to publish")
        return "window empty"
    result = compose_digest(OpenAICompatLLM(), items, start=start, end=end)
    with session_scope() as session:
        upsert_digest(session, result)
    logger.info("digest %s: %d papers, %d cited", result.slug, len(result.items), len(result.cited))
    note = f"{result.slug}: {len(result.items)} papers, {len(result.cited)} cited"
    if not result.llm_ok:
        note += "; prose fallback (llm unavailable)"
    return note


def _topics(settings: Settings) -> str:
    from researchscout.cluster import build_topics
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.topics import replace_topics

    with session_scope() as session:
        build = build_topics(
            session,
            _embedder(),
            OpenAICompatLLM(),
            days=settings.cluster_window_days,
            threshold=settings.cluster_distance_threshold,
            algo=settings.cluster_algo,
        )
        replace_topics(session, build.topics)
    note = f"{len(build.topics)} topic(s)"
    if build.fallback_labels:
        note += f"; labels: {build.llm_labels} llm, {build.fallback_labels} keyword-fallback"
    logger.info("built %s", note)
    return note


def _report(settings: Settings) -> str:
    """Publish the morning report, then run the prunes — each in its own session.

    The prunes ride this daily slot for cadence only; a prune failure must not roll back a
    published report, so nothing shares a transaction here.
    """
    from researchscout.report import build_daily_report
    from researchscout.store.db import session_scope
    from researchscout.store.digests import upsert_digest
    from researchscout.store.lineage import prune_lineage
    from researchscout.store.raw import prune_raw_items

    zone = ZoneInfo(settings.scheduler_timezone)
    with session_scope() as session:
        result = build_daily_report(session, zone=zone)
        if result is None:
            logger.info("daily report: window empty, nothing to publish")
            note = "window empty"
        else:
            upsert_digest(session, result)
            logger.info("daily report %s: %d must-read", result.slug, len(result.items))
            note = f"{result.slug}: {len(result.items)} must-read"
    with session_scope() as session:
        pruned = prune_lineage(session)
    if pruned:
        logger.info("pruned %d lineage rows", pruned)
    with session_scope() as session:
        raw_pruned = prune_raw_items(session, keep_days=settings.raw_items_keep_days)
    if raw_pruned:
        logger.info("pruned %d raw item(s)", raw_pruned)
    return note


def _catalog(settings: Settings) -> str:
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
    note = (
        f"{summary.models} model(s), {summary.benchmarks} benchmark(s), {summary.results} result(s)"
    )
    if summary.failed:
        # Yesterday's rows still stand — partial data is the module's contract — but the
        # ledger must not read ok when an upstream silently contributed nothing.
        raise RuntimeError(f"upstreams failed: {', '.join(summary.failed)}; kept {note}")
    return note


# A repeated health failure is suppressed by note comparison. Both sides are cut below the
# ledger's 400-character cap, minus room for the prefix, so truncation alone can never make
# an unchanged failure read as new.
_STILL_FAILING = "still failing: "
_NOTE_COMPARE_LEN = 380


def _same_failure(previous: str, note: str) -> bool:
    stripped = previous.removeprefix(_STILL_FAILING)
    return stripped[:_NOTE_COMPARE_LEN] == note[:_NOTE_COMPARE_LEN]


def _health(settings: Settings) -> str:
    """Self-check the pipeline and corpus; fail loudly on any NEW failure.

    The raise is what reaches the error reporter, and one event per state change is signal
    where one every thirty minutes is noise: an unchanged failing summary repeats quietly
    in the ledger instead, and any changed summary raises again.
    """
    from researchscout.health import overall_ok, run_health_checks, summarize
    from researchscout.store.db import session_scope
    from researchscout.store.runs import last_finished

    with session_scope() as session:
        checks = run_health_checks(session, settings)
        previous = last_finished(session, "health")
        previous_note = previous.note if previous is not None else None
    note = summarize(checks)
    if overall_ok(checks):
        return note
    if previous_note is not None and _same_failure(previous_note, note):
        return _STILL_FAILING + note
    raise RuntimeError(note)


def _record_safely(name: str, started: datetime, *, ok: bool, note: str = "") -> None:
    """Write one completed ledger row, never letting the write take the outcome down."""
    from researchscout.store.db import session_scope
    from researchscout.store.runs import record_run

    try:
        with session_scope() as session:
            record_run(
                session, name, started_at=started, finished_at=datetime.now(UTC), ok=ok, note=note
            )
    except Exception:  # noqa: BLE001 - a task that ran must not fail over its bookkeeping
        logger.warning("could not record the %s run", name, exc_info=True)


def record_started(count: int) -> None:
    """Write the scheduler's own start-up into the ledger.

    ``deploy/verify.sh`` keys on this row: a slot that passed after the newest start with no
    task run after it means the loop is stalled or dead, not merely young - the distinction
    between "the ledger fills as tasks run" and "the morning slot went missing".
    """
    now = datetime.now(UTC)
    _record_safely("scheduler", now, ok=True, note=f"started: {count} task(s)")


def record_crashed(note: str) -> None:
    """Write the scheduler's own death into the ledger, best-effort.

    ``serve all`` calls this just before exiting the process, so the ledger explains the
    restart the platform is about to perform.
    """
    _record_safely("scheduler", datetime.now(UTC), ok=False, note=note)


def _recorded(name: str, run: TaskFn) -> Callable[[], None]:
    """Wrap a task so every run lands in the ledger — opened at start, completed at finish.

    The open row is what makes a hang visible: a task that never comes back leaves
    ``finished_at`` NULL instead of leaving nothing. Failures re-raise so the loop's own
    logging stays the one place a traceback appears; the ledger keeps the fact, the timing,
    and the task's own note.
    """
    from researchscout.store.db import session_scope
    from researchscout.store.runs import record_task_finished

    def finish(run_id: int | None, started: datetime, *, ok: bool, note: str) -> None:
        if run_id is None:
            _record_safely(name, started, ok=ok, note=note)
            return
        try:
            with session_scope() as session:
                record_task_finished(
                    session, run_id, finished_at=datetime.now(UTC), ok=ok, note=note
                )
        except Exception:  # noqa: BLE001
            logger.warning("could not record the %s run", name, exc_info=True)

    def wrapped() -> None:
        from researchscout.store.runs import record_task_started

        started = datetime.now(UTC)
        run_id: int | None = None
        try:
            with session_scope() as session:
                run_id = record_task_started(session, name, started_at=started)
        except Exception:  # noqa: BLE001 - bookkeeping must not stop the task itself
            logger.warning("could not open the %s ledger row", name, exc_info=True)
        try:
            note = run() or ""
        except Exception as exc:
            finish(run_id, started, ok=False, note=str(exc) or exc.__class__.__name__)
            raise
        finish(run_id, started, ok=True, note=note)

    return wrapped


def build_tasks(settings: Settings, *, heartbeat: Heartbeat | None = None) -> list[Task]:
    """Construct the scheduler's tasks from ``settings``.

    Each task carries both an interval and, when the corresponding ``_at`` setting is present,
    a set of times of day; ``Task`` prefers the times when it has them. Keeping the interval
    on the task regardless means unsetting the times is all it takes to go back.
    """
    zone = ZoneInfo(settings.scheduler_timezone)
    pipeline_at = parse_times(settings.scheduler_pipeline_at)
    signals_at = parse_times(settings.scheduler_signals_at) or pipeline_at
    citations_at = parse_times(settings.scheduler_citations_at) or pipeline_at
    # The revisions sweep runs only when it has its own slot: unset means off, because an
    # interval default would re-walk lastUpdatedDate hourly for no reason.
    revisions_at = parse_times(settings.scheduler_revisions_at)
    daily_at = parse_times(settings.scheduler_daily_at)
    # The report describes the overnight arrivals, so it takes its own morning time; unset,
    # it stays with the daily set.
    report_at = parse_times(settings.scheduler_report_at) or daily_at

    def task(name: str, interval: float, run: TaskFn, at: tuple[clock_time, ...]) -> Task:
        return Task(name, interval, _recorded(name, run), at=at, zone=zone)

    tasks: list[Task] = []
    if settings.scheduler_batch_pipeline:
        # Ordered so a cycle flows the way a paper does: arrive, get keywords and an
        # embedding, get embedded if categorize missed it, get its text — then the signal
        # groups follow their own clocks.
        tasks += [
            task(
                "ingest",
                settings.scheduler_ingest_interval_sec,
                partial(_ingest, settings),
                pipeline_at,
            ),
            task(
                "categorize",
                settings.scheduler_categorize_interval_sec,
                partial(_categorize, settings),
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
                partial(_fulltext, settings, heartbeat),
                pipeline_at,
            ),
            task(
                "signals",
                settings.scheduler_signals_interval_sec,
                partial(_signals, settings),
                signals_at,
            ),
            task(
                "citations",
                settings.scheduler_citations_interval_sec,
                partial(_citations, settings),
                citations_at,
            ),
        ]
        if revisions_at:
            tasks.append(task("revisions", 86400.0, partial(_revisions, settings), revisions_at))
    else:
        # The one configuration in which this process schedules nothing that fetches a paper.
        # That is correct when the stream is running, and silently wrong when it is not - a
        # deployment can sit for weeks looking healthy while its corpus stands still, which is
        # what happened here. Saying so on every start-up costs one line.
        logger.warning(
            "no fetch tasks scheduled: RS_SCHEDULER_BATCH_PIPELINE is off, so new papers "
            "arrive only while `scout stream serve` is running elsewhere"
        )
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
            "report", settings.scheduler_report_interval_sec, partial(_report, settings), report_at
        ),
        task("health", settings.scheduler_health_interval_sec, partial(_health, settings), ()),
    ]
    return tasks
