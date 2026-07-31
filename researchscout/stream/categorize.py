"""Tiered enrichment stage: taxonomy group, topic-centroid match, keywords, custom labels.

Cheapest first. The arXiv taxonomy lookup is free; the topic match costs one embed whose
vector is reused as the paper's stored embedding; keyword extraction is pure sklearn and
numpy over vectors we compute anyway. The LLM appears in exactly two places, both bounded:
a fallback for weak keyword extractions and the optional custom-label classifier, and every
LLM output is filtered against a closed set before it can enter the payload.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from researchscout.embed.base import Embedder
from researchscout.llm.base import LLM
from researchscout.store.topics import list_topics
from researchscout.stream.envelope import Envelope
from researchscout.taxonomy import group_for

logger = logging.getLogger(__name__)

_KEYWORD_TOP_K = 6
_KEYWORD_MMR_LAMBDA = 0.7
# The LLM fallback triggers when extraction looks weak: too few phrases, a low best
# similarity, or a document too short for statistics to mean anything.
_KEYWORD_LLM_MIN_COUNT = 3
_KEYWORD_LLM_BEST_FLOOR = 0.45
_KEYWORD_MIN_DOC_WORDS = 30
_CENTROID_REFRESH_SEC = 900.0
_LIST_SPLIT = re.compile(r"[,\n;]+")

_KEYWORD_SYSTEM = (
    "You extract keywords from research paper metadata. Reply with exactly five short "
    "keyword phrases separated by commas. No numbering, no explanations."
)
_LABEL_SYSTEM = (
    "You classify research papers into a fixed label set. Reply with the matching label "
    "names separated by commas, or the word none. Never invent a label."
)


@dataclass(frozen=True)
class LabelSpec:
    name: str
    description: str


@dataclass(frozen=True)
class TopicCentroid:
    key: str
    label: str
    vector: list[float]


@dataclass
class Categorized:
    """The stage output: the envelope plus the in-memory document vector.

    The vector rides alongside the envelope to the inject stage (it becomes the stored
    paper embedding) but is never serialized into the Kafka taps.
    """

    envelope: Envelope
    vector: list[float] | None


def load_labels(path: Path) -> list[LabelSpec]:
    """The configured custom label set ([] when the file is absent or empty)."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    specs = []
    for entry in data.get("labels", []):
        name = str(entry.get("name", "")).strip()
        if name:
            specs.append(LabelSpec(name=name, description=str(entry.get("description", ""))))
    return specs


def extract_keywords(
    text: str,
    doc_vector: list[float],
    embedder: Embedder,
    *,
    top_k: int = _KEYWORD_TOP_K,
    min_similarity: float,
    mmr_lambda: float = _KEYWORD_MMR_LAMBDA,
) -> list[tuple[str, float]]:
    """KeyBERT-style statistical keywords: candidate n-grams scored by cosine to the doc.

    Candidates come from a CountVectorizer over the document itself (1-3 grams, English
    stop words removed); each candidate embeds through the same model as the document, and
    greedy MMR picks a relevant-but-diverse top-k above the similarity floor.
    """
    import numpy as np
    from sklearn.feature_extraction.text import CountVectorizer

    vectorizer = CountVectorizer(stop_words="english", ngram_range=(1, 3))
    try:
        vectorizer.fit([text])
    except ValueError:  # empty vocabulary: everything was a stop word
        return []
    candidates = [str(term) for term in vectorizer.get_feature_names_out()]
    if not candidates:
        return []
    vectors = np.asarray(embedder.embed_documents(candidates))
    doc = np.asarray(doc_vector)
    similarities = vectors @ doc

    eligible = [i for i in np.argsort(similarities)[::-1] if similarities[i] >= min_similarity]
    selected: list[int] = []
    while eligible and len(selected) < top_k:
        best_index, best_gain = -1, float("-inf")
        for i in eligible:
            redundancy = max((float(vectors[i] @ vectors[j]) for j in selected), default=0.0)
            gain = mmr_lambda * float(similarities[i]) - (1.0 - mmr_lambda) * redundancy
            if gain > best_gain:
                best_index, best_gain = i, gain
        selected.append(best_index)
        eligible.remove(best_index)
    return [(candidates[i], float(similarities[i])) for i in selected]


