"""Reading and writing the model and benchmark catalogue.

Writes are upserts keyed by slug, so a refresh converges rather than accumulating: running it
twice in a day leaves the same rows, and a model that gains a Hugging Face repo on Tuesday keeps
the Epoch AI facts it arrived with on Monday. Nothing here deletes -- an upstream that drops a
row for a week should not take it off the site, and the refresh timestamp is how staleness shows.

Reads are shaped for the two pages that use them: a filterable model list, and a benchmark with
its leaderboard.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Any, Literal

from sqlalchemy import ColumnElement, CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.providers import ProviderConfig
from researchscout.store.models import (
    AiModelRow,
    BenchmarkResultRow,
    BenchmarkRow,
    PaperRow,
)

_PUNCTUATION = re.compile(r"[^a-z0-9]+")

#: One score as ``replace_benchmark_results`` takes it: model name, score, when, from where.
BenchmarkResult = tuple[str, float, date_type | None, str | None]


def _rows(result: object) -> int:
    """The affected-row count of a DML statement, which only a CursorResult carries."""
    return int(result.rowcount) if isinstance(result, CursorResult) else 0


def slug(name: str) -> str:
    """A stable key for a model or benchmark name.

    Case, punctuation and accents vary between the two upstreams for the same thing -- "GPT-4o"
    against "GPT 4o", "Llama 3" against "LLaMA-3" -- and the slug is what makes them one row.
    Deliberately lossy: it is a join key, never something shown.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return _PUNCTUATION.sub("-", folded.lower()).strip("-")


@dataclass
class ModelUpsert:
    """The fields a refresh writes for one model; None leaves the stored value alone."""

    name: str
    organization: str | None = None
    publication_date: date_type | None = None
    domains: str | None = None
    task: str | None = None
    parameters: float | None = None
    training_compute_flop: float | None = None
    accessibility: str | None = None
    open_weights: bool | None = None
    link: str | None = None
    paper_id: str | None = None
    hf_repo: str | None = None
    hf_downloads: int | None = None
    hf_likes: int | None = None
    source: str = ""


_MODEL_FIELDS = (
    "organization",
    "publication_date",
    "domains",
    "task",
    "parameters",
    "training_compute_flop",
    "accessibility",
    "open_weights",
    "link",
    "paper_id",
    "hf_repo",
    "hf_downloads",
    "hf_likes",
)


def upsert_models(
    session: Session,
    models: Sequence[ModelUpsert],
    *,
    authoritative: frozenset[str] = frozenset(),
) -> int:
    """Insert or merge models by slug; returns how many rows were written.

    ``authoritative`` names the fields this batch's source owns. Those replace what is stored;
    every other field only fills a gap it finds empty.

    That distinction is the whole point. "A None never overwrites" sounds like enough, and is
    not: both upstreams supply an organisation, a task and a link, so the merge was decided by
    whichever refresh ran second. Hugging Face runs second, so it was quietly replacing Epoch
    AI's paper link with a repository URL, its "Alibaba" with "Qwen", and -- worst -- its
    weight flag with "open" for any closed model whose name happened to slug the same as some
    repository. Ordering no longer decides anything.

    Written as one statement over many parameter sets rather than one statement per model: a
    refresh carries about a thousand models and three thousand scores, and that was three
    thousand round trips.
    """
    now = datetime.now(UTC)
    # Last wins within a batch, as before - and deduplicating here is also what keeps the
    # executemany below from touching one row twice in a single statement.
    by_key: dict[str, ModelUpsert] = {}
    for model in models:
        if key := slug(model.name):
            by_key[key] = model
    if not by_key:
        return 0

    # One read for the whole batch. ``sources`` is a set, so merging it needs to know what is
    # already there, and doing that here keeps the statement legible. Two refreshes racing
    # could drop a name; the refresh is a single daily task, so that is not worth a lock.
    stored: dict[str, str] = {
        model_id: sources
        for model_id, sources in session.execute(
            select(AiModelRow.id, AiModelRow.sources).where(AiModelRow.id.in_(list(by_key)))
        ).all()
    }
    rows = [
        {
            "id": key,
            "name": model.name,
            "sources": _merged_sources(stored.get(key), model.source),
            "refreshed_at": now,
            **{field: getattr(model, field) for field in _MODEL_FIELDS},
        }
        for key, model in by_key.items()
    ]

    stmt = insert(AiModelRow)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "name": stmt.excluded.name,
            "sources": stmt.excluded.sources,
            "refreshed_at": stmt.excluded.refreshed_at,
            **{
                field: (
                    func.coalesce(stmt.excluded[field], getattr(AiModelRow, field))
                    if field in authoritative
                    else func.coalesce(getattr(AiModelRow, field), stmt.excluded[field])
                )
                for field in _MODEL_FIELDS
            },
        },
    )
    session.execute(stmt, rows)
    return len(rows)


