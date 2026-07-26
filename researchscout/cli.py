"""The ``scout`` command-line interface — the same core the HTTP API serves, from the terminal."""

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
topics_app = typer.Typer(help="Build and inspect emerging topics.", no_args_is_help=True)
serve_app = typer.Typer(help="Run ResearchScout services.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(db_app, name="db")
app.add_typer(signals_app, name="signals")
app.add_typer(topics_app, name="topics")
app.add_typer(serve_app, name="serve")


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
    agentic: Annotated[
        bool | None,
        typer.Option(help="Decompose the question and follow references (multi-hop)."),
    ] = None,
) -> None:
    """Answer a question with a grounded, cited summary of recent papers."""
    from researchscout.answer import answer
    from researchscout.config import get_settings
    from researchscout.embed.local import LocalEmbedder
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope

    use_agentic = agentic if agentic is not None else get_settings().agentic_ask
    embedder = LocalEmbedder()
    llm = OpenAICompatLLM()
    with session_scope() as session:
        result = answer(session, embedder, llm, question, k=k, days=days, agentic=use_agentic)

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


@topics_app.command("build")
def topics_build(
    days: Annotated[
        int | None, typer.Option(help="Window in days (default from settings).")
    ] = None,
) -> None:
    """Cluster recent papers into emerging topics and store them (needs the LLM up)."""
    from researchscout.cluster import build_topics
    from researchscout.config import get_settings
    from researchscout.embed.local import LocalEmbedder
    from researchscout.llm.openai_compat import OpenAICompatLLM
    from researchscout.store.db import session_scope
    from researchscout.store.topics import replace_topics

    settings = get_settings()
    window = days if days is not None else settings.cluster_window_days
    embedder = LocalEmbedder()
    llm = OpenAICompatLLM()
    with session_scope() as session:
        topics = build_topics(
            session, embedder, llm, days=window, threshold=settings.cluster_distance_threshold
        )
        replace_topics(session, topics)
    typer.secho(f"built {len(topics)} topic(s)", fg=typer.colors.GREEN)
    for topic in topics:
        typer.echo(f"  {topic.score:7.3f}  {topic.size:>3}  {topic.label}")
