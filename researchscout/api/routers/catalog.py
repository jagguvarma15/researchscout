"""The AI landscape: which models exist, and how they score.

Public, like the papers endpoints: this is published data about the world, and none of it is
about the caller. The one thing here a general model list cannot do is ``paper_id`` -- a model
reached through its arXiv link, resolved against papers this corpus already holds.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.schemas import (
    BenchmarkDetail,
    BenchmarkList,
    BenchmarkResultSummary,
    BenchmarkSummary,
    ModelList,
    ModelSummary,
)
from researchscout.store import catalog

router = APIRouter(tags=["catalog"])


@router.get("/models")
def models_index(
    session: Annotated[Session, Depends(get_session)],
    organization: Annotated[str | None, Query(max_length=100)] = None,
    domain: Annotated[str | None, Query(max_length=50)] = None,
    open_weights: Annotated[bool | None, Query()] = None,
    with_paper: Annotated[bool, Query()] = False,
    paper_id: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ModelList:
    """Models newest first.

    ``with_paper`` keeps only those that reach a paper in this corpus; ``paper_id`` narrows to
    one paper, which is what the paper page asks for when it lists what came out of the work.
    """
    if paper_id:
        rows = catalog.models_for_paper(session, paper_id)
        items = [ModelSummary.from_row(row) for row in rows]
        return ModelList(items=items, total=len(items), limit=limit, offset=0)
    rows = catalog.list_models(
        session,
        organization=organization,
        domain=domain,
        open_weights=open_weights,
        with_paper=with_paper,
        limit=limit,
        offset=offset,
    )
    total = catalog.count_models(
        session,
        organization=organization,
        domain=domain,
        open_weights=open_weights,
        with_paper=with_paper,
    )
    return ModelList(
        items=[ModelSummary.from_row(row) for row in rows],
        total=total,
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
        BenchmarkResultSummary(benchmark=name, model=match.name, score=score, model_id=match.id)
        for name, score in catalog.results_for_model(session, model_id)
    ]
    return summary


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