def score_scale(scores: Sequence[float]) -> str:
    """Whether a benchmark's scores read as percentages or as bare numbers.

    Decided over the whole set at write time rather than per page: a leaderboard capped at
    fifty rows and a provider comparison showing five would otherwise reach different answers
    about the same benchmark and format it two ways on two pages.

    A benchmark with no scores at all is called a fraction, which is the common case and what
    the column defaults to.
    """
    if not scores:
        return "fraction"
    return "fraction" if all(0.0 <= score <= 1.0 for score in scores) else "raw"


def _merged_sources(stored: str | None, source: str) -> str:
    """Add ``source`` to the comma-joined set already recorded, sorted and without duplicates.

    The column was being replaced rather than added to, so a model both upstreams describe
    reported only whichever one wrote last -- which is exactly the case the field exists to
    record.
    """
    names = {part for part in (stored or "").split(",") if part}
    if source:
        names.add(source)
    return ",".join(sorted(names))


def link_models_to_papers(session: Session, pairs: dict[str, str]) -> int:
    """Point model slugs at canonical paper ids; unknown papers are skipped.

    This is the join the whole feature turns on, so it runs as its own step after both sources
    have been written: a model that arrives from Hugging Face with an arXiv tag and from Epoch
    with a link resolves the same way either way round.
    """
    if not pairs:
        return 0
    known = set(
        session.execute(select(PaperRow.id).where(PaperRow.id.in_(list(pairs.values())))).scalars()
    )
    linked = 0
    for model_id, paper_id in pairs.items():
        if paper_id not in known:
            continue
        result = session.execute(
            update(AiModelRow).where(AiModelRow.id == model_id).values(paper_id=paper_id)
        )
        linked += _rows(result)
    return linked


def known_model_ids(session: Session) -> set[str]:
    """Every model slug in the catalogue, so a score can link only where a model exists."""
    return set(session.execute(select(AiModelRow.id)).scalars())


def replace_benchmark_results(
    session: Session,
    benchmark: str,
    released_on: date_type | None,
    results: Sequence[BenchmarkResult],
    known_models: set[str],
) -> int:
    """Upsert one benchmark and its scores; returns how many scores were written.

    ``results`` is ``(model_name, score, measured_on, origin)``. Scores are upserted rather than
    replaced wholesale so a partial upstream response never empties a leaderboard.

    ``model_id`` is set only for a slug already in ``ai_models``. Roughly half the benchmarked
    models are not in the catalogue at all -- a leaderboard covers what has been measured, the
    catalogue covers what somebody judged notable, and they are not the same list. The score is
    kept either way and simply does not link, which is why ``model_name`` is part of the key.
    """
    now = datetime.now(UTC)
    key = slug(benchmark)
    if not key:
        return 0
    scale = score_scale([score for _, score, _, _ in results])
    # Deduplicated on the primary key before anything is written: an upstream listing the same
    # model twice on one benchmark would otherwise make a single statement touch one row twice,
    # which Postgres refuses outright.
    rows: dict[str, dict[str, object]] = {}
    for model_name, score, measured_on, origin in results:
        model_key = slug(model_name)
        rows[model_name] = {
            "benchmark_id": key,
            "model_name": model_name,
            "model_id": model_key if model_key in known_models else None,
            "score": score,
            "measured_on": measured_on,
            "origin": origin,
        }

    stmt = insert(BenchmarkRow).values(
        id=key,
        name=benchmark,
        released_on=released_on,
        result_count=len(rows),
        score_scale=scale,
        refreshed_at=now,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name": stmt.excluded.name,
                "released_on": func.coalesce(stmt.excluded.released_on, BenchmarkRow.released_on),
                "result_count": stmt.excluded.result_count,
                "score_scale": stmt.excluded.score_scale,
                "refreshed_at": stmt.excluded.refreshed_at,
            },
        )
    )
    if not rows:
        return 0
    row_stmt = insert(BenchmarkResultRow)
    row_stmt = row_stmt.on_conflict_do_update(
        index_elements=["benchmark_id", "model_name"],
        set_={
            "model_id": row_stmt.excluded.model_id,
            "score": row_stmt.excluded.score,
            "measured_on": row_stmt.excluded.measured_on,
            "origin": row_stmt.excluded.origin,
        },
    )
    # One statement, every score: a refresh carries a few thousand of these.
    session.execute(row_stmt, list(rows.values()))
    return len(rows)


