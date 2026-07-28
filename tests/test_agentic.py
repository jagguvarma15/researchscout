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

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        return self._reply


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._body


def _scored(pid: str, score: float) -> ScoredPaper:
    paper = Paper(
        id=pid,
        title=pid,
        abstract="a",
        authors=[Author(name="X")],
        categories=["cs.LG"],
        published_at=NOW,
        source="arxiv",
    )
    return ScoredPaper(paper=paper, score=score, distance=0.0)


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
