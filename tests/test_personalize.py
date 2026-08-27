import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import researchscout.retrieve.personalize as personalize_mod
from researchscout.embed.base import Embedder
from researchscout.retrieve.personalize import (
    _cosine,
    _Entry,
    _mmr,
    interest_centroid,
    personalized_papers,
    profile_clusters,
)
from researchscout.schema import Author, Paper


class StubEmbedder(Embedder):
    model_id = "stub"
    dim = 3

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._mapping[text]


def test_interest_centroid_averages_and_normalizes() -> None:
    embedder = StubEmbedder({"a": [3.0, 0.0, 0.0], "b": [0.0, 4.0, 0.0]})
    centroid = interest_centroid(embedder, ["a", "b"])
    assert centroid == pytest.approx([0.6, 0.8, 0.0])  # mean [1.5, 2, 0] normalized
    assert math.isclose(sum(x * x for x in centroid), 1.0)


def test_interest_centroid_none_when_empty() -> None:
    embedder = StubEmbedder({})
    assert interest_centroid(embedder, []) is None
    assert interest_centroid(embedder, ["  ", ""]) is None


def test_cosine() -> None:
    assert _cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == 0.0
    assert _cosine([1.0, 0.0], [0.5, 0.0]) == 0.5


def _entry(pid: str, vector: list[float], score: float) -> _Entry:
    paper = Paper(
        id=pid,
        title=pid,
        abstract="x",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        published_at=datetime.now(UTC),
        source="arxiv",
    )
    return _Entry(paper=paper, vector=vector, similarity=0.9, score=score, reason=None)


def test_mmr_prefers_diversity_over_near_duplicates() -> None:
    first = _entry("arxiv:a", [1.0, 0.0], 1.0)
    duplicate = _entry("arxiv:b", [0.999, 0.01], 0.99)
    distinct = _entry("arxiv:c", [0.0, 1.0], 0.5)
    picked = _mmr([first, duplicate, distinct], k=2, lam=0.5)
    assert [entry.paper.id for entry in picked] == ["arxiv:a", "arxiv:c"]
    # Pure relevance keeps the near-duplicate instead.
    picked = _mmr([first, duplicate, distinct], k=2, lam=1.0)
    assert [entry.paper.id for entry in picked] == ["arxiv:a", "arxiv:b"]


def test_profile_clusters_split_interests_and_anchor_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    saved = [
        ("arxiv:v1", "Vision One", now, [1.0, 0.0]),
        ("arxiv:v2", "Vision Two", now - timedelta(days=400), [0.99, 0.01]),
        ("arxiv:r1", "RL One", now, [0.0, 1.0]),
    ]
    monkeypatch.setattr(personalize_mod, "saved_vectors", lambda s, u, m: saved)
    clusters = profile_clusters(
        None,  # type: ignore[arg-type]
        StubEmbedder({}),
        "local",
        [],
        k=2,
        half_life_days=75.0,
    )
    assert len(clusters) == 2
    reasons = {cluster.reason for cluster in clusters}
    assert "Close to your saved paper: RL One" in reasons
    # The fresh vision save outweighs the 400-day-old one as the cluster anchor.
    assert "Close to your saved paper: Vision One" in reasons
    for cluster in clusters:
        norm = sum(value * value for value in cluster.centroid) ** 0.5
        assert norm == pytest.approx(1.0)


def test_profile_clusters_empty_without_saves_or_interests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(personalize_mod, "saved_vectors", lambda s, u, m: [])
    assert (
        profile_clusters(
            None,  # type: ignore[arg-type]
            StubEmbedder({}),
            "local",
            ["  "],
            k=3,
            half_life_days=75.0,
        )
        == []
    )


def test_profile_clusters_never_reads_events_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = [("arxiv:a", "A", datetime.now(UTC), [1.0, 0.0])]
    monkeypatch.setattr(personalize_mod, "saved_vectors", lambda s, u, m: saved)

    def _forbidden(*args: object, **kwargs: object) -> list[object]:
        raise AssertionError("positive_event_vectors must not run with RS_FORYOU_EVENTS off")

    monkeypatch.setattr(personalize_mod, "positive_event_vectors", _forbidden)
    clusters = profile_clusters(
        None,  # type: ignore[arg-type]
        StubEmbedder({}),
        "local",
        [],
        k=1,
        half_life_days=75.0,
    )
    assert len(clusters) == 1


def test_profile_clusters_blends_events_at_reduced_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RS_FORYOU_EVENTS", "true")
    now = datetime.now(UTC)
    monkeypatch.setattr(
        personalize_mod, "saved_vectors", lambda s, u, m: [("arxiv:s", "Saved", now, [1.0, 0.0])]
    )
    monkeypatch.setattr(
        personalize_mod,
        "positive_event_vectors",
        lambda s, u, m: [("arxiv:e", "Opened", now, [0.0, 1.0])],
    )
    clusters = profile_clusters(
        None,  # type: ignore[arg-type]
        StubEmbedder({}),
        "local",
        [],
        k=2,
        half_life_days=75.0,
    )
    reasons = {cluster.reason for cluster in clusters}
    # The opened paper anchors its own cluster, but a same-cluster save would outweigh it:
    # the event weight is a fraction of a save's.
    assert reasons == {"Close to your saved paper: Saved", "Like a paper you read: Opened"}


DIM = 384


def _onehot(i: int) -> list[float]:
    vector = [0.0] * DIM
    vector[i] = 1.0
    return vector


