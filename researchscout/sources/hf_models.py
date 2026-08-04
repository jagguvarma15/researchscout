"""Hugging Face: open-weight models, how much they are used, and the papers behind them.

Epoch AI's catalogue is editorial -- a model is in it because somebody judged it notable, which
is the right filter for a landscape but means a widely used open-weight release can be missing
for months. The Hub knows about those the day they appear, and knows two things Epoch does not:
how many people actually download them, and, through the ``arxiv:`` tags on a model card, which
paper they came from.

That tag is the reason this source exists. It turns "here is a list of models" into "here is the
model that came out of the paper you are reading", which is a question only a site holding both
can answer.

Keyless: the Hub's model listing is public and documented for programmatic use. One request per
refresh, paged, with a cap -- there is no call for walking two million repositories to build a
landscape page.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from researchscout.useragent import default_headers

logger = logging.getLogger(__name__)

MODELS_URL = "https://huggingface.co/api/models"
_REQUEST_TIMEOUT = 30.0
_PAGE = 100

#: The task tags worth listing on a page about language and vision models. Anything else on the
#: Hub (tabular regression, reinforcement-learning environments, audio classification) is real
#: work and not what this page is about.
PIPELINES = (
    "text-generation",
    "image-text-to-text",
    "visual-question-answering",
    "text-to-image",
    "automatic-speech-recognition",
)

_ARXIV_TAG = re.compile(r"^arxiv:([0-9]{4}\.[0-9]{4,5})(?:v\d+)?$")


@dataclass(frozen=True)
class HubModel:
    """One Hub repository, as much of it as a landscape page needs."""

    repo: str
    pipeline: str | None
    downloads: int
    likes: int
    created_at: datetime | None
    arxiv_ids: list[str]

    @property
    def name(self) -> str:
        """The repository name without its owner: ``Qwen/Qwen3-0.6B`` -> ``Qwen3-0.6B``."""
        return self.repo.split("/", 1)[-1]

    @property
    def owner(self) -> str | None:
        """The organisation that published it, when the repo is namespaced."""
        return self.repo.split("/", 1)[0] if "/" in self.repo else None

    @property
    def primary_arxiv_id(self) -> str | None:
        """The paper this model is most likely to have come from, or None.

        A card carrying one tag is unambiguous -- ``openai/whisper-large-v3`` tags the Whisper
        paper and nothing else. A card carrying several is citing its ancestors as well as
        itself, and the newest is the one that is about *this* model: an ASR release tagging
        ``2604.19079``, ``2305.05084`` and ``2304.09325`` came out of the first and merely
        stands on the others.

        Taking the wrong one is not a harmless miss. Several such models resolved to "Attention
        Is All You Need" on a first pass, which would have put half of Hugging Face under one
        2017 paper. arXiv ids begin YYMM, so the newest is simply the largest.
        """
        return max(self.arxiv_ids) if self.arxiv_ids else None


def arxiv_ids_in(tags: list[str]) -> list[str]:
    """The arXiv ids among a model card's tags, in order and without duplicates."""
    seen: dict[str, None] = {}
    for tag in tags:
        match = _ARXIV_TAG.match(str(tag).strip())
        if match:
            seen.setdefault(match.group(1), None)
    return list(seen)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_models(payload: Any) -> list[HubModel]:
    """Turn the Hub's JSON listing into records; entries without an id are skipped."""
    if not isinstance(payload, list):
        return []
    models: list[HubModel] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        repo = entry.get("id") or entry.get("modelId")
        if not repo:
            continue
        tags = entry.get("tags")
        models.append(
            HubModel(
                repo=str(repo),
                pipeline=entry.get("pipeline_tag"),
                downloads=int(entry.get("downloads") or 0),
                likes=int(entry.get("likes") or 0),
                created_at=_timestamp(entry.get("createdAt")),
                arxiv_ids=arxiv_ids_in(tags if isinstance(tags, list) else []),
            )
        )
    return models


def fetch_models(*, limit: int = 200, pipelines: tuple[str, ...] = PIPELINES) -> list[HubModel]:
    """The most downloaded models for each listed task, up to ``limit`` per task.

    Sorted by downloads rather than by recency: this feeds a landscape, and a landscape is what
    people are using, not what was uploaded in the last hour. A task whose request fails is
    logged and skipped, so one bad response costs one row of the page rather than the page.
    """
    out: dict[str, HubModel] = {}
    for pipeline in pipelines:
        for offset in range(0, limit, _PAGE):
            try:
                response = httpx.get(
                    MODELS_URL,
                    params={
                        "filter": pipeline,
                        "sort": "downloads",
                        "direction": -1,
                        "limit": min(_PAGE, limit - offset),
                        "skip": offset,
                        "full": "true",
                    },
                    headers=default_headers(),
                    timeout=_REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("hugging face models (%s) failed: %s", pipeline, exc)
                break
            page = parse_models(response.json())
            if not page:
                break
            for model in page:
                # A model can carry several task tags; first listing wins, so the ordering
                # above decides which task it is filed under.
                out.setdefault(model.repo, model)
    return list(out.values())
