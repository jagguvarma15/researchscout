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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


def upsert_models(session: Session, models: Sequence[ModelUpsert]) -> int:
    """Insert or merge models by slug; returns how many were written.

    A None field never overwrites a stored value. That is what lets a model keep the facts one
    upstream knows when the other refreshes without them -- Hugging Face has download counts and
    no training compute, Epoch AI the reverse, and the row ends up with both whichever order
    they arrive in.
    """
    now = datetime.now(UTC)
    written = 0
    for model in models:
        key = slug(model.name)
        if not key:
            continue
        values = {
            "id": key,
            "name": model.name,
            "sources": model.source,
            "refreshed_at": now,
            **{field: getattr(model, field) for field in _MODEL_FIELDS},
        }
        stmt = insert(AiModelRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name": stmt.excluded.name,
                "sources": stmt.excluded.sources,
                "refreshed_at": stmt.excluded.refreshed_at,
                **{
                    field: func.coalesce(stmt.excluded[field], getattr(AiModelRow, field))
                    for field in _MODEL_FIELDS
                },
            },
        )
        session.execute(stmt)
        written += 1
    return written


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
    stmt = insert(BenchmarkRow).values(
        id=key,
        name=benchmark,
        released_on=released_on,
        result_count=len(results),
        refreshed_at=now,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name": stmt.excluded.name,
                "released_on": func.coalesce(stmt.excluded.released_on, BenchmarkRow.released_on),
                "result_count": stmt.excluded.result_count,
                "refreshed_at": stmt.excluded.refreshed_at,
            },
        )
    )
    written = 0
    for model_name, score, measured_on, origin in results:
        model_key = slug(model_name)
        row = insert(BenchmarkResultRow).values(
            benchmark_id=key,
            model_name=model_name,
            model_id=model_key if model_key in known_models else None,
            score=score,
            measured_on=measured_on,
            origin=origin,
        )
        session.execute(
            row.on_conflict_do_update(
                index_elements=["benchmark_id", "model_name"],
                set_={
                    "model_id": row.excluded.model_id,
                    "score": row.excluded.score,
                    "measured_on": row.excluded.measured_on,
                    "origin": row.excluded.origin,
                },
            )
        )
        written += 1
    return written


def list_models(
    session: Session,
    *,
    organization: str | None = None,
    domain: str | None = None,
    open_weights: bool | None = None,
    with_paper: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[AiModelRow]:
    """Models newest first, filtered the way the page filters them."""
    stmt = select(AiModelRow)
    if organization:
        stmt = stmt.where(AiModelRow.organization.ilike(f"%{organization}%"))
    if domain:
        stmt = stmt.where(AiModelRow.domains.ilike(f"%{domain}%"))
    if open_weights is not None:
        stmt = stmt.where(AiModelRow.open_weights.is_(open_weights))
    if with_paper:
        stmt = stmt.where(AiModelRow.paper_id.is_not(None))
    stmt = (
        stmt.order_by(AiModelRow.publication_date.desc().nullslast(), AiModelRow.name)
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars())


def count_models(
    session: Session,
    *,
    organization: str | None = None,
    domain: str | None = None,
    open_weights: bool | None = None,
    with_paper: bool = False,
) -> int:
    """How many models match, ignoring pagination."""
    stmt = select(func.count()).select_from(AiModelRow)
    if organization:
        stmt = stmt.where(AiModelRow.organization.ilike(f"%{organization}%"))
    if domain:
        stmt = stmt.where(AiModelRow.domains.ilike(f"%{domain}%"))
    if open_weights is not None:
        stmt = stmt.where(AiModelRow.open_weights.is_(open_weights))
    if with_paper:
        stmt = stmt.where(AiModelRow.paper_id.is_not(None))
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
) -> list[tuple[str, float]]:
    """(benchmark name, score) for one model, so a model row can show what it scores."""
    rows = session.execute(
        select(BenchmarkRow.name, BenchmarkResultRow.score)
        .join(BenchmarkResultRow, BenchmarkResultRow.benchmark_id == BenchmarkRow.id)
        .where(BenchmarkResultRow.model_id == model_id)
        .order_by(BenchmarkRow.name)
        .limit(limit)
    ).all()
    return [(name, score) for name, score in rows]


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
