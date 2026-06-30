"""The ``scout`` command-line interface.

Most commands are stubs at this stage: they print which PR will implement them, so the full
command surface is already visible from ``scout --help``. Each later PR fills in its command.
"""

from __future__ import annotations

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
def sources_list() -> None:
    """List registered sources and their enabled/health state."""
    _todo("sources list", "PR 02")


@sources_app.command("test")
def sources_test() -> None:
    """Fetch + normalize from one source and print the results (no persistence)."""
    _todo("sources test", "PR 02")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations (alembic upgrade head)."""
    _todo("db upgrade", "PR 03")


@signals_app.command("show")
def signals_show() -> None:
    """Show the signal time series for a paper."""
    _todo("signals show", "PR 08")
