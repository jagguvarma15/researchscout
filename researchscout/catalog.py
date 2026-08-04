"""Refreshing the model and benchmark catalogue, and joining it to the corpus.

Two upstreams describe different halves of the same landscape. Epoch AI knows what a model is --
who built it, when, how large, on how much compute, and under what licence. Hugging Face knows
what people actually run, and, through the ``arxiv:`` tags on a model card, which paper it came
out of. Merged on a slug of the model's name, one row carries both.

The join to ``papers`` is the point of doing this here rather than linking out to a leaderboard.
A model reached through an arXiv link or an arXiv tag becomes a model attached to a paper this
corpus already holds, so the Models page can lead into the reader and a paper page can say what
came out of it.

Nothing here fails loudly. An upstream that is down, rate limited or has changed shape costs the
catalogue its refresh for that cycle, logged, with yesterday's rows still standing -- which for a
page of facts about the world is much better than an empty one.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from researchscout.schema import normalize_arxiv_id
from researchscout.sources import epoch, hf_models
from researchscout.sources.base import source_config
from researchscout.store.catalog import (
    ModelUpsert,
    catalog_counts,
    known_model_ids,
    link_models_to_papers,
    replace_benchmark_results,
    slug,
    upsert_models,
)
from researchscout.store.models import ExternalIdRow

logger = logging.getLogger(__name__)

EPOCH_SOURCE = "epoch_ai"
HF_SOURCE = "huggingface_models"


@dataclass
class CatalogSummary:
    models: int = 0
    benchmarks: int = 0
    results: int = 0
    linked: int = 0
    failed: tuple[str, ...] = ()


def _enabled(name: str) -> bool:
    return bool(source_config(name).get("enabled", False))


def _resolve_papers(session: Session, arxiv_ids: set[str]) -> dict[str, str]:
    """arXiv id -> canonical paper id, for the ids this corpus actually holds."""
    if not arxiv_ids:
        return {}
    rows = session.execute(
        select(ExternalIdRow.value, ExternalIdRow.paper_id).where(
            ExternalIdRow.scheme == "arxiv",
            ExternalIdRow.value.in_(sorted(arxiv_ids)),
        )
    ).all()
    return {value: paper_id for value, paper_id in rows}


def _epoch_models(session: Session) -> tuple[list[ModelUpsert], dict[str, str]]:
    """Epoch's catalogue as upserts, plus the arXiv id each model claims."""
    records = epoch.fetch_models()
    wanted = {record.arxiv_id for record in records if record.arxiv_id}
    resolved = _resolve_papers(session, wanted)
    upserts: list[ModelUpsert] = []
    claims: dict[str, str] = {}
    for record in records:
        key = slug(record.name)
        if not key:
            continue
        paper_id = resolved.get(record.arxiv_id or "")
        if paper_id:
            claims[key] = paper_id
        upserts.append(
            ModelUpsert(
                name=record.name,
                organization=record.organization,
                publication_date=record.publication_date,
                domains=",".join(record.domains) or None,
                task=record.task,
                parameters=record.parameters,
                training_compute_flop=record.training_compute_flop,
                accessibility=record.accessibility,
                open_weights=record.open_weights,
                link=record.link,
                paper_id=paper_id,
                source=EPOCH_SOURCE,
            )
        )
    return upserts, claims


def _hub_models(session: Session) -> tuple[list[ModelUpsert], dict[str, str]]:
    """The Hub's most-downloaded models as upserts, plus the papers their cards cite."""
    records = hf_models.fetch_models()
    # Only the newest tag on each card: see HubModel.primary_arxiv_id for why an older one is a
    # citation rather than the paper this model came from.
    wanted = {
        normalize_arxiv_id(record.primary_arxiv_id) for record in records if record.primary_arxiv_id
    }
    resolved = _resolve_papers(session, wanted)
    upserts: list[ModelUpsert] = []
    claims: dict[str, str] = {}
    for record in records:
        key = slug(record.name)
        if not key:
            continue
        primary = record.primary_arxiv_id
        paper_id = resolved.get(normalize_arxiv_id(primary)) if primary else None
        if paper_id:
            claims[key] = paper_id
        upserts.append(
            ModelUpsert(
                name=record.name,
                organization=record.owner,
                task=record.pipeline,
                link=f"https://huggingface.co/{record.repo}",
                paper_id=paper_id,
                hf_repo=record.repo,
                hf_downloads=record.downloads,
                hf_likes=record.likes,
                # Being on the Hub at all is what open weights means here.
                open_weights=True,
                source=HF_SOURCE,
            )
        )
    return upserts, claims


def refresh_catalog(session: Session) -> CatalogSummary:
    """Refresh both upstreams, merge them, and join what can be joined to the corpus.

    Order matters twice. Epoch AI is written before the Hub, so its richer description wins the
    fields both know and the Hub fills the gaps it leaves -- though only weakly, since a None
    never overwrites. And every model source is written before any benchmark source, because a
    score can only link to a model that is already a row.
    """
    summary = CatalogSummary()
    failed: list[str] = []
    claims: dict[str, str] = {}

    # Every model source before any benchmark source: a score links to a model only when that
    # model is already a row, so writing the catalogue first is what gives the leaderboard the
    # most links it can have in one pass.
    if _enabled(EPOCH_SOURCE):
        try:
            upserts, model_claims = _epoch_models(session)
            summary.models += upsert_models(session, upserts)
            claims.update(model_claims)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("epoch models refresh failed: %s", exc)
            failed.append(EPOCH_SOURCE)

    if _enabled(HF_SOURCE):
        try:
            upserts, model_claims = _hub_models(session)
            summary.models += upsert_models(session, upserts)
            # A model in both keeps the Epoch link, which points at the paper of record rather
            # than at whichever paper the model card happened to cite.
            for key, paper_id in model_claims.items():
                claims.setdefault(key, paper_id)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("hugging face models refresh failed: %s", exc)
            failed.append(HF_SOURCE)

    summary.linked = link_models_to_papers(session, claims)

    if _enabled(EPOCH_SOURCE):
        try:
            summary.benchmarks, summary.results = _refresh_benchmarks(session)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("epoch benchmarks refresh failed: %s", exc)
            failed.append(f"{EPOCH_SOURCE}:benchmarks")

    summary.failed = tuple(failed)
    return summary


def _refresh_benchmarks(session: Session) -> tuple[int, int]:
    """Group the flat score list by benchmark and write each one; returns (benchmarks, scores)."""
    grouped: dict[str, list[epoch.BenchmarkScore]] = defaultdict(list)
    for score in epoch.fetch_benchmarks():
        grouped[score.benchmark].append(score)

    # Read once: the models are all written by now, and asking per score would be a query per
    # row for a few thousand rows.
    known = known_model_ids(session)
    benchmarks = 0
    results = 0
    for name, scores in grouped.items():
        released = next(
            (s.benchmark_release_date for s in scores if s.benchmark_release_date), None
        )
        written = replace_benchmark_results(
            session,
            name,
            released,
            [(s.model, s.score, s.measured_on, s.origin) for s in scores],
            known,
        )
        benchmarks += 1
        results += written
    return benchmarks, results


def counts(session: Session) -> dict[str, int]:
    """Row counts for the catalogue, as the pages and the CLI report them."""
    return catalog_counts(session)
