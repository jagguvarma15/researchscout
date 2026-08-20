"""The curated-signal auto-import lane: scope-checked, paced, fail-open."""

from contextlib import contextmanager
from typing import Any
from collections.abc import Iterator

import httpx
import pytest

import researchscout.ingest.autoimport as autoimport_mod
from researchscout.ingest.autoimport import land_unknown_papers


@contextmanager
def _no_session() -> Iterator[None]:
    yield None


def _payload(arxiv_id: str, categories: list[str]) -> dict[str, Any]:
    return {
        "id": f"http://arxiv.org/abs/{arxiv_id}v1",
        "title": f"Paper {arxiv_id}",
        "summary": "An abstract.",
        "authors": ["A. Author"],
        "categories": categories,
        "published": "2026-07-15T00:00:00Z",
        "links": [],
    }


@pytest.fixture(autouse=True)
def _fast_and_stubbed(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    landed: dict[str, list[str]] = {"landed": []}
    monkeypatch.setenv("RS_ARXIV_PAGE_DELAY_SEC", "0")
    monkeypatch.setattr(autoimport_mod, "session_scope", _no_session)

    def fake_land(session: Any, payload: dict[str, Any]) -> tuple[str, None, bool]:
        arxiv_id = payload["id"].rsplit("/", 1)[-1].removesuffix("v1")
        landed["landed"].append(arxiv_id)
        return f"arxiv:{arxiv_id}", None, False

    monkeypatch.setattr(autoimport_mod, "land_entry", fake_land)
    return landed


def test_in_scope_papers_land_and_out_of_scope_never_do(
    monkeypatch: pytest.MonkeyPatch, _fast_and_stubbed: dict[str, list[str]]
) -> None:
    payloads = {
        "2607.00001": _payload("2607.00001", ["cs.LG"]),
        "2607.00002": _payload("2607.00002", ["q-bio.NC"]),  # out of scope, dropped
        "2607.00003": None,  # unknown to arXiv, skipped
    }
    monkeypatch.setattr(autoimport_mod, "fetch_arxiv_entry", lambda aid: payloads[aid])

    landed = land_unknown_papers(["2607.00001", "2607.00002", "2607.00003"])
    assert landed == {"2607.00001": "arxiv:2607.00001"}
    assert _fast_and_stubbed["landed"] == ["2607.00001"]


def test_an_unreachable_arxiv_ends_the_run_quietly(
    monkeypatch: pytest.MonkeyPatch, _fast_and_stubbed: dict[str, list[str]]
) -> None:
    calls: list[str] = []

    def flaky(arxiv_id: str) -> dict[str, Any]:
        calls.append(arxiv_id)
        if arxiv_id == "2607.00002":
            raise httpx.ConnectError("down")
        return _payload(arxiv_id, ["cs.LG"])

    monkeypatch.setattr(autoimport_mod, "fetch_arxiv_entry", flaky)
    landed = land_unknown_papers(["2607.00001", "2607.00002", "2607.00003"])
    # The first id landed; the failure ended the run without reaching the third.
    assert list(landed) == ["2607.00001"]
    assert calls == ["2607.00001", "2607.00002"]


def test_the_per_run_bound_holds(
    monkeypatch: pytest.MonkeyPatch, _fast_and_stubbed: dict[str, list[str]]
) -> None:
    ids = [f"2607.{n:05d}" for n in range(1, 40)]
    monkeypatch.setattr(
        autoimport_mod, "fetch_arxiv_entry", lambda aid: _payload(aid, ["cs.LG"])
    )
    landed = land_unknown_papers(ids)
    assert len(landed) == 25  # _MAX_PER_RUN