@dataclass(frozen=True)
class ModelFilters:
    """What narrows the model list. One object so the list and the count cannot disagree."""

    organization: str | None = None
    domain: str | None = None
    open_weights: bool | None = None
    with_paper: bool = False
    #: Free-text match on the model name, for the search box above the table.
    query: str | None = None


def _model_where(filters: ModelFilters) -> list[ColumnElement[bool]]:
    """The filter chain both reads share.

    They had a copy each, identical line for line, which is two places for the next filter to
    land in only one of - and a count that disagrees with its own list is a pager that sends
    people to empty pages.
    """
    clauses: list[ColumnElement[bool]] = []
    if filters.organization:
        clauses.append(AiModelRow.organization.ilike(f"%{filters.organization}%"))
    if filters.domain:
        clauses.append(AiModelRow.domains.ilike(f"%{filters.domain}%"))
    if filters.open_weights is not None:
        clauses.append(AiModelRow.open_weights.is_(filters.open_weights))
    if filters.with_paper:
        clauses.append(AiModelRow.paper_id.is_not(None))
    if filters.query:
        clauses.append(AiModelRow.name.ilike(f"%{filters.query}%"))
    return clauses


#: What each sort key orders by.
_MODEL_COLUMNS: dict[str, Any] = {
    "released": AiModelRow.publication_date,
    "parameters": AiModelRow.parameters,
    "compute": AiModelRow.training_compute_flop,
    "downloads": AiModelRow.hf_downloads,
    "organization": AiModelRow.organization,
    "name": AiModelRow.name,
}

#: Which keys read high-to-low when you first click them. A date and a size are interesting
#: from the top; a name is interesting from A.
_DESCENDING_BY_DEFAULT = frozenset({"released", "parameters", "compute", "downloads"})

ModelSort = Literal["released", "parameters", "compute", "downloads", "organization", "name"]
MODEL_SORTS: tuple[str, ...] = tuple(_MODEL_COLUMNS)


def _model_order(sort: str, descending: bool | None) -> tuple[Any, ...]:
    """The ORDER BY for one sort key and direction.

    Nulls last in both directions, which is not what SQL does by default and is what a reader
    means: a missing parameter count is not a small one, and a column that opens with a screen
    of blanks is useless whichever way it is pointing.

    Everything falls back to the name, because without a tiebreak two models with no date can
    swap places between page one and page two and one of them is never seen at all.
    """
    column = _MODEL_COLUMNS.get(sort) or _MODEL_COLUMNS["released"]
    if descending is None:
        descending = sort in _DESCENDING_BY_DEFAULT
    ordered = column.desc().nullslast() if descending else column.asc().nullslast()
    return (ordered,) if column is AiModelRow.name else (ordered, AiModelRow.name)


