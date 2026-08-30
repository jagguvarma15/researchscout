from datetime import UTC, datetime

import httpx
import pytest

import researchscout.agentic as agentic_mod
from researchscout.agentic import _fuse, _merge, _reference_arxiv_ids, agentic_retrieve, decompose
from researchscout.llm.base import LLM
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper
from researchscout.store.facets import PaperFacets

NOW = datetime(2024, 6, 1, tzinfo=UTC)


class _FakeLLM(LLM):
    model = "fake"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.purpose_seen: str | None = None

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        from researchscout.llm.usage import current_purpose

        self.purpose_seen = current_purpose()
        return self._reply


class _BrokenLLM(LLM):
    model = "fake"

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        raise RuntimeError("model unavailable")


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._body


def _scored(
    pid: str,
    score: float,
    *,
    distance: float = 0.0,
    relevance: float | None = None,
    prior: float = 1.0,
) -> ScoredPaper:
    paper = Paper(
        id=pid,
        title=pid,
        abstract="a",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=NOW,
        source="arxiv",
    )
    return ScoredPaper(
        paper=paper, score=score, distance=distance, relevance=relevance, prior=prior
    )


def test_decompose_strips_list_markers() -> None:
    llm = _FakeLLM("1. sparse attention\n- long context\n* KV cache")
    assert decompose(llm, "efficient transformers") == [
        "sparse attention",
        "long context",
        "KV cache",
    ]


def test_decompose_keeps_real_leading_digits() -> None:
    assert decompose(_FakeLLM("3D reconstruction"), "q") == ["3D reconstruction"]


def test_decompose_dedups_case_insensitively() -> None:
    assert decompose(_FakeLLM("Diffusion\ndiffusion\nsampling"), "q") == ["Diffusion", "sampling"]


def test_decompose_caps_parts() -> None:
    reply = "\n".join(f"q{i}" for i in range(10))
    assert len(decompose(_FakeLLM(reply), "q", max_parts=4)) == 4


def test_decompose_falls_back_to_question() -> None:
    assert decompose(_FakeLLM("   "), "my question") == ["my question"]


def test_decompose_survives_a_broken_model() -> None:
    """A failed decomposition degrades a deep ask to a single-shot one, never to an error."""
    assert decompose(_BrokenLLM(), "my question") == ["my question"]


def test_decompose_tags_its_call_with_the_purpose() -> None:
    llm = _FakeLLM("a\nb")
    decompose(llm, "q")
    assert llm.purpose_seen == "decompose"


def test_merge_dedups_keeping_highest_score() -> None:
    merged = _merge([[_scored("arxiv:1", 0.5), _scored("arxiv:2", 0.7)], [_scored("arxiv:1", 0.9)]])
    assert [(item.paper.id, item.score) for item in merged] == [("arxiv:1", 0.9), ("arxiv:2", 0.7)]


def test_fuse_prefers_papers_found_by_multiple_subquestions() -> None:
    # arxiv:2 is rank 2 then rank 1 (1/62 + 1/61); arxiv:1 is a single rank 1 (1/61).
    fused = _fuse([[_scored("arxiv:1", 0.9), _scored("arxiv:2", 0.8)], [_scored("arxiv:2", 0.7)]])
    assert [item.paper.id for item in fused] == ["arxiv:2", "arxiv:1"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_fuse_keeps_the_best_measured_distance() -> None:
    near = _scored("arxiv:1", 0.5)
    far = ScoredPaper(paper=near.paper, score=0.4, distance=1.0)
    fused = _fuse([[far], [near]])
    assert fused[0].distance == 0.0


def test_fuse_preserves_the_recency_breakthrough_prior() -> None:
    """Fused scores stay on the single-shot scale: rrf x prior, not a bare rank sum."""
    hot = _scored("arxiv:hot", 0.9, prior=3.0)  # fresh, high momentum
    cold = _scored("arxiv:cold", 0.8, prior=0.1)  # old, quiet
    fused = _fuse([[cold, hot]])
    by_id = {item.paper.id: item for item in fused}
    assert by_id["arxiv:hot"].score == pytest.approx((1 / 62) * 3.0)
    assert by_id["arxiv:cold"].score == pytest.approx((1 / 61) * 0.1)
    # The prior outweighs one rank step, exactly as it does in single-shot retrieval.
    assert fused[0].paper.id == "arxiv:hot"
    assert by_id["arxiv:hot"].prior == 3.0


def test_fuse_reranked_hits_stay_on_the_relevance_scale() -> None:
    item = _scored("arxiv:1", 0.5, relevance=0.8, prior=0.5)
    fused = _fuse([[item], [item]])
    rrf = 1 / 61 + 1 / 61
    assert fused[0].score == pytest.approx(0.8 * 0.5 * (1.0 + rrf))
    assert fused[0].relevance == 0.8


def test_fuse_carries_the_best_relevance_across_appearances() -> None:
    close_but_weak = _scored("arxiv:1", 0.5, distance=0.1, relevance=0.3)
    far_but_strong = _scored("arxiv:1", 0.4, distance=0.6, relevance=0.9)
    fused = _fuse([[close_but_weak], [far_but_strong]])
    # Representative distance is the closest appearance; relevance is the best one.
    assert fused[0].distance == 0.1
    assert fused[0].relevance == 0.9


def test_agentic_retrieve_fuses_and_threads_facets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    hits = {
        "a": [_scored("arxiv:1", 0.9), _scored("arxiv:2", 0.8)],
        "b": [_scored("arxiv:2", 0.7)],
    }

    def fake_retrieve(session: object, embedder: object, part: str, **kwargs: object) -> list:
        calls.append({"part": part, **kwargs})
        return hits[part]

    monkeypatch.setattr(agentic_mod, "retrieve", fake_retrieve)
    facets = PaperFacets(categories=["cs.LG"])
    results = agentic_retrieve(
        None, None, _FakeLLM("a\nb"), "q", k=5, days=30, facets=facets, follow_citations=False
    )
    # The old max-score merge would put arxiv:1 (0.9) first; RRF prefers the double hit.
    assert [item.paper.id for item in results] == ["arxiv:2", "arxiv:1"]
    assert [call["part"] for call in calls] == ["a", "b"]
    assert all(call["facets"] is facets for call in calls)


def test_reference_arxiv_ids_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {"citedPaper": {"externalIds": {"ArXiv": "2301.00001"}}},
            {"citedPaper": {"externalIds": {"DOI": "10.x"}}},  # no arXiv id
            {"citedPaper": None},  # missing paper
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, payload))
    assert _reference_arxiv_ids("2401.00001", limit=10) == ["2301.00001"]


