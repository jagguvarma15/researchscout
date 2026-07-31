"""Optional cross-encoder reranking: a precise second pass over the top first-stage candidates.

Hybrid RRF retrieval is fast but coarse — it never reads the query and a document together. A cross
encoder does: it jointly encodes each (query, paper) pair and scores relevance directly, which is
markedly sharper than comparing bag-of-vectors similarity. It is too expensive to run over the whole
corpus, so it reranks only the handful of candidates the first stage already surfaced.

Reranking is off by default (a CPU model still adds latency); enable it with ``RS_RERANK_ENABLED``.
When on, a paper's cross-encoder relevance (squashed to ``[0, 1]``) replaces the RRF term while the
recency-and-breakthrough prior is kept, so freshness and momentum still shape the final order.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cached_property, lru_cache
from typing import Any

from researchscout.config import get_settings


@dataclass
class Candidate:
    """A first-stage hit carried into reranking."""

    key: str  # opaque identity (the paper id)
    text: str  # what the cross encoder scores against the query
    prior: float  # recency x (1 + breakthrough): the "worth reading now" multiplier
    first_stage: float  # rrf x prior: selects the top-N and orders the no-rerank path


class Reranker(ABC):
    """Scores query-document relevance. Any implementation hides behind this interface."""

    @abstractmethod
    def scores(self, query: str, documents: list[str]) -> list[float]:
        """Relevance per document in ``[0, 1]``, parallel to the input (higher is more relevant)."""


class CrossEncoderReranker(Reranker):
    """A sentence-transformers cross encoder, loaded lazily, run on CPU (or MPS when available)."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    @cached_property
    def _model(self) -> Any:
        import torch
        from sentence_transformers import CrossEncoder

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        return CrossEncoder(self.model_id, device=device)

    def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        logits = self._model.predict([(query, doc) for doc in documents])
        return [_sigmoid(float(logit)) for logit in logits]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@lru_cache(maxsize=2)
def _cross_encoder(model_id: str) -> CrossEncoderReranker:
    """One shared cross encoder per model id, so the weights load once per process."""
    return CrossEncoderReranker(model_id)


def get_reranker() -> Reranker | None:
    """The configured reranker, or None when reranking is disabled."""
    settings = get_settings()
    if not settings.rerank_enabled:
        return None
    return _cross_encoder(settings.rerank_model)


def rerank(
    query: str,
    candidates: list[Candidate],
    reranker: Reranker | None,
    *,
    top_n: int,
) -> list[tuple[str, float, float | None]]:
    """Rerank the top-N first-stage candidates; returns (key, blended score, raw relevance).

    With ``reranker`` None this is a pure pass-through of the first-stage order (relevance
    None), so retrieval is unchanged when reranking is off. Otherwise each candidate's
    cross-encoder relevance replaces the RRF term while its recency-and-breakthrough prior
    is kept — and the raw calibrated relevance in ``[0, 1]`` rides along, because it is the
    only absolute signal of whether anything actually matched the query.
    """
    ordered = sorted(candidates, key=lambda c: c.first_stage, reverse=True)[:top_n]
    if reranker is None:
        return [(c.key, c.first_stage, None) for c in ordered]
    relevances = reranker.scores(query, [c.text for c in ordered])
    blended: list[tuple[str, float, float | None]] = [
        (c.key, rel * c.prior, rel) for c, rel in zip(ordered, relevances, strict=True)
    ]
    blended.sort(key=lambda kv: kv[1], reverse=True)
    return blended
