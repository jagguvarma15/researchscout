from researchscout.cluster import cluster_labels, label_topic
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
