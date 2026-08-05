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
import zipfile
from dataclasses import dataclass
from datetime import date

import httpx

from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

MODELS_URL = "https://epoch.ai/data/notable_ai_models.csv"
BENCHMARKS_URL = "https://epoch.ai/data/benchmark_data.zip"
_REQUEST_TIMEOUT = 60.0

#: Columns in a benchmark file that hold a number without holding a score. The score is found
#: positionally (see :func:`score_column`), and these sit inside the range it searches.
_NOT_A_SCORE = frozenset({"release date", "training compute (flop)", "step budget"})

#: Where the metadata block starts in every benchmark file: model identification and scores
#: come before it, and nothing after it is a score.
_METADATA_START = "release date"

#: Benchmark files that are not a benchmark.
_NOT_A_BENCHMARK = frozenset({"epoch_capabilities_index"})

#: How a benchmark is written when the field writes it a particular way. Deriving the name from
#: the file stem gets "Gpqa diamond" and "Swe bench verified", which nobody calls them. Only the
#: ones with a settled spelling are listed; anything else falls back to the derived form, which
#: is serviceable and self-maintaining as the hub adds files.
#:
#: These names also decide the ids, since a benchmark is keyed by a slug of its name. Renaming
#: one here changes the URL of its leaderboard, so it is worth getting right once.
_DISPLAY_NAMES = {
    "adversarial_nli": "Adversarial NLI",
    "arc_agi": "ARC-AGI",
    "arc_agi_2": "ARC-AGI-2",
    "bbh": "BIG-Bench Hard",
    "bool_q": "BoolQ",
    "frontiermath": "FrontierMath",
    "frontiermath_tier_4": "FrontierMath Tier 4",
    "gdpval": "GDPval",
    "gpqa_diamond": "GPQA Diamond",
    "gsm8k": "GSM8K",
    "hella_swag": "HellaSwag",
    "hle": "Humanity's Last Exam",
    "lambada": "LAMBADA",
    "live_bench": "LiveBench",
    "math_level_5": "MATH Level 5",
    "mmlu": "MMLU",
    "open_book_qa": "OpenBookQA",
    "os_world": "OSWorld",
    "osworld_2": "OSWorld 2",
    "otis_mock_aime_2024_2025": "OTIS Mock AIME",
    "piqa": "PIQA",
    "science_qa": "ScienceQA",
    "scicode": "SciCode",
    "simpleqa_verified": "SimpleQA Verified",
    "superglue": "SuperGLUE",
    "swe_bench_verified": "SWE-bench Verified",
    "terminalbench": "Terminal-Bench",
    "trivia_qa": "TriviaQA",
    "video_mme": "Video-MME",
    "webdev_arena": "WebDev Arena",
    "wino_grande": "WinoGrande",
}

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
    """One model's score on one benchmark.

    ``organization`` and ``training_compute_flop`` ride along because the benchmark files carry
    them, and a model that has been measured but is not in the notable-models catalogue would
    otherwise have no row to link to - which is most of the leaderboard.
    """

    benchmark: str
    model: str
    score: float
    benchmark_release_date: date | None
    measured_on: date | None
    origin: str | None
    organization: str | None = None
    training_compute_flop: float | None = None


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


def benchmark_name(filename: str) -> str:
    """Turn ``gpqa_diamond.csv`` into ``Gpqa diamond``, which the slug then keys on.

    The file name is the only place the benchmark is named - the rows inside carry models, not
    the thing they were measured against - so the name comes from here. ``_external`` marks a
    score Epoch collected from somebody else's leaderboard rather than ran itself; that is a
    provenance fact, recorded per score, not part of what the benchmark is called.
    """
    stem = filename.rsplit("/", 1)[-1].removesuffix(".csv").removesuffix("_external")
    return _DISPLAY_NAMES.get(stem) or stem.replace("_", " ").strip().capitalize()


def score_column(fieldnames: list[str], rows: list[dict[str, str]]) -> str | None:
    """Which column holds the score, or None when the file has no usable one.

    Found by position rather than by name, because the name is per-benchmark: mean_score,
    Accuracy mean, Percent correct, Pass@1, Win Rate (%), Pooled score, and so on for seventy
    files. What every file does share is a shape - the model, then its scores, then a metadata
    block beginning at "Release date" - so the score is the first column before that block
    whose values are numbers. That skips a categorical qualifier like Agent or Scaffold without
    having to know it exists, and it survives a benchmark being added with yet another name.
    """
    for name in fieldnames:
        lowered = name.strip().casefold()
        if lowered == _METADATA_START:
            return None  # reached the metadata block without finding one
        if lowered in _NOT_A_SCORE or lowered == "model version":
            continue
        if any(_number(row.get(name)) is not None for row in rows):
            return name
    return None


def parse_benchmark_file(filename: str, text: str) -> list[BenchmarkScore]:
    """Parse one benchmark's CSV out of the archive; an unreadable shape yields nothing."""
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows or not reader.fieldnames:
        return []
    column = score_column(list(reader.fieldnames), rows)
    if column is None:
        logger.info("no score column in %s, skipping it", filename)
        return []

    name = benchmark_name(filename)
    origin = "Epoch AI" if not filename.endswith("_external.csv") else "External leaderboard"
    scores: list[BenchmarkScore] = []
    for row in rows:
        model = _clean(row.get("Model version")) or _clean(row.get("Model"))
        value = _number(row.get(column))
        if model is None or value is None:
            continue
        scores.append(
            BenchmarkScore(
                benchmark=name,
                model=model,
                score=value,
                benchmark_release_date=None,
                measured_on=_date(row.get("Release date")),
                origin=origin,
                organization=_clean(row.get("Organization")),
                training_compute_flop=_number(row.get("Training compute (FLOP)")),
            )
        )
    return scores


def parse_benchmarks(payload: bytes) -> list[BenchmarkScore]:
    """Parse every benchmark in the archive.

    One CSV per benchmark, plus a README and the capabilities index. The index is a composite
    of the others rather than a benchmark anyone was measured against, so it is left out: it
    would appear on a leaderboard as a score with no test behind it.
    """
    scores: list[BenchmarkScore] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith(".csv") or "/" in name:
                continue
            if name.removesuffix(".csv") in _NOT_A_BENCHMARK:
                continue
            text = archive.read(name).decode("utf-8", "replace")
            scores.extend(parse_benchmark_file(name, text))
    return scores


def _get(url: str) -> httpx.Response:
    response = httpx.get(
        url, headers=default_headers(), timeout=_REQUEST_TIMEOUT, follow_redirects=True
    )
    response.raise_for_status()
    return response


def fetch_models() -> list[ModelRecord]:
    """Download and parse the notable-models catalogue."""
    return parse_models(_get(MODELS_URL).text)


def fetch_benchmarks() -> list[BenchmarkScore]:
    """Download and parse every benchmark in the hub's archive."""
    return parse_benchmarks(_get(BENCHMARKS_URL).content)
