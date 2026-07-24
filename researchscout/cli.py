"""The ``scout`` command-line interface.

Commands that aren't implemented yet are stubs that print which PR will implement them, so the full
command surface is already visible from ``scout --help``. Each later PR fills in its command.
"""

from __future__ import annotations

from datetime import datetime
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
serve_app = typer.Typer(help="Run ResearchScout services.", no_args_is_help=True)
jobs_app = typer.Typer(help="Emit event-plane jobs.", no_args_is_help=True)
worker_app = typer.Typer(help="Run event-plane workers.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(db_app, name="db")
app.add_typer(signals_app, name="signals")
app.add_typer(serve_app, name="serve")
app.add_typer(jobs_app, name="jobs")
app.add_typer(worker_app, name="worker")


def _todo(command: str, pr: str) -> None:
    typer.secho(
        f"`scout {command}` is not implemented yet — arrives in {pr}.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the ResearchScout version."""
    typer.echo(__version__)


@app.command()
def ingest(
    since: Annotated[datetime, typer.Option(help="Ingest items submitted on/after this date.")],
    source: Annotated[str, typer.Option(help="Source name.")] = "arxiv",
    category: Annotated[str | None, typer.Option(help="Category override, e.g. cs.LG.")] = None,
    max_items: Annotated[int | None, typer.Option("--max", help="Max items to ingest.")] = None,
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

    if category is not None and isinstance(src, ArxivSource):
        src.categories = [category]

    try:
        with session_scope() as session:
            summary = run_ingest(session, src, since, max_items=max_items)
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
) -> None:
    """Embed stored papers into the pgvector index."""
    from researchscout.embed.local import LocalEmbedder
    from researchscout.store.db import session_scope
    from researchscout.store.vectors import index_papers

    embedder = LocalEmbedder()
    with session_scope() as session:
        count = index_papers(session, embedder, batch_size=batch_size)
    typer.secho(f"Embedded {count} paper(s) with {embedder.model_id}.", fg=typer.colors.GREEN)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query / topic.")],
    days: Annotated[int | None, typer.Option(help="Freshness window in days.")] = None,
    category: Annotated[str | None, typer.Option(help="Filter to a category, e.g. cs.LG.")] = None,
    k: Annotated[int, typer.Option("-k", "--top-k", help="Number of results.")] = 10,
) -> None:
    """Freshness-aware semantic search over stored papers."""
    from researchscout.embed.local import LocalEmbedder
    from researchscout.retrieve.search import retrieve
    from researchscout.store.db import session_scope

    embedder = LocalEmbedder()
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
) -> None:
    """Answer a question with a grounded, cited summary of recent papers."""
    from researchscout.answer import answer
    from researchscout.embed.local import LocalEmbedder
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope

    embedder = LocalEmbedder()
    llm = OpenAICompatLLM()
    with session_scope() as session:
        result = answer(session, embedder, llm, question, k=k, days=days)

    typer.echo(result.text)
    if result.cited:
        typer.secho(f"\nCited: {', '.join(result.cited)}", fg=typer.colors.GREEN)
    if result.hallucinated:
        dropped = ", ".join(result.hallucinated)
        typer.secho(f"Dropped (not retrieved): {dropped}", fg=typer.colors.YELLOW)


@serve_app.command("api")
def serve_api(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (dev).")] = False,
) -> None:
    """Run the HTTP API with uvicorn (requires the `api` extra)."""
    import uvicorn

    uvicorn.run("researchscout.api.main:app", host=host, port=port, reload=reload)


@serve_app.command("scheduler")
def serve_scheduler(
    once: Annotated[
        bool, typer.Option("--once", help="Run one full pass and exit (for cron / a CronJob).")
    ] = False,
) -> None:
    """Run the refresh loop: ingest sources, embed papers, refresh signals, rebuild the digest."""
    import logging
    import signal as signalmod
    import threading
    from contextlib import suppress

    from researchscout.config import get_settings
    from researchscout.scheduler import Scheduler, build_tasks

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    scheduler = Scheduler(build_tasks(settings), tick_sec=settings.scheduler_tick_sec)

    if once:
        scheduler.run_pass()
        return

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
    from researchscout.events.publish import publish_digest_published
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
    publish_digest_published(result.slug, result.title, result.period_start, result.period_end)
    typer.secho(
        f"published {result.slug}: {len(result.items)} papers, {len(result.cited)} cited",
        fg=typer.colors.GREEN,
    )


