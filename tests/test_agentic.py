from datetime import UTC, datetime

import httpx
import pytest

from researchscout.agentic import _merge, _reference_arxiv_ids, decompose
from researchscout.llm.base import LLM
from researchscout.retrieve.search import ScoredPaper
from researchscout.schema import Author, Paper

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


def test_reference_arxiv_ids_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> _Resp:
        raise httpx.HTTPError("nope")

    monkeypatch.setattr(httpx, "get", boom)
    assert _reference_arxiv_ids("2401.00001", limit=10) == []
