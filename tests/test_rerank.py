from researchscout.rerank import Candidate, CrossEncoderReranker, Reranker, get_reranker, rerank


class StubReranker(Reranker):
    def __init__(self, relevance: dict[str, float]) -> None:
        self.relevance = relevance
        self.calls: list[tuple[str, list[str]]] = []

    def scores(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        return [self.relevance[doc] for doc in documents]


def _cand(key: str, *, prior: float, first_stage: float, text: str | None = None) -> Candidate:
    return Candidate(key=key, text=text or key, prior=prior, first_stage=first_stage)


def test_no_reranker_is_passthrough() -> None:
    cands = [_cand("a", prior=1.0, first_stage=0.3), _cand("b", prior=1.0, first_stage=0.9)]
    assert rerank("q", cands, None, top_n=10) == [("b", 0.9), ("a", 0.3)]


def test_top_n_truncates_before_reranking() -> None:
    cands = [_cand(str(i), prior=1.0, first_stage=float(i)) for i in range(5)]
    stub = StubReranker({"0": 0.9, "1": 0.9, "2": 0.9, "3": 0.9, "4": 0.9})
    rerank("q", cands, stub, top_n=2)
    # Only the two highest first-stage candidates are handed to the cross encoder.
    assert stub.calls == [("q", ["4", "3"])]


def test_relevance_reorders_within_the_selected_set() -> None:
    cands = [
        _cand("a", prior=1.0, first_stage=0.9, text="ta"),  # stronger first stage
        _cand("b", prior=1.0, first_stage=0.3, text="tb"),
    ]
    stub = StubReranker({"ta": 0.1, "tb": 0.95})  # but b is far more relevant
    out = rerank("q", cands, stub, top_n=10)
    assert [key for key, _ in out] == ["b", "a"]


def test_prior_breaks_ties_in_relevance() -> None:
    cands = [
        _cand("fresh", prior=2.0, first_stage=0.5, text="tf"),
        _cand("stale", prior=1.0, first_stage=0.5, text="ts"),
    ]
    stub = StubReranker({"tf": 0.5, "ts": 0.5})  # equally relevant
    out = rerank("q", cands, stub, top_n=10)
    assert [key for key, _ in out] == ["fresh", "stale"]  # recency/momentum prior wins


def test_cross_encoder_empty_documents_needs_no_model() -> None:
    assert CrossEncoderReranker("any-model").scores("q", []) == []


def test_get_reranker_disabled_by_default() -> None:
    assert get_reranker() is None
