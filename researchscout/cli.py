"""The ``scout`` command-line interface — the same core the HTTP API serves, from the terminal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from researchscout import __version__

app = typer.Typer(
    name="scout",
    help="ResearchScout — a source-agnostic radar for AI/ML research.",
    no_args_is_help=True,
    add_completion=False,
)

sources_app = typer.Typer(help="Inspect and test content/signal sources.", no_args_is_help=True)
db_app = typer.Typer(help="Database setup and migrations.", no_args_is_help=True)
signals_app = typer.Typer(help="Inspect the signal time series.", no_args_is_help=True)
topics_app = typer.Typer(help="Build and inspect emerging topics.", no_args_is_help=True)
serve_app = typer.Typer(help="Run ResearchScout services.", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluate retrieval quality and embedding speed.", no_args_is_help=True)
stream_app = typer.Typer(help="Run and observe the streaming pipeline.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(db_app, name="db")
app.add_typer(signals_app, name="signals")
app.add_typer(topics_app, name="topics")
app.add_typer(serve_app, name="serve")
app.add_typer(eval_app, name="eval")
app.add_typer(stream_app, name="stream")


@app.command()
def version() -> None:
    """Print the ResearchScout version."""
    typer.echo(__version__)


@app.command()
def ingest(
    since: Annotated[datetime, typer.Option(help="Ingest items submitted on/after this date.")],
    source: Annotated[str, typer.Option(help="Source name.")] = "arxiv",
    category: Annotated[
        list[str] | None,
        typer.Option(
            help="Category filter; repeat for several, e.g. --category cs.LG --category math.CO."
        ),
    ] = None,
    max_items: Annotated[int | None, typer.Option("--max", help="Max items to ingest.")] = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Continue from the saved cursor for the same window.")
    ] = False,
) -> None:
    """Fetch a source, normalize, dedup, and store (idempotent, replayable)."""
    import httpx

    from researchscout.ingest.pipeline import run_ingest
    from researchscout.sources import get_source
    from researchscout.sources.arxiv import ArxivSource
    from researchscout.store.db import session_scope

    try:
        src = get_source(source)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if category and isinstance(src, ArxivSource):
        src.categories = list(category)

    try:
        with session_scope() as session:
            summary = run_ingest(session, src, since, max_items=max_items, resume=resume)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = " (rate limited — wait and retry)" if code == 429 else ""
        typer.secho(f"{source}: request failed with HTTP {code}{hint}.", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        typer.secho(f"{source}: request failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.secho(
        f"{summary.source}: fetched={summary.fetched} new={summary.new_papers} "
        f"collapsed={summary.collapsed} signals={summary.signals} raw={summary.raw_stored}",
        fg=typer.colors.GREEN,
    )


@app.command()
def index(
    batch_size: Annotated[int, typer.Option("--batch", help="Embedding batch size.")] = 64,
    model: Annotated[
        str | None, typer.Option(help="Embedding model id (defaults to RS_EMBEDDING_MODEL).")
    ] = None,
    chunks: Annotated[
        bool, typer.Option("--chunks", help="Also chunk and embed fetched full text.")
    ] = False,
) -> None:
    """Embed stored papers into the pgvector index (and full-text chunks with --chunks)."""
    from researchscout.config import get_settings
    from researchscout.embed.local import LocalEmbedder
    from researchscout.store.db import session_scope
    from researchscout.store.vectors import index_papers

    embedder = LocalEmbedder(model or get_settings().embedding_model)
    with session_scope() as session:
        count = index_papers(session, embedder, batch_size=batch_size)
        chunk_count = 0
        if chunks:
            from researchscout.store.chunks import index_chunks

            chunk_count = index_chunks(session, embedder, batch_size=batch_size)
    message = f"Embedded {count} paper(s) with {embedder.model_id}."
    if chunks:
        message += f" Embedded {chunk_count} chunk(s)."
    typer.secho(message, fg=typer.colors.GREEN)


@app.command()
def fulltext(
    limit: Annotated[int, typer.Option("--limit", help="Papers to fetch this run.")] = 25,
) -> None:
    """Fetch full text for stored papers (saved and interacted-with first), politely paced.

    arXiv HTML first, ar5iv fallback; papers with neither are marked checked so the batch
    never retries them. Full-content harvesting is not permitted, so keep batches modest.
    """
    import time

    from sqlalchemy import select

    from researchscout.config import get_settings
    from researchscout.fulltext import fetch_full_text
    from researchscout.store.db import session_scope
    from researchscout.store.models import EventRow, SavedPaperRow
    from researchscout.store.papers import papers_missing_full_text, set_full_text

    delay = get_settings().arxiv_page_delay_sec
    fetched = unavailable = 0
    with session_scope() as session:
        priority = set(session.execute(select(SavedPaperRow.paper_id)).scalars()) | set(
            session.execute(select(EventRow.paper_id).distinct()).scalars()
        )
        pending = papers_missing_full_text(session, limit=limit, first=sorted(priority))
        for index, (paper_id, arxiv_id) in enumerate(pending):
            if index and delay > 0:
                time.sleep(delay)
            text = fetch_full_text(arxiv_id)
            set_full_text(session, paper_id, text or "")
            if text is None:
                unavailable += 1
            else:
                fetched += 1
    typer.secho(
        f"full text: fetched={fetched} unavailable={unavailable} of {len(pending)} attempted",
        fg=typer.colors.GREEN,
    )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query / topic.")],
    days: Annotated[int | None, typer.Option(help="Freshness window in days.")] = None,
    category: Annotated[str | None, typer.Option(help="Filter to a category, e.g. cs.LG.")] = None,
    k: Annotated[int, typer.Option("-k", "--top-k", help="Number of results.")] = 10,
) -> None:
    """Freshness-aware semantic search over stored papers."""
    from researchscout.embed.factory import default_embedder
    from researchscout.retrieve.search import retrieve
    from researchscout.store.db import session_scope

    embedder = default_embedder()
    categories = [category] if category else None
    with session_scope() as session:
        results = retrieve(session, embedder, query, k=k, days=days, categories=categories)

    if not results:
        typer.secho("No results in the freshness window.", fg=typer.colors.YELLOW)
        return
    for item in results:
        paper = item.paper
        typer.echo(f"  {item.score:.3f}  {paper.id}  {paper.published_at:%Y-%m-%d}  {paper.title}")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Your question / topic.")],
    days: Annotated[int | None, typer.Option(help="Freshness window in days.")] = None,
    k: Annotated[int, typer.Option("-k", help="Papers to ground the answer on.")] = 8,
    agentic: Annotated[
        bool | None,
        typer.Option(help="Decompose the question and follow references (multi-hop)."),
    ] = None,
) -> None:
    """Answer a question with a grounded, cited summary of recent papers."""
    from researchscout.answer import answer
    from researchscout.config import get_settings
    from researchscout.embed.factory import default_embedder
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope

    use_agentic = agentic if agentic is not None else get_settings().agentic_ask
    embedder = default_embedder()
    llm = OpenAICompatLLM()
    with session_scope() as session:
        result = answer(session, embedder, llm, question, k=k, days=days, agentic=use_agentic)

    typer.echo(result.text)
    if result.cited:
        typer.secho(f"\nCited: {', '.join(result.cited)}", fg=typer.colors.GREEN)
    if result.hallucinated:
        dropped = ", ".join(result.hallucinated)
        typer.secho(f"Dropped (not retrieved): {dropped}", fg=typer.colors.YELLOW)


def _warm_models() -> None:
    """Load the retrieval models before serving so no request pays the lazy loads.

    The first ask otherwise stacks the embedder weights, the sklearn import, and (when
    reranking is on) the cross-encoder into one multi-second stall that reads as the chat
    hanging. Everything touched here is a process-wide singleton, so the serving process
    reuses exactly these instances.
    """
    import time

    from researchscout.config import get_settings
    from researchscout.embed.factory import default_embedder

    started = time.perf_counter()
    default_embedder().embed_query("warmup")
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS  # noqa: F401

    if get_settings().rerank_enabled:
        from researchscout.rerank import get_reranker

        get_reranker()
    typer.echo(f"models warm in {time.perf_counter() - started:.1f}s")


@serve_app.command("api")
def serve_api(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (dev).")] = False,
) -> None:
    """Run the HTTP API with uvicorn (requires the `api` extra).

    Models are warmed first; under ``--reload`` uvicorn re-imports the app in a child
    process, so dev runs still pay the loads there.
    """
    import uvicorn

    if not reload:
        _warm_models()
    uvicorn.run("researchscout.api.main:app", host=host, port=port, reload=reload)


@serve_app.command("scheduler")
def serve_scheduler(
    once: Annotated[
        bool, typer.Option("--once", help="Run one full pass and exit (for cron / a CronJob).")
    ] = False,
) -> None:
    """Run the refresh loop: weekly digest, topic rebuild, and the daily report."""
    import signal as signalmod
    import threading
    from contextlib import suppress

    from researchscout.config import get_settings
    from researchscout.scheduler import Scheduler, build_tasks, record_started
    from researchscout.trace import configure_logging

    configure_logging()
    settings = get_settings()
    tasks = build_tasks(settings)
    scheduler = Scheduler(tasks, tick_sec=settings.scheduler_tick_sec)

    if once:
        scheduler.run_pass()
        return

    record_started(len(tasks))
    stop = threading.Event()
    for sig in (signalmod.SIGINT, signalmod.SIGTERM):
        with suppress(ValueError):  # not the main thread — fall back to KeyboardInterrupt
            signalmod.signal(sig, lambda *_: stop.set())
    typer.secho("scheduler running; Ctrl-C to stop", fg=typer.colors.GREEN)
    try:
        scheduler.run_forever(stop.is_set)
    except KeyboardInterrupt:
        pass
    typer.secho("scheduler stopped", fg=typer.colors.YELLOW)


@app.command()
def digest(
    days: Annotated[
        int | None, typer.Option(help="Window in days (default from settings).")
    ] = None,
    k: Annotated[int | None, typer.Option("-k", help="Papers to include.")] = None,
) -> None:
    """Build and publish this week's digest (LLM summary over the window's top papers)."""
    from researchscout.config import get_settings
    from researchscout.digest import build_digest
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.digests import upsert_digest

    settings = get_settings()
    window = days if days is not None else settings.digest_days
    top_k = k if k is not None else settings.digest_top_k
    llm = OpenAICompatLLM()
    with session_scope() as session:
        result = build_digest(session, llm, days=window, k=top_k)
        if result is None:
            typer.secho(f"No papers in the last {window}d — no digest.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        upsert_digest(session, result)
    typer.secho(
        f"published {result.slug}: {len(result.items)} papers, {len(result.cited)} cited",
        fg=typer.colors.GREEN,
    )


@stream_app.command("serve")
def stream_serve(
    producer_only: Annotated[
        bool, typer.Option("--producer-only", help="Run only the polling producers.")
    ] = False,
    worker_only: Annotated[
        bool, typer.Option("--worker-only", help="Run only the processing worker.")
    ] = False,
) -> None:
    """Run the streaming pipeline: producers polling sources plus the processing worker."""
    from researchscout.stream.serve import run_stream
    from researchscout.trace import configure_logging

    if producer_only and worker_only:
        typer.secho("choose at most one of --producer-only / --worker-only", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    configure_logging()
    run_stream(producer_only=producer_only, worker_only=worker_only)


@stream_app.command("tail")
def stream_tail(
    topic: Annotated[
        str, typer.Option(help="Which topic to watch: raw, fulltext, parsed, or enriched.")
    ] = "enriched",
    from_beginning: Annotated[
        bool, typer.Option("--from-beginning", help="Start at the oldest retained packet.")
    ] = False,
) -> None:
    """Watch packets flow through a pipeline topic (Ctrl-C to stop)."""
    from researchscout.config import get_settings
    from researchscout.stream.broker import StreamTopics
    from researchscout.stream.tail import iter_lines

    settings = get_settings()
    topics = StreamTopics.for_prefix(settings.kafka_topic_prefix)
    names = {
        "raw": topics.raw,
        "fulltext": topics.raw_fulltext,
        "parsed": topics.parsed,
        "enriched": topics.enriched,
    }
    if topic not in names:
        typer.secho("topic must be raw, fulltext, parsed, or enriched", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        for line in iter_lines(
            names[topic], bootstrap=settings.kafka_bootstrap, from_beginning=from_beginning
        ):
            typer.echo(line)
    except KeyboardInterrupt:  # a clean stop, not an error
        pass


@app.command()
def report() -> None:
    """Build and publish today's report (deterministic; no LLM needed)."""
    from researchscout.report import build_daily_report
    from researchscout.store.db import session_scope
    from researchscout.store.digests import upsert_digest

    with session_scope() as session:
        result = build_daily_report(session)
        if result is None:
            typer.secho("No papers in the last day — no report.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        upsert_digest(session, result)
    typer.secho(
        f"published {result.slug}: {len(result.items)} must-read of the day",
        fg=typer.colors.GREEN,
    )


@sources_app.command("list")
def sources_list(
    probe: Annotated[bool, typer.Option(help="Probe each source's health.")] = False,
) -> None:
    """List registered sources with their kind, enabled state, and (optionally) health."""
    from researchscout.sources.base import registered_sources, source_config

    for cls in registered_sources():
        cfg = source_config(cls.name)
        enabled = bool(cfg.get("enabled", False))
        line = f"{cls.name:<16} kind={cls.kind:<8} enabled={str(enabled).lower()}"
        if probe:
            line += f"  health={cls().health()}"
        typer.echo(line)


@sources_app.command("test")
def sources_test(
    name: Annotated[str, typer.Argument(help="Source name, e.g. 'arxiv'.")],
    since: Annotated[datetime, typer.Option(help="Fetch items submitted on/after this date.")],
    category: Annotated[str | None, typer.Option(help="Category override, e.g. cs.LG.")] = None,
    limit: Annotated[int, typer.Option(help="Max items to print.")] = 10,
) -> None:
    """Fetch + normalize from one source and print the results (no persistence)."""
    from researchscout.schema import Signal
    from researchscout.sources import get_source
    from researchscout.sources.arxiv import ArxivSource

    try:
        src = get_source(name)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if category is not None and isinstance(src, ArxivSource):
        src.categories = [category]

    items, _ = src.fetch(since, cursor=None)
    items = items[:limit]
    typer.secho(f"{src.name}: fetched {len(items)} item(s)", fg=typer.colors.GREEN)
    for raw in items:
        obj = src.normalize(raw)
        if isinstance(obj, Signal):
            typer.echo(f"  signal {obj.type} paper={obj.paper_id} value={obj.value}")
        else:
            authors = ", ".join(a.name for a in obj.authors[:3])
            typer.echo(f"  {obj.id}  {obj.published_at:%Y-%m-%d}  {obj.title}")
            typer.echo(f"      {authors}  [{', '.join(obj.categories)}]")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations (alembic upgrade head)."""
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")
    typer.secho("Database is at head.", fg=typer.colors.GREEN)


@app.command("catalog")
def catalog_refresh() -> None:
    """Refresh the model and benchmark catalogue from Epoch AI and Hugging Face.

    Runs on its own daily; this is the manual way in. Both upstreams are keyless and fail soft,
    so a refresh that cannot reach one of them leaves its rows as they were.
    """
    from researchscout.catalog import counts, refresh_catalog
    from researchscout.store.db import session_scope
    from researchscout.trace import configure_logging

    configure_logging()
    with session_scope() as session:
        summary = refresh_catalog(session)
        totals = counts(session)
    typer.echo(
        f"wrote {summary.models} model(s), {summary.benchmarks} benchmark(s), "
        f"{summary.results} score(s); linked {summary.linked} to papers"
    )
    typer.echo(
        f"catalogue now holds {totals['models']} model(s), {totals['benchmarks']} benchmark(s), "
        f"{totals['results']} score(s), {totals['linked']} with a paper here"
    )
    if summary.failed:
        typer.secho(
            f"upstream(s) unavailable, existing rows kept: {', '.join(summary.failed)}",
            fg=typer.colors.YELLOW,
        )


@db_app.command("prune-scope")
def db_prune_scope(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would go without deleting anything.")
    ] = False,
) -> None:
    """Delete papers outside what this radar covers (see researchscout/taxonomy.py).

    For a corpus gathered before the scope narrowed. Deletion cascades to embeddings, chunks,
    signals, citation edges and saved rows, and cannot be undone without a re-ingest -- so run
    it with --dry-run first, and take a backup if the corpus is one you care about.
    """
    from researchscout.store.db import session_scope
    from researchscout.store.scope import (
        count_out_of_scope,
        delete_out_of_scope,
        sample_out_of_scope,
    )

    with session_scope() as session:
        total = count_out_of_scope(session)
        if total == 0:
            typer.secho("Every stored paper is in scope.", fg=typer.colors.GREEN)
            return
        typer.echo(f"{total} paper(s) fall outside the scope rule, for example:")
        for paper_id, title, primary in sample_out_of_scope(session):
            typer.echo(f"  {primary or 'none':<12} {paper_id:<24} {title[:60]}")
        if dry_run:
            typer.secho("Dry run: nothing was deleted.", fg=typer.colors.YELLOW)
            return
        typer.confirm(f"Delete {total} paper(s) and everything derived from them?", abort=True)
        deleted = delete_out_of_scope(session)
    typer.secho(f"Deleted {deleted} paper(s).", fg=typer.colors.GREEN)


@signals_app.command("show")
def signals_show(
    paper_id: Annotated[str, typer.Argument(help="Canonical paper id.")],
    days: Annotated[int, typer.Option(help="Look-back window in days.")] = 90,
) -> None:
    """Show the signal time series for a paper."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from researchscout.store.db import session_scope
    from researchscout.store.models import SignalRow

    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as session:
        rows = (
            session.execute(
                select(SignalRow)
                .where(SignalRow.paper_id == paper_id, SignalRow.observed_at >= since)
                .order_by(SignalRow.type, SignalRow.observed_at)
            )
            .scalars()
            .all()
        )

    if not rows:
        typer.secho(f"No signals for {paper_id} in the last {days}d.", fg=typer.colors.YELLOW)
        return
    for row in rows:
        typer.echo(
            f"  {row.observed_at:%Y-%m-%d %H:%M}  {row.type:<16} {row.source:<14} {row.value}"
        )


@signals_app.command("score")
def signals_score(
    paper_id: Annotated[str, typer.Argument(help="Canonical paper id.")],
) -> None:
    """Show a paper's breakthrough score and its per-signal contributions."""
    from researchscout.score import breakthrough
    from researchscout.store.db import session_scope

    with session_scope() as session:
        result = breakthrough(session, paper_id)

    typer.secho(f"breakthrough {result.total:.3f}  {paper_id}", fg=typer.colors.GREEN)
    if not result.contributions:
        typer.secho("  no signals observed yet", fg=typer.colors.YELLOW)
        return
    for name, value in sorted(result.contributions.items(), key=lambda kv: kv[1], reverse=True):
        typer.echo(f"  {name:<18} {value:+.3f}")


@topics_app.command("build")
def topics_build(
    days: Annotated[
        int | None, typer.Option(help="Window in days (default from settings).")
    ] = None,
) -> None:
    """Cluster recent papers into emerging topics and store them (needs the LLM up)."""
    from researchscout.cluster import build_topics
    from researchscout.config import get_settings
    from researchscout.embed.factory import default_embedder
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.topics import replace_topics

    settings = get_settings()
    window = days if days is not None else settings.cluster_window_days
    embedder = default_embedder()
    llm = OpenAICompatLLM()
    with session_scope() as session:
        topics = build_topics(
            session,
            embedder,
            llm,
            days=window,
            threshold=settings.cluster_distance_threshold,
            algo=settings.cluster_algo,
        )
        replace_topics(session, topics)
    typer.secho(f"built {len(topics)} topic(s)", fg=typer.colors.GREEN)
    for topic in topics:
        typer.echo(f"  {topic.score:7.3f}  {topic.size:>3}  {topic.label}")


@eval_app.command("draft")
def eval_draft(
    out: Annotated[Path, typer.Option("--out", help="Where to write the YAML query set.")] = Path(
        "config/eval_queries.yaml"
    ),
    n: Annotated[int, typer.Option("--n", help="How many known-item cases to draft.")] = 20,
    days: Annotated[int, typer.Option(help="Corpus window to draft from, in days.")] = 3650,
) -> None:
    """Draft a known-item query set (each title -> its paper); hand-edit before trusting it."""
    from researchscout.evaluate import EvalCase, save_cases
    from researchscout.store.db import session_scope
    from researchscout.store.facets import PaperFacets
    from researchscout.store.papers import list_papers

    with session_scope() as session:
        papers = list_papers(session, facets=PaperFacets(days=days), limit=n)
    if not papers:
        typer.secho("No papers in the window - ingest first.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    cases = [EvalCase(query=paper.title, relevant=(paper.id,)) for paper in papers]
    save_cases(out, cases)
    typer.secho(
        f"Wrote {len(cases)} case(s) to {out}. Edit the queries before trusting the numbers.",
        fg=typer.colors.GREEN,
    )


@eval_app.command("retrieval")
def eval_retrieval(
    queries: Annotated[Path, typer.Option(help="YAML query set (see scout eval draft).")] = Path(
        "config/eval_queries.yaml"
    ),
    k: Annotated[int, typer.Option("-k", "--top-k", help="Cutoff for the metrics.")] = 10,
    days: Annotated[
        int, typer.Option(help="Freshness window; wide by default so older papers stay in.")
    ] = 3650,
    model: Annotated[
        str | None,
        typer.Option(help="Embedding model id (must be indexed; see scout index --model)."),
    ] = None,
    rerank: Annotated[
        bool, typer.Option("--rerank/--no-rerank", help="Include the cross-encoder pass.")
    ] = False,
) -> None:
    """Score retrieval against a labeled query set (Recall@k and nDCG@k)."""
    from researchscout.config import get_settings
    from researchscout.embed.local import LocalEmbedder
    from researchscout.evaluate import evaluate_cases, load_cases
    from researchscout.retrieve.search import retrieve
    from researchscout.store.db import session_scope

    cases = load_cases(queries)
    if not cases:
        typer.secho(f"No cases in {queries} - run scout eval draft.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    embedder = LocalEmbedder(model or get_settings().embedding_model)
    with session_scope() as session:

        def ranker(query: str) -> list[str]:
            hits = retrieve(session, embedder, query, k=k, days=days, use_rerank=rerank)
            return [item.paper.id for item in hits]

        report = evaluate_cases(cases, ranker, k=k)
    for result in report.cases:
        typer.echo(f"  recall {result.recall:.2f}  ndcg {result.ndcg:.2f}  {result.query[:70]}")
    typer.secho(
        f"{embedder.model_id}  mean recall@{k} {report.mean_recall:.3f}"
        f"  mean ndcg@{k} {report.mean_ndcg:.3f}  ({len(report.cases)} cases)",
        fg=typer.colors.GREEN,
    )


@eval_app.command("embed-speed")
def eval_embed_speed(
    model: Annotated[
        str | None, typer.Option(help="Embedding model id (defaults to RS_EMBEDDING_MODEL).")
    ] = None,
    device: Annotated[
        str | None, typer.Option(help="cpu or mps (default: mps when available).")
    ] = None,
    backend: Annotated[
        str,
        typer.Option(
            help="torch or onnx (onnx needs a manual 'uv pip install optimum[onnxruntime]'; "
            "kept out of the lock because optimum pins an older transformers)."
        ),
    ] = "torch",
    batch_size: Annotated[int, typer.Option("--batch", help="Embedding batch size.")] = 64,
    docs: Annotated[int, typer.Option(help="How many documents to embed.")] = 256,
) -> None:
    """Benchmark document-embedding throughput over stored title+abstract texts."""
    from researchscout.config import get_settings
    from researchscout.embed.local import LocalEmbedder
    from researchscout.evaluate import benchmark_embedding
    from researchscout.store.db import session_scope
    from researchscout.store.facets import PaperFacets
    from researchscout.store.papers import list_papers

    with session_scope() as session:
        papers = list_papers(session, facets=PaperFacets(days=3650), limit=min(docs, 500))
    if not papers:
        typer.secho("No papers stored - ingest first.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    texts = [f"{paper.title}\n\n{paper.abstract}" for paper in papers]
    while len(texts) < docs:
        texts.extend(texts)
    texts = texts[:docs]
    embedder = LocalEmbedder(
        model or get_settings().embedding_model, device=device, backend=backend
    )
    embedder.embed_documents(texts[:8])  # warmup: model load stays out of the timing
    rate = benchmark_embedding(embedder.embed_documents, texts, batch_size=batch_size)
    typer.secho(
        f"{embedder.model_id}  device {device or 'auto'}  backend {backend}"
        f"  {rate:.1f} docs/sec  (batch {batch_size}, {len(texts)} docs)",
        fg=typer.colors.GREEN,
    )