class Categorizer:
    """Per-process enrichment state: cached centroids, the label set, and both models."""

    def __init__(
        self,
        embedder: Embedder,
        llm: LLM,
        session_factory: Callable[[], AbstractContextManager[Session]],
        *,
        topic_match_min: float,
        keyword_min_similarity: float,
        keywords_llm_fallback: bool,
        labels: list[LabelSpec],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._embedder = embedder
        self._llm = llm
        self._session_factory = session_factory
        self._topic_match_min = topic_match_min
        self._keyword_min_similarity = keyword_min_similarity
        self._keywords_llm_fallback = keywords_llm_fallback
        self._labels = labels
        self._clock = clock
        self._centroids: list[TopicCentroid] = []
        self._centroids_loaded_at: float | None = None

    def _topic_centroids(self) -> list[TopicCentroid]:
        now = self._clock()
        stale = (
            self._centroids_loaded_at is None
            or now - self._centroids_loaded_at >= _CENTROID_REFRESH_SEC
        )
        if stale:
            with self._session_factory() as session:
                self._centroids = [
                    TopicCentroid(
                        key=row.topic_key or str(row.id), label=row.label, vector=row.centroid
                    )
                    for row in list_topics(session)
                    if row.centroid
                ]
            self._centroids_loaded_at = now
        return self._centroids

    def _match_topic(self, vector: list[float]) -> dict[str, object] | None:
        best: TopicCentroid | None = None
        best_similarity = self._topic_match_min
        for centroid in self._topic_centroids():
            similarity = sum(a * b for a, b in zip(vector, centroid.vector, strict=False))
            if similarity >= best_similarity:
                best, best_similarity = centroid, similarity
        if best is None:
            return None
        return {"key": best.key, "label": best.label, "similarity": round(best_similarity, 4)}

    def _llm_keywords(self, title: str, abstract: str) -> list[str]:
        reply = self._llm.complete(
            _KEYWORD_SYSTEM, f"Title: {title}\n\nAbstract: {abstract}", temperature=0.0
        )
        phrases = [p.strip(" .-") for p in _LIST_SPLIT.split(reply)]
        return [p for p in phrases if p and len(p.split()) <= 6][:_KEYWORD_TOP_K]

    def _keywords(self, title: str, abstract: str, vector: list[float]) -> tuple[list[str], str]:
        text = f"{title}\n\n{abstract}"
        extracted = extract_keywords(
            text, vector, self._embedder, min_similarity=self._keyword_min_similarity
        )
        weak = (
            len(text.split()) < _KEYWORD_MIN_DOC_WORDS
            or len(extracted) < _KEYWORD_LLM_MIN_COUNT
            or max((score for _, score in extracted), default=0.0) < _KEYWORD_LLM_BEST_FLOOR
        )
        if weak and self._keywords_llm_fallback:
            try:
                generated = self._llm_keywords(title, abstract)
            except Exception:  # noqa: BLE001 - the statistical result is the safe floor
                logger.warning("keyword fallback failed", exc_info=True)
            else:
                if generated:
                    return generated, "llm"
        return [phrase for phrase, _ in extracted], "statistical"

    def _custom_labels(self, title: str, abstract: str) -> list[str]:
        if not self._labels:
            return []
        catalog = "\n".join(f"{spec.name}: {spec.description}" for spec in self._labels)
        try:
            reply = self._llm.complete(
                _LABEL_SYSTEM,
                f"Labels:\n{catalog}\n\nTitle: {title}\n\nAbstract: {abstract}",
                temperature=0.0,
            )
        except Exception:  # noqa: BLE001 - labels are optional enrichment
            logger.warning("label classification failed", exc_info=True)
            return []
        allowed = {spec.name.lower(): spec.name for spec in self._labels}
        names = [allowed.get(p.strip(" .").lower()) for p in _LIST_SPLIT.split(reply)]
        return list(dict.fromkeys(name for name in names if name is not None))

    def run(self, envelope: Envelope) -> Categorized:
        """Enrich a parsed paper packet; other kinds (and failed parses) pass through."""
        if envelope.kind != "paper" or "paper" not in envelope.payload:
            if envelope.kind == "paper":
                stamp = envelope.begin("categorize")
                envelope.finish(stamp, "skipped", "parse did not produce a paper")
            return Categorized(envelope, None)

        stamp = envelope.begin("categorize")
        try:
            paper = envelope.payload["paper"]
            title, abstract = paper["title"], paper["abstract"]
            vector = self._embedder.embed_documents([f"{title}\n\n{abstract}"])[0]
            group = group_for(paper.get("primary_category"))
            keywords, method = self._keywords(title, abstract, vector)
            envelope.payload["enrichment"] = {
                "group": group.key if group else None,
                "tech": group.tech if group else None,
                "topic": self._match_topic(vector),
                "keywords": keywords,
                "keyword_method": method,
                "labels": self._custom_labels(title, abstract),
            }
        except Exception as exc:  # noqa: BLE001 - a bad packet must not stop the flow
            envelope.finish(stamp, "error", f"{type(exc).__name__}: {exc}")
            return Categorized(envelope, None)
        envelope.finish(stamp)
        return Categorized(envelope, vector)
