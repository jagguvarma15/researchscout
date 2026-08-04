"""Epoch AI: the catalogue of notable models, and the benchmark scores they reach.

Two CSVs, both keyless and both CC-BY, which is why they are here rather than one of the richer
commercial catalogues -- OpenRouter's model list is the obvious alternative and its terms reserve
all rights to it, so republishing it is not available to us at any price.

* ``notable_ai_models.csv`` -- about a thousand models with organisation, publication date,
  domain, parameters, training compute, weight availability and a link to the work they came
  from. Half of those links are arXiv, which is what lets a model reach the paper in this corpus.
* ``eci_benchmarks.csv`` -- benchmark scores, a few thousand rows over several dozen benchmarks.

Fetching and parsing are separate on purpose. The parsers take text, so the tests read fixtures
rather than the network, and a change of shape upstream shows up as a parse test rather than as
an empty page.

Attribution is required by the licence and declared in ``config/sources.yaml``, which is what
puts it on the /about page: "Epoch AI, 'Data on AI Models', published online at epoch.ai".
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx

from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

MODELS_URL = "https://epoch.ai/data/notable_ai_models.csv"
BENCHMARKS_URL = "https://epoch.ai/data/eci_benchmarks.csv"
_REQUEST_TIMEOUT = 60.0

_ARXIV_LINK = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?")


@dataclass(frozen=True)
class ModelRecord:
    """One notable model as Epoch AI describes it."""

    name: str
    organization: str | None
    publication_date: date | None
    domains: list[str]
    task: str | None
    parameters: float | None
    training_compute_flop: float | None
    accessibility: str | None
    open_weights: bool | None
    link: str | None
    arxiv_id: str | None


@dataclass(frozen=True)
class BenchmarkScore:
    """One model's score on one benchmark."""

    benchmark: str
    model: str
    score: float
    benchmark_release_date: date | None
    measured_on: date | None
    origin: str | None


def _clean(value: str | None) -> str | None:
    """Trim, and treat an empty cell as absent -- a CSV has no null."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _number(value: str | None) -> float | None:
    """A float, or None for a blank or unparseable cell.

    Unparseable rather than raising: one malformed parameter count should cost that column on
    that row, not the whole refresh.
    """
    text = _clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(value: str | None) -> date | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _yes_no(value: str | None) -> bool | None:
    text = _clean(value)
    if text is None:
        return None
    return text.strip().lower().startswith("y")


def arxiv_id_in(link: str | None) -> str | None:
    """The arXiv id in a link field, or None.

    The column sometimes holds several URLs on separate lines (a paper and a technical report),
    so this searches rather than parses, and takes the first arXiv one it finds.
    """
    if not link:
        return None
    match = _ARXIV_LINK.search(link)
    return match.group(1) if match else None


def parse_models(text: str) -> list[ModelRecord]:
    """Parse the notable-models CSV. Rows without a model name are skipped."""
    records: list[ModelRecord] = []
    for row in csv.DictReader(io.StringIO(text)):
        name = _clean(row.get("Model"))
        if name is None:
            continue
        link = _clean(row.get("Link"))
        domains = [
            part.strip() for part in (_clean(row.get("Domain")) or "").split(",") if part.strip()
        ]
        records.append(
            ModelRecord(
                name=name,
                organization=_clean(row.get("Organization")),
                publication_date=_date(row.get("Publication date")),
                domains=domains,
                task=_clean(row.get("Task")),
                parameters=_number(row.get("Parameters")),
                training_compute_flop=_number(row.get("Training compute (FLOP)")),
                accessibility=_clean(row.get("Model accessibility")),
                open_weights=_yes_no(row.get("Open model weights?")),
                link=link.splitlines()[0].strip() if link else None,
                arxiv_id=arxiv_id_in(link),
            )
        )
    return records


def parse_benchmarks(text: str) -> list[BenchmarkScore]:
    """Parse the benchmark CSV. Rows missing a benchmark, a model or a score are skipped."""
    scores: list[BenchmarkScore] = []
    for row in csv.DictReader(io.StringIO(text)):
        benchmark = _clean(row.get("benchmark"))
        model = _clean(row.get("Model")) or _clean(row.get("model"))
        value = _number(row.get("performance"))
        if benchmark is None or model is None or value is None:
            continue
        scores.append(
            BenchmarkScore(
                benchmark=benchmark,
                model=model,
                score=value,
                benchmark_release_date=_date(row.get("benchmark_release_date")),
                measured_on=_date(row.get("date")),
                origin=_clean(row.get("source")),
            )
        )
    return scores


def _get(url: str) -> str:
    response = httpx.get(
        url, headers=default_headers(), timeout=_REQUEST_TIMEOUT, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def fetch_models() -> list[ModelRecord]:
    """Download and parse the notable-models catalogue."""
    return parse_models(_get(MODELS_URL))


def fetch_benchmarks() -> list[BenchmarkScore]:
    """Download and parse the benchmark scores."""
    return parse_benchmarks(_get(BENCHMARKS_URL))
