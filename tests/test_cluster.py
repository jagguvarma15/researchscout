from types import SimpleNamespace

import pytest

import researchscout.cluster as cluster_mod
from researchscout.cluster import (
    build_topics,
    cluster_keywords,
    cluster_labels,
    label_topic,
    representative_order,
    unit_centroid,
)
from researchscout.llm.base import LLM


def test_cluster_labels_separates_distant_groups() -> None:
    # Two tight groups, orthogonal to each other in cosine space -> two clusters.
    vectors = [
        [1.0, 0.0, 0.0],
        [0.99, 0.02, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.02, 0.99],
    ]
    labels = cluster_labels(vectors, threshold=0.5)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]
    assert len(set(labels)) == 2


def test_cluster_labels_edge_cases() -> None:
    assert cluster_labels([], threshold=0.5) == []
    assert cluster_labels([[1.0, 0.0]], threshold=0.5) == [0]


class _FakeLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        return self._reply


def test_label_topic_parses_label_and_summary() -> None:
    llm = _FakeLLM("Diffusion models\nFast image generation methods.")
    label, summary = label_topic(llm, ["t1", "t2"])
    assert label == "Diffusion models"
    assert summary == "Fast image generation methods."


def test_label_topic_without_summary() -> None:
    label, summary = label_topic(_FakeLLM("Sparse attention"), ["t1"])
    assert label == "Sparse attention"
    assert summary is None


def test_label_topic_empty_reply_falls_back() -> None:
    label, summary = label_topic(_FakeLLM("   "), ["t1"])
    assert label == "Untitled topic"
    assert summary is None


class _CapturingLLM(LLM):
    model = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.prompts.append(user)
        return "Label\nSummary."


def test_label_topic_prompt_carries_keywords_and_titles() -> None:
    llm = _CapturingLLM()
    label_topic(llm, ["A title"], ["diffusion", "guidance"])
    assert llm.prompts[0].startswith("Keywords: diffusion, guidance")
    assert "- A title" in llm.prompts[0]


def test_cluster_keywords_are_discriminative() -> None:
    keywords = cluster_keywords(
        {
            0: ["diffusion sampling models", "diffusion guidance models"],
            1: ["reinforcement policy models", "reinforcement reward models"],
        }
    )
    # "models" appears in both classes; the class-specific terms must outrank it.
    assert keywords[0][0] != "models"
    assert any("diffusion" in term for term in keywords[0])
    assert any("reinforcement" in term for term in keywords[1])
    assert not any("diffusion" in term for term in keywords[1])


def test_cluster_keywords_edge_cases() -> None:
    assert cluster_keywords({}) == {}
    # Nothing but stop words: an empty vocabulary must not raise.
    assert cluster_keywords({0: ["the of and"]}) == {0: []}


def test_representative_order_puts_the_centroid_neighbor_first() -> None:
    # Two aligned vectors and one outlier: the aligned ones are more typical.
    order = representative_order([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
    assert order[-1] == 2
    assert set(order[:2]) == {0, 1}


def test_hdbscan_finds_dense_groups_and_marks_outliers() -> None:
    vectors = [
        [1.0, 0.0, 0.0],
        [0.99, 0.02, 0.0],
        [0.98, 0.01, 0.01],
        [0.0, 0.0, 1.0],
        [0.0, 0.02, 0.99],
        [0.01, 0.01, 0.98],
        [-1.0, -0.5, -0.5],  # anti-aligned misfit: no cohort anywhere near it
    ]
    labels = cluster_labels(vectors, threshold=0.5, algo="hdbscan")
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]
    assert labels[6] == -1


def test_hdbscan_tiny_inputs_are_outliers() -> None:
    assert cluster_labels([], threshold=0.5, algo="hdbscan") == []
    assert cluster_labels([[1.0, 0.0]], threshold=0.5, algo="hdbscan") == [-1]


def test_build_topics_excludes_the_outlier_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        ("arxiv:1", "diffusion sampling", [1.0, 0.0]),
        ("arxiv:2", "diffusion guidance", [0.99, 0.01]),
        ("arxiv:3", "a lone misfit", [0.0, 1.0]),
    ]
    monkeypatch.setattr(cluster_mod, "window_vectors", lambda *a, **k: rows)
    monkeypatch.setattr(cluster_mod, "cluster_labels", lambda v, *, threshold, algo: [0, 0, -1])
    monkeypatch.setattr(cluster_mod, "breakthrough", lambda s, pid: SimpleNamespace(total=1.0))
    topics = build_topics(
        None,  # type: ignore[arg-type]
        SimpleNamespace(model_id="m"),  # type: ignore[arg-type]
        _FakeLLM("Label\nSummary."),
        days=30,
        threshold=0.5,
        algo="hdbscan",
    ).topics
    assert len(topics) == 1
    assert topics[0].size == 2
    assert {member.paper_id for member in topics[0].members} == {"arxiv:1", "arxiv:2"}
    norm = sum(value * value for value in topics[0].centroid) ** 0.5
    assert norm == pytest.approx(1.0)