class MockEmbedder(Embedder):
    model_id = "mock-v1"
    dim = DIM

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._mapping[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._mapping[text]


@pytest.mark.integration
def test_personalized_ranks_interest_aligned_first(session: Session) -> None:
    from researchscout.schema import Author, Paper
    from researchscout.store.papers import upsert_paper
    from researchscout.store.vectors import upsert_embedding

    def _paper(pid: str) -> Paper:
        return Paper(
            id=pid,
            external_ids={"arxiv": pid.split(":")[1]},
            title=pid,
            abstract="x",
            authors=[Author(name="A")],
            categories=["cs.LG"],
            published_at=datetime.now(UTC) - timedelta(days=1),
            source="arxiv",
        )

    embedder = MockEmbedder({"vision": _onehot(1)})
    upsert_paper(session, _paper("arxiv:1"))
    upsert_paper(session, _paper("arxiv:2"))
    upsert_embedding(session, "arxiv:1", embedder.model_id, _onehot(1))  # aligned with "vision"
    upsert_embedding(session, "arxiv:2", embedder.model_id, _onehot(0))  # orthogonal
    session.flush()

    results = personalized_papers(session, embedder, ["vision"], k=10, days=30)
    assert [item.paper.id for item in results] == ["arxiv:1", "arxiv:2"]
    assert all(item.reason is None for item in results)  # legacy path names no reasons


@pytest.mark.integration
def test_personalized_cold_start_is_empty(session: Session) -> None:
    assert personalized_papers(session, MockEmbedder({}), [], k=10, days=30) == []


@pytest.mark.integration
def test_v2_names_reasons_and_fills_explore_slots(
    session: Session, set_setting: Callable[[str, str], None]
) -> None:
    from researchscout.schema import Author, Paper, Signal, SignalType
    from researchscout.store.papers import upsert_paper
    from researchscout.store.saved import save_paper
    from researchscout.store.signals import append_signal
    from researchscout.store.vectors import upsert_embedding

    def _paper(pid: str, title: str) -> Paper:
        return Paper(
            id=pid,
            external_ids={"arxiv": pid.split(":")[1]},
            title=title,
            abstract="x",
            authors=[Author(name="A")],
            categories=["cs.LG"],
            published_at=datetime.now(UTC) - timedelta(days=1),
            source="arxiv",
        )

    embedder = MockEmbedder({})
    upsert_paper(session, _paper("arxiv:1", "Saved Vision Paper"))
    upsert_paper(session, _paper("arxiv:2", "Fresh Vision Paper"))
    upsert_paper(session, _paper("arxiv:3", "Rising Outsider"))
    upsert_embedding(session, "arxiv:1", embedder.model_id, _onehot(1))
    upsert_embedding(session, "arxiv:2", embedder.model_id, _onehot(1))
    upsert_embedding(session, "arxiv:3", embedder.model_id, _onehot(0))  # outside the profile
    save_paper(session, "local", "arxiv:1")
    append_signal(
        session,
        Signal(
            paper_id="arxiv:3",
            type=SignalType.citation,
            source="test",
            value=50.0,
            observed_at=datetime.now(UTC),
        ),
    )
    session.flush()

    # set_setting rather than setenv: the session fixture builds the engine, which reads the
    # configuration and so populates its cache before this line runs. Setting the environment
    # afterwards changes nothing without dropping that cache, which is what a restart does.
    set_setting("RS_FORYOU_CENTROIDS", "2")
    set_setting("RS_FORYOU_EXPLORE_SLOTS", "1")
    results = personalized_papers(session, embedder, [], user_sub="local", k=3, days=30)

    by_id = {item.paper.id: item for item in results}
    assert by_id["arxiv:2"].reason == "Close to your saved paper: Saved Vision Paper"
    assert by_id["arxiv:3"].reason == "Rising outside your usual topics"


@pytest.mark.integration
def test_events_flag_filters_dismissed_papers(
    session: Session, set_setting: Callable[[str, str], None]
) -> None:
    from researchscout.schema import Author, Paper
    from researchscout.store.events import EventInput, append_events
    from researchscout.store.papers import upsert_paper
    from researchscout.store.saved import save_paper
    from researchscout.store.vectors import upsert_embedding

    def _paper(pid: str, title: str) -> Paper:
        return Paper(
            id=pid,
            external_ids={"arxiv": pid.split(":")[1]},
            title=title,
            abstract="x",
            authors=[Author(name="A")],
            categories=["cs.LG"],
            published_at=datetime.now(UTC) - timedelta(days=1),
            source="arxiv",
        )

    embedder = MockEmbedder({})
    upsert_paper(session, _paper("arxiv:1", "Saved Anchor"))
    upsert_paper(session, _paper("arxiv:2", "Fresh Match"))
    upsert_paper(session, _paper("arxiv:3", "Dismissed Match"))
    for pid in ("arxiv:1", "arxiv:2", "arxiv:3"):
        upsert_embedding(session, pid, embedder.model_id, _onehot(1))
    save_paper(session, "local", "arxiv:1")
    append_events(session, "local", [EventInput(event="dismiss", paper_id="arxiv:3")])
    session.flush()

    set_setting("RS_FORYOU_CENTROIDS", "1")
    results = personalized_papers(session, embedder, [], user_sub="local", k=10, days=30)
    assert {item.paper.id for item in results} == {"arxiv:1", "arxiv:2", "arxiv:3"}

    set_setting("RS_FORYOU_EVENTS", "true")
    results = personalized_papers(session, embedder, [], user_sub="local", k=10, days=30)
    assert {item.paper.id for item in results} == {"arxiv:1", "arxiv:2"}
