"""The AI landscape: which models exist, and how they score.

Public, like the papers endpoints: this is published data about the world, and none of it is
about the caller. The one thing here a general model list cannot do is ``paper_id`` -- a model
reached through its arXiv link, resolved against papers this corpus already holds.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import (
    BenchmarkColumn,
    BenchmarkDetail,
    BenchmarkList,
    BenchmarkResultSummary,
    BenchmarkSummary,
    ModelList,
    ModelSummary,
    ProviderList,
    ProviderSummary,
)
from researchscout.providers import load_providers
from researchscout.store import catalog

router = APIRouter(tags=["catalog"])


@router.get("/models")
def models_index(
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    organization: Annotated[str | None, Query(max_length=100)] = None,
    domain: Annotated[str | None, Query(max_length=50)] = None,
    open_weights: Annotated[bool | None, Query()] = None,
    with_paper: Annotated[bool, Query()] = False,
    paper_id: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[catalog.ModelSort, Query()] = "released",
    direction: Annotated[Literal["asc", "desc"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ModelList:
    """Models in ``sort`` order, newest released first by default.

    ``q`` matches the model name; ``with_paper`` keeps only those that reach a paper in this
    corpus; ``paper_id`` narrows to one paper, which is what the paper page asks for when it
    lists what came out of the work.

    ``direction`` is optional and defaults to whichever way the column is usually wanted: dates
    and sizes from the top, names from A. It exists so a table heading can toggle rather than
    regenerating the URL it is already on.
    """
    if paper_id:
        rows = catalog.models_for_paper(session, paper_id)
        items = [ModelSummary.from_row(row) for row in rows]
        return ModelList(items=items, total=len(items), limit=limit, offset=0)
    filters = catalog.ModelFilters(
        organization=organization,
        domain=domain,
        open_weights=open_weights,
        with_paper=with_paper,
        query=q,
    )
    rows = catalog.list_models(
        session,
        filters=filters,
        sort=sort,
        descending=None if direction is None else direction == "desc",
        limit=limit,
        offset=offset,
    )
    return ModelList(
        items=[ModelSummary.from_row(row) for row in rows],
        total=catalog.count_models(session, filters=filters),
        limit=limit,
        offset=offset,
    )


@router.get("/models/{model_id}")
def model_detail(
    model_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> ModelSummary:
    """One model, with whatever benchmark scores are recorded against it."""
    match = catalog.get_model(session, model_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"unknown model id: {model_id}")
    summary = ModelSummary.from_row(match)
    summary.scores = [
        BenchmarkResultSummary(
            benchmark=name, model=match.name, score=score, model_id=match.id, scale=scale
        )
        for name, score, scale in catalog.results_for_model(session, model_id)
    ]
    return summary


@router.get("/providers")
def providers_index(
    session: Annotated[Session, Depends(get_session)],
) -> ProviderList:
    """Each listed provider's current flagship, and how it scores on the headline benchmarks.

    A leaderboard answers "what is the best score on this benchmark"; this answers the question
    people actually arrive with, which is which lab is ahead. Who is listed and which
    benchmarks are compared come from ``config/providers.yaml``.
    """
    entries, columns = catalog.provider_leaders(session, load_providers())
    return ProviderList(
        columns=[
            BenchmarkColumn(id=column.id, name=column.name, scale=column.scale)
            for column in columns
        ],
        items=[
            ProviderSummary(
                provider=entry.provider,
                country=entry.country,
                model_id=entry.model_id,
                model_name=entry.model_name,
                published_on=entry.published_on,
                paper_id=entry.paper_id,
                open_weights=entry.open_weights,
                scores=entry.scores,
            )
            for entry in entries
        ],
    )


@router.get("/benchmarks")
def benchmarks_index(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> BenchmarkList:
    """Benchmarks with the most recorded scores first."""
    return BenchmarkList(
        items=[
            BenchmarkSummary.from_row(row) for row in catalog.list_benchmarks(session, limit=limit)
        ]
    )


@router.get("/benchmarks/{benchmark_id}")
def benchmark_detail(
    benchmark_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> BenchmarkDetail:
    """One benchmark and its leaderboard, best score first."""
    match = catalog.get_benchmark(session, benchmark_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"unknown benchmark id: {benchmark_id}")
    results = catalog.leaderboard(session, benchmark_id, limit=limit)
    return BenchmarkDetail(
        **BenchmarkSummary.from_row(match).model_dump(),
        results=[
            BenchmarkResultSummary(
                benchmark=match.name,
                model=row.model_name,
                model_id=row.model_id,
                score=row.score,
                measured_on=row.measured_on,
                origin=row.origin,
            )
            for row in results
        ],
    )