class _QuotaError(Exception):
    status_code = 429


class _CountingLLM(LLM):
    """Counts completions; a script of replies/exceptions drives failure scenarios."""

    model = "fake"

    def __init__(self, script: list[str | Exception] | None = None) -> None:
        self.calls = 0
        self._script = script

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        if not self._script:
            return "Label\nSummary."
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _patch_three_clusters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three two-paper clusters whose summed scores rank them 1, 2, 0."""
    rows = [
        ("arxiv:1", "alpha one", [1.0, 0.0]),
        ("arxiv:2", "alpha two", [0.99, 0.01]),
        ("arxiv:3", "beta one", [0.0, 1.0]),
        ("arxiv:4", "beta two", [0.01, 0.99]),
        ("arxiv:5", "gamma one", [0.5, 0.5]),
        ("arxiv:6", "gamma two", [0.51, 0.49]),
    ]
    scores = {
        "arxiv:1": 1.0,
        "arxiv:2": 1.0,
        "arxiv:3": 5.0,
        "arxiv:4": 5.0,
        "arxiv:5": 3.0,
        "arxiv:6": 3.0,
    }
    keywords = {
        0: ["alpha", "one", "two", "extra"],
        1: ["beta", "one", "two"],
        2: ["gamma", "one"],
    }
    monkeypatch.setattr(cluster_mod, "window_vectors", lambda *a, **k: rows)
    monkeypatch.setattr(
        cluster_mod, "cluster_labels", lambda v, *, threshold, algo: [0, 0, 1, 1, 2, 2]
    )
    monkeypatch.setattr(
        cluster_mod, "breakthrough", lambda s, pid: SimpleNamespace(total=scores[pid])
    )
    monkeypatch.setattr(cluster_mod, "cluster_keywords", lambda docs, **k: keywords)


def _build(llm: LLM, *, max_topics: int = 12) -> cluster_mod.TopicBuild:
    return build_topics(
        None,  # type: ignore[arg-type]
        SimpleNamespace(model_id="m"),  # type: ignore[arg-type]
        llm,
        days=30,
        threshold=0.5,
        max_topics=max_topics,
    )


def test_build_topics_labels_only_the_ranked_survivors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_three_clusters(monkeypatch)
    llm = _CountingLLM()
    build = _build(llm, max_topics=2)
    assert llm.calls == 2
    assert build.llm_labels == 2
    assert build.fallback_labels == 0
    assert [topic.score for topic in build.topics] == [10.0, 6.0]


def test_a_label_failure_falls_back_to_keywords_without_latching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_three_clusters(monkeypatch)
    llm = _CountingLLM([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    build = _build(llm)
    assert llm.calls == 3  # a non-quota failure keeps trying the next cluster
    assert build.llm_labels == 0
    assert build.fallback_labels == 3
    labels = [topic.label for topic in build.topics]
    assert labels == ["beta, one, two", "gamma, one", "alpha, one, two"]
    assert all(topic.summary is None for topic in build.topics)


def test_the_first_quota_error_stops_all_later_label_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_three_clusters(monkeypatch)
    llm = _CountingLLM([_QuotaError("Error code: 429")])
    build = _build(llm)
    assert llm.calls == 1
    assert build.llm_labels == 0
    assert build.fallback_labels == 3


def test_degradation_counts_split_llm_and_fallback_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_three_clusters(monkeypatch)
    llm = _CountingLLM(["Label One\nSummary.", _QuotaError("Error code: 429")])
    build = _build(llm)
    assert llm.calls == 2
    assert build.llm_labels == 1
    assert build.fallback_labels == 2
    assert build.topics[0].label == "Label One"


def test_missing_keywords_fall_back_to_the_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_three_clusters(monkeypatch)
    monkeypatch.setattr(cluster_mod, "cluster_keywords", lambda docs, **k: {})
    build = _build(_CountingLLM([_QuotaError("Error code: 429")]))
    assert [topic.label for topic in build.topics] == ["Untitled topic"] * 3


def test_unit_centroid_is_normalized() -> None:
    centroid = unit_centroid([[2.0, 0.0], [0.0, 2.0]])
    assert sum(value * value for value in centroid) ** 0.5 == pytest.approx(1.0)
    assert centroid[0] == pytest.approx(centroid[1])
