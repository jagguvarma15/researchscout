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
app.add_typer(sources_app, name="sources")
app.add_typer(db_app, name="db")
app.add_typer(signals_app, name="signals")


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
def ingest() -> None:
    """Fetch a source, normalize, dedup, and store (idempotent, replayable)."""
    _todo("ingest", "PR 04")


@app.command()
def index() -> None:
    """Embed stored papers into the pgvector index."""
    _todo("index", "PR 05")


@app.command()
def search() -> None:
    """Freshness-aware semantic search over stored papers."""
    _todo("search", "PR 06")


@app.command()
def ask() -> None:
    """Answer a question with a grounded, cited summary of recent papers."""
    _todo("ask", "PR 07")


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
def signals_show() -> None:
    """Show the signal time series for a paper."""
    _todo("signals show", "PR 08")