def list_models(
    session: Session,
    *,
    filters: ModelFilters | None = None,
    sort: ModelSort = "released",
    descending: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AiModelRow]:
    """Models in ``sort`` order, filtered the way the page filters them.

    ``descending`` of None takes the direction the column is usually wanted in, so a caller
    that only cares which column need not say. Passing it is what lets a table heading toggle:
    without a direction a second click on a sorted column produces the URL it is already on,
    which reads as the sort being broken rather than as already sorted.
    """
    stmt = select(AiModelRow).where(*_model_where(filters or ModelFilters()))
    order = _model_order(sort, descending)
    return list(session.execute(stmt.order_by(*order).limit(limit).offset(offset)).scalars())


def count_models(session: Session, *, filters: ModelFilters | None = None) -> int:
    """How many models match, ignoring pagination."""
    stmt = (
        select(func.count()).select_from(AiModelRow).where(*_model_where(filters or ModelFilters()))
    )
    return session.execute(stmt).scalar_one()


def get_model(session: Session, model_id: str) -> AiModelRow | None:
    """One model by slug, or None."""
    return session.get(AiModelRow, model_id)


def get_benchmark(session: Session, benchmark_id: str) -> BenchmarkRow | None:
    """One benchmark by slug, or None."""
    return session.get(BenchmarkRow, benchmark_id)


def models_for_paper(session: Session, paper_id: str) -> list[AiModelRow]:
    """Models this corpus knows came out of one paper."""
    return list(
        session.execute(
            select(AiModelRow)
            .where(AiModelRow.paper_id == paper_id)
            .order_by(AiModelRow.publication_date.desc().nullslast(), AiModelRow.name)
        ).scalars()
    )


def list_benchmarks(session: Session, *, limit: int = 100) -> list[BenchmarkRow]:
    """Benchmarks with the most scores first, which is roughly most-used first."""
    return list(
        session.execute(
            select(BenchmarkRow)
            .order_by(BenchmarkRow.result_count.desc(), BenchmarkRow.name)
            .limit(limit)
        ).scalars()
    )


def leaderboard(
    session: Session, benchmark_id: str, *, limit: int = 25
) -> list[BenchmarkResultRow]:
    """The best scores on one benchmark, best first."""
    return list(
        session.execute(
            select(BenchmarkResultRow)
            .where(BenchmarkResultRow.benchmark_id == benchmark_id)
            .order_by(BenchmarkResultRow.score.desc(), BenchmarkResultRow.model_name)
            .limit(limit)
        ).scalars()
    )


def results_for_model(
    session: Session, model_id: str, *, limit: int = 50
) -> list[tuple[str, float, str]]:
    """(benchmark name, score, scale) for one model, so its page can show what it scores.

    The scale comes along because it belongs to the benchmark, not the score: without it the
    page has one number and no way to know whether it is a percentage.
    """
    rows = session.execute(
        select(BenchmarkRow.name, BenchmarkResultRow.score, BenchmarkRow.score_scale)
        .join(BenchmarkResultRow, BenchmarkResultRow.benchmark_id == BenchmarkRow.id)
        .where(BenchmarkResultRow.model_id == model_id)
        .order_by(BenchmarkRow.name)
        .limit(limit)
    ).all()
    return [(name, score, scale) for name, score, scale in rows]


@dataclass(frozen=True)
class ProviderEntry:
    """One provider's current flagship, and what it scores on the headline benchmarks."""

    provider: str
    country: str | None
    model_id: str
    model_name: str
    published_on: date_type | None
    paper_id: str | None
    open_weights: bool | None
    #: benchmark id -> score, holding only the benchmarks this model has been measured on.
    scores: dict[str, float]


@dataclass(frozen=True)
class ScoreColumn:
    """One column of the provider comparison: a benchmark, and how to read its numbers."""

    id: str
    name: str
    scale: str