def test_reference_arxiv_ids_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> _Resp:
        raise httpx.HTTPError("nope")

    monkeypatch.setattr(httpx, "get", boom)
    # None, not [] - a transient failure must never be cached as "no references".
    assert _reference_arxiv_ids("2401.00001", limit=10) is None


def test_reference_arxiv_ids_survives_a_shape_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """An upstream payload drift must degrade the hop, never crash the ask."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, {"data": ["not-a-dict"]}))
    assert _reference_arxiv_ids("2401.00001", limit=10) is None
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, "garbage"))
    assert _reference_arxiv_ids("2401.00001", limit=10) is None


def test_the_hop_timeout_is_bounded() -> None:
    """The hop runs inside the request and the held generation slot; 8s per source, 10s total."""
    assert agentic_mod._REQUEST_TIMEOUT == 8.0
    assert agentic_mod._HOP_BUDGET_SEC == 10.0


def _hop_paper(pid: str, arxiv: str | None) -> ScoredPaper:
    item = _scored(pid, 0.5)
    if arxiv is not None:
        item.paper.external_ids["arxiv"] = arxiv
    return item


def test_follow_references_spent_budget_skips_fetches_but_merges_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import researchscout.store.citations as citations_mod
    import researchscout.store.papers as papers_mod
    from researchscout.agentic import follow_references

    cached = {"arxiv:cached": ["2301.00001"]}
    monkeypatch.setattr(citations_mod, "references_cached", lambda session, pid: cached.get(pid))
    fetches: list[str] = []

    def fake_fetch(arxiv_id: str, *, limit: int) -> list[str]:
        fetches.append(arxiv_id)
        return []

    monkeypatch.setattr(agentic_mod, "_reference_arxiv_ids", fake_fetch)
    monkeypatch.setattr(
        papers_mod, "find_by_external_id", lambda session, kind, value: f"arxiv:{value}"
    )
    ref_paper = _scored("arxiv:2301.00001", 0.0).paper
    monkeypatch.setattr(papers_mod, "get_paper", lambda session, pid: ref_paper)
    # A spent budget (elapsed > -1 immediately) must skip every uncached fetch while the
    # cached source still contributes its edges.
    monkeypatch.setattr(agentic_mod, "_HOP_BUDGET_SEC", -1.0)

    added = follow_references(
        None,  # type: ignore[arg-type] - every session read is monkeypatched
        [_hop_paper("arxiv:cached", "2401.00001"), _hop_paper("arxiv:uncached", "2401.00002")],
    )
    assert fetches == []
    assert [item.paper.id for item in added] == ["arxiv:2301.00001"]
    assert added[0].score == 0.0


def test_agentic_retrieve_accepts_precomputed_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        agentic_mod,
        "retrieve",
        lambda session, embedder, part, **kwargs: calls.append(part) or [_scored("arxiv:1", 0.9)],
    )
    results = agentic_retrieve(
        None,
        None,
        _BrokenLLM(),  # decompose must not be consulted when parts are given
        "q",
        parts=["alpha", "beta"],
        follow_citations=False,
    )
    assert calls == ["alpha", "beta"]
    assert results and results[0].paper.id == "arxiv:1"
