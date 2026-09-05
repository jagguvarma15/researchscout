from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from researchscout.cluster import Member, Topic
from researchscout.schema import Author, Paper
from researchscout.store.papers import upsert_paper
from researchscout.store.topics import classify_trend, list_topics, paper_meta, replace_topics


def _paper(pid: str) -> Paper:
    return Paper(
        id=pid,
        external_ids={"arxiv": pid.split(":")[-1]},
        title="T",
        abstract="a",
        authors=[Author(name="A")],
        categories=["cs.LG"],
        primary_category="cs.LG",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
        source="arxiv",
    )


def _topic(label: str, centroid: list[float], *, size: int = 3, score: float = 1.0) -> Topic:
    return Topic(
        label=label,
        summary=None,
        score=score,
        size=size,
        members=[Member(paper_id=f"arxiv:{label}", title=label, score=score)],
        centroid=centroid,
    )


def test_classify_trend_transitions() -> None:
    assert classify_trend([{"size": 3}]) == "new"
    assert classify_trend([{"size": 3}, {"size": 5}]) == "rising"
    assert classify_trend([{"size": 5}, {"size": 5}]) == "steady"
    assert classify_trend([{"size": 5}, {"size": 2}]) == "fading"


def test_classify_trend_reads_the_full_history() -> None:
    # A steady climb reads rising; its mirror reads fading.
    assert classify_trend([{"size": s} for s in [2, 3, 4, 5, 6, 7]]) == "rising"
    assert classify_trend([{"size": s} for s in [7, 6, 5, 4, 3, 2]]) == "fading"
    # A one-paper wobble around a level is steady, not a build-to-build flip (the last-two bug).
    assert classify_trend([{"size": s} for s in [5, 4, 5, 4, 5, 4]]) == "steady"
    # A long plateau with a single closing uptick does not flip to rising.
    assert classify_trend([{"size": s} for s in [8, 8, 8, 8, 8, 9]]) == "steady"


@pytest.mark.integration
def test_paper_meta_returns_category_and_date(session: Session) -> None:
    upsert_paper(session, _paper("arxiv:2608.00001"))
    session.flush()
    meta = paper_meta(session, ["arxiv:2608.00001", "arxiv:missing"])
    # A member no longer in the corpus simply has no entry.
    assert "arxiv:missing" not in meta
    entry = meta["arxiv:2608.00001"]
    assert entry.primary_category == "cs.LG"
    assert entry.published_at == datetime(2026, 8, 20, tzinfo=UTC)


@pytest.mark.integration
def test_topic_identity_carries_across_builds(session: Session) -> None:
    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    t2 = datetime(2026, 7, 2, tzinfo=UTC)
    t3 = datetime(2026, 7, 3, tzinfo=UTC)

    replace_topics(session, [_topic("diffusion", [1.0, 0.0], size=3)], built_at=t1)
    first = list_topics(session)[0]
    assert first.trend == "new"
    assert [point["size"] for point in first.history] == [3]

    # A similar centroid (cos ~0.98) inherits the key; an orthogonal one starts fresh.
    replace_topics(
        session,
        [
            _topic("diffusion relabeled", [0.98, 0.199], size=5, score=2.0),
            _topic("agents", [0.0, 1.0], size=2, score=1.0),
        ],
        built_at=t2,
    )
    rows = {row.label: row for row in list_topics(session)}
    carried, fresh = rows["diffusion relabeled"], rows["agents"]
    assert carried.topic_key == first.topic_key
    assert carried.trend == "rising"
    assert [point["size"] for point in carried.history] == [3, 5]
    assert carried.first_seen == t1
    assert fresh.topic_key != first.topic_key
    assert fresh.trend == "new"

    # Shrinking on the third build reads as fading, history intact.
    replace_topics(session, [_topic("diffusion again", [1.0, 0.05], size=2)], built_at=t3)
    final = list_topics(session)[0]
    assert final.topic_key == first.topic_key
    assert final.trend == "fading"
    assert [point["size"] for point in final.history] == [3, 5, 2]


@pytest.mark.integration
def test_previous_key_is_inherited_at_most_once(session: Session) -> None:
    replace_topics(session, [_topic("seed", [1.0, 0.0])], built_at=datetime(2026, 7, 1, tzinfo=UTC))
    seed_key = list_topics(session)[0].topic_key

    # Two near-identical new topics compete for one previous identity; momentum order wins.
    replace_topics(
        session,
        [
            _topic("winner", [1.0, 0.0], score=2.0),
            _topic("runner-up", [0.99, 0.1], score=1.0),
        ],
        built_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    rows = {row.label: row for row in list_topics(session)}
    assert rows["winner"].topic_key == seed_key
    assert rows["runner-up"].topic_key != seed_key
    assert rows["runner-up"].trend == "new"