def provider_leaders(
    session: Session, config: ProviderConfig
) -> tuple[list[ProviderEntry], list[ScoreColumn]]:
    """Each listed provider's best-covered model, plus the benchmark columns worth drawing.

    Best-covered rather than newest: a lab's most recent release has usually been run against
    one or two of these so far, and a row with a single figure in eight columns cannot be
    compared against anything, which is the entire point of the table. Ties go to the newer
    model.

    The second return value is the (id, display name) of the configured benchmarks that at
    least one of these models has a score on, in configured order. Columns nothing scored are
    dropped rather than drawn empty - which benchmarks the upstream publishes is not ours to
    decide, and a table should show what exists.
    """
    aliases = sorted({alias for provider in config.providers for alias in provider.aliases})
    wanted = list(config.benchmarks)
    if not aliases or not wanted:
        return [], []

    # Exact match on the whole trimmed field, never a substring: an organisation column holds
    # "Mistral AI" and "Mistral community", and only one of those is the lab.
    candidates = session.execute(
        select(
            AiModelRow.id,
            AiModelRow.name,
            AiModelRow.organization,
            AiModelRow.publication_date,
            AiModelRow.paper_id,
            AiModelRow.open_weights,
        )
        .where(func.lower(func.btrim(AiModelRow.organization)).in_(aliases))
        .order_by(AiModelRow.publication_date.desc().nullslast(), AiModelRow.name)
    ).all()
    if not candidates:
        return [], []

    scores: dict[str, dict[str, float]] = defaultdict(dict)
    for model_id, benchmark_id, score in session.execute(
        select(
            BenchmarkResultRow.model_id,
            BenchmarkResultRow.benchmark_id,
            BenchmarkResultRow.score,
        ).where(
            BenchmarkResultRow.model_id.in_([row.id for row in candidates]),
            BenchmarkResultRow.benchmark_id.in_(wanted),
        )
    ).all():
        scores[model_id][benchmark_id] = score

    # Group first, then choose: the model with the most of these benchmarks measured, and among
    # equals the newest. Taking simply the newest scored model gave rows with one figure in
    # eight columns, because a brand-new release has usually been run against one thing so far -
    # and a row that cannot be compared is not worth a row. Candidates arrive newest first and
    # max() keeps the first of equals, so recency remains the tiebreak for free.
    by_provider: dict[str, list[Any]] = defaultdict(list)
    for row in candidates:
        provider = config.for_organization(row.organization)
        if provider is not None and row.id in scores:
            by_provider[provider.name].append(row)

    best: dict[str, ProviderEntry] = {}
    for provider in config.providers:
        rows = by_provider.get(provider.name)
        if not rows:
            continue
        row = max(rows, key=lambda candidate: len(scores[candidate.id]))
        best[provider.name] = ProviderEntry(
            provider=provider.name,
            country=provider.country,
            model_id=row.id,
            model_name=row.name,
            published_on=row.publication_date,
            paper_id=row.paper_id,
            open_weights=row.open_weights,
            scores=dict(scores[row.id]),
        )

    entries = [best[provider.name] for provider in config.providers if provider.name in best]
    described: dict[str, tuple[str, str]] = {
        benchmark_id: (name, scale)
        for benchmark_id, name, scale in session.execute(
            select(BenchmarkRow.id, BenchmarkRow.name, BenchmarkRow.score_scale).where(
                BenchmarkRow.id.in_(wanted)
            )
        ).all()
    }
    columns = [
        ScoreColumn(
            id=benchmark_id,
            name=described.get(benchmark_id, (benchmark_id, "fraction"))[0],
            scale=described.get(benchmark_id, (benchmark_id, "fraction"))[1],
        )
        for benchmark_id in wanted
        if any(benchmark_id in entry.scores for entry in entries)
    ]
    return entries, columns


def catalog_counts(session: Session) -> dict[str, int]:
    """Row counts, for the page footers and the refresh log."""
    return {
        "models": session.execute(select(func.count()).select_from(AiModelRow)).scalar_one(),
        "benchmarks": session.execute(select(func.count()).select_from(BenchmarkRow)).scalar_one(),
        "results": session.execute(
            select(func.count()).select_from(BenchmarkResultRow)
        ).scalar_one(),
        "linked": session.execute(
            select(func.count()).select_from(AiModelRow).where(AiModelRow.paper_id.is_not(None))
        ).scalar_one(),
    }