@jobs_app.command("emit-watchlist")
def jobs_emit_watchlist(
    since_days: Annotated[int, typer.Option(help="Ingest window per job, in days.")] = 1,
) -> None:
    """Emit one ingest job per enabled Airtable watchlist row (the scheduler entrypoint)."""
    from datetime import UTC, timedelta

    from pyairtable import Api

    from researchscout.config import get_settings
    from researchscout.events.kafka import ensure_topics, producer
    from researchscout.events.schemas import TOPIC_INGEST_JOBS, TOPIC_PAPERS_NEW
    from researchscout.ingest.watchlist import jobs_from_rows

    settings = get_settings()
    if not settings.airtable_api_key or not settings.airtable_base_id:
        typer.secho(
            "RS_AIRTABLE_API_KEY and RS_AIRTABLE_BASE_ID are required.", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)
    table = Api(settings.airtable_api_key).table(
        settings.airtable_base_id, settings.airtable_watchlist_table
    )
    since = datetime.now(UTC) - timedelta(days=since_days)
    jobs = jobs_from_rows(table.all(), since=since)
    if not jobs:
        typer.secho("Watchlist has no enabled rows.", fg=typer.colors.YELLOW)
        return
    ensure_topics([TOPIC_INGEST_JOBS, TOPIC_PAPERS_NEW])
    client = producer()
    for job in jobs:
        client.produce(
            TOPIC_INGEST_JOBS, key=job.source.encode(), value=job.model_dump_json().encode()
        )
    client.flush()
    typer.secho(f"emitted {len(jobs)} watchlist job(s)", fg=typer.colors.GREEN)


@jobs_app.command("emit-ingest")
def jobs_emit_ingest(
    since: Annotated[datetime, typer.Option(help="Ingest items submitted on/after this date.")],
    source: Annotated[str, typer.Option(help="Source name.")] = "arxiv",
    category: Annotated[str | None, typer.Option(help="Category override, e.g. cs.LG.")] = None,
    max_items: Annotated[int | None, typer.Option("--max", help="Max items to ingest.")] = None,
) -> None:
    """Publish an ingest job to the event plane (requires the `kafka` extra)."""
    from researchscout.events.kafka import ensure_topics, producer
    from researchscout.events.schemas import TOPIC_INGEST_JOBS, TOPIC_PAPERS_NEW, IngestJob

    job = IngestJob(
        source=source,
        since=since,
        max_items=max_items,
        categories=[category] if category else None,
    )
    ensure_topics([TOPIC_INGEST_JOBS, TOPIC_PAPERS_NEW])
    client = producer()
    client.produce(TOPIC_INGEST_JOBS, key=source.encode(), value=job.model_dump_json().encode())
    client.flush()
    typer.secho(f"emitted ingest job for {source} since {since:%Y-%m-%d}", fg=typer.colors.GREEN)


@worker_app.command("ingest")
def worker_ingest() -> None:
    """Consume ingest jobs and store papers, publishing new ones to papers.new."""
    import logging

    from researchscout.workers.ingest_worker import run

    logging.basicConfig(level=logging.INFO)
    run()


@worker_app.command("embed")
def worker_embed() -> None:
    """Consume papers.new and index embeddings."""
    import logging

    from researchscout.workers.embed_worker import run

    logging.basicConfig(level=logging.INFO)
    run()


@worker_app.command("airtable")
def worker_airtable() -> None:
    """Consume papers.saved and mirror reading lists into Airtable."""
    import logging

    from researchscout.workers.airtable_sync import run

    logging.basicConfig(level=logging.INFO)
    run()


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
