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

#: The fields each upstream is the authority for: what it says replaces what is stored, and
#: everything else it supplies only fills a gap. Epoch AI describes a model - who built it, how
#: large, on how much compute, under what licence, and the work it came from. The Hub knows how
#: much it is downloaded and nothing else that is not a guess: its "organisation" is a
#: repository owner and its link is that repository. Splitting them this way is what stops the
#: refresh order from deciding what a model both describe looks like.
EPOCH_FIELDS = frozenset(
    {
        "organization",
        "publication_date",
        "domains",
        "task",
        "parameters",
        "training_compute_flop",
        "accessibility",
        "open_weights",
        "link",
    }
)
HF_FIELDS = frozenset({"hf_repo", "hf_downloads", "hf_likes"})
# paper_id is in neither: link_models_to_papers sets it once both have been written, so an
# upsert must never do more than fill it in when it happens to be empty.


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

    Order matters once, and used to matter twice. Every model source is written before any
    benchmark source, because a score can only link to a model that is already a row.

    What no longer matters is which of the two model sources runs first: each declares the
    fields it is the authority for (``EPOCH_FIELDS``, ``HF_FIELDS``) and can only fill gaps in
    the rest. Before that, running second was the same as being right, and the Hub ran second.
    """
    summary = CatalogSummary()
    failed: list[str] = []
    claims: dict[str, str] = {}
    # Slugs rather than a running total: a model both upstreams describe is one row, and
    # counting it twice made the log overstate the catalogue by however much they overlap.
    written: set[str] = set()

    # Every model source before any benchmark source: a score links to a model only when that
    # model is already a row, so writing the catalogue first is what gives the leaderboard the
    # most links it can have in one pass.
    if _enabled(EPOCH_SOURCE):
        try:
            upserts, model_claims = _epoch_models(session)
            upsert_models(session, upserts, authoritative=EPOCH_FIELDS)
            written |= {key for model in upserts if (key := slug(model.name))}
            claims.update(model_claims)
        except Exception as exc:  # noqa: BLE001 - keep yesterday's rows, name the upstream
            logger.warning("epoch models refresh failed: %s", exc)
            failed.append(EPOCH_SOURCE)

    if _enabled(HF_SOURCE):
        try:
            upserts, model_claims = _hub_models(session)
            upsert_models(session, upserts, authoritative=HF_FIELDS)
            written |= {key for model in upserts if (key := slug(model.name))}
            # A model in both keeps the Epoch link, which points at the paper of record rather
            # than at whichever paper the model card happened to cite.
            for key, paper_id in model_claims.items():
                claims.setdefault(key, paper_id)
        except Exception as exc:  # noqa: BLE001 - keep yesterday's rows, name the upstream
            logger.warning("hugging face models refresh failed: %s", exc)
            failed.append(HF_SOURCE)

    summary.models = len(written)

    summary.linked = link_models_to_papers(session, claims)

    if _enabled(EPOCH_SOURCE):
        try:
            summary.benchmarks, summary.results = _refresh_benchmarks(session)
        except Exception as exc:  # noqa: BLE001 - keep yesterday's rows, name the upstream
            logger.warning("epoch benchmarks refresh failed: %s", exc)
            failed.append(f"{EPOCH_SOURCE}:benchmarks")

    summary.failed = tuple(failed)
    return summary


def _refresh_benchmarks(session: Session) -> tuple[int, int]:
    """Group the flat score list by benchmark and write each one; returns (benchmarks, scores).

    Grouped by slug rather than by the printed name, because the slug is the key the rows are
    written under. Two spellings of one benchmark grouped separately and then collided on
    write, and the second group's ``result_count`` replaced the first's rather than joining it.
    """
    grouped: dict[str, list[epoch.BenchmarkScore]] = defaultdict(list)
    display: dict[str, str] = {}
    for score in epoch.fetch_benchmarks():
        key = slug(score.benchmark)
        if not key:
            continue
        grouped[key].append(score)
        display.setdefault(key, score.benchmark)

    # The benchmark files carry the organisation and the training compute of every model they
    # measure, and most of those models are not in the notable-models catalogue - a leaderboard
    # covers what has been evaluated, the catalogue what somebody judged notable. Writing them
    # as rows first is what gives each one a page to link to instead of an "unlisted" marker,
    # and what lets the provider comparison find a lab's newest measured model at all. Fill-only
    # by construction: this pass declares authority over nothing, so anything Epoch's own
    # catalogue says about the same model still wins.
    measured = {
        key: ModelUpsert(
            name=score.model,
            organization=score.organization,
            training_compute_flop=score.training_compute_flop,
            publication_date=score.measured_on,
            source=EPOCH_SOURCE,
        )
        for scores in grouped.values()
        for score in scores
        if (key := slug(score.model))
    }
    if measured:
        upsert_models(session, list(measured.values()))

    # Read once, after that: the models are all written by now, and asking per score would be a
    # query per row for a few thousand rows.
    known = known_model_ids(session)
    benchmarks = 0
    results = 0
    for key, scores in grouped.items():
        released = next(
            (s.benchmark_release_date for s in scores if s.benchmark_release_date), None
        )
        written = replace_benchmark_results(
            session,
            display[key],
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
