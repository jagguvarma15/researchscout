"""The LangSmith pipeline seam: strict no-op when off, real run trees when on."""

from typing import Any

import pytest

from researchscout.llm.tracing import NOOP_RUN, pipeline_run


def test_pipeline_run_is_a_noop_without_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert pipeline_run("ask") is NOOP_RUN
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert pipeline_run("ask") is NOOP_RUN


def test_the_noop_handle_supports_the_whole_surface() -> None:
    run = NOOP_RUN
    with run.step("decompose", inputs={"question": "q"}) as step:
        assert step is NOOP_RUN
        with step.ambient():
            pass
        step.out(parts=["a"])
    run.out(retrieved=3)
    run.end(outputs={"outcome": "ok"})


def test_a_noop_step_reraises_but_stays_silent() -> None:
    with pytest.raises(ValueError), NOOP_RUN.step("retrieve"):
        raise ValueError("boom")


class _FakeRunTree:
    """Stands in for langsmith.run_trees.RunTree; records the lifecycle calls."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.children: list[_FakeRunTree] = []
        self.posted = False
        self.patched = False
        self.ended: dict[str, Any] | None = None

    def create_child(self, **kwargs: Any) -> "_FakeRunTree":
        child = _FakeRunTree(**kwargs)
        self.children.append(child)
        return child

    def post(self) -> None:
        self.posted = True

    def patch(self) -> None:
        self.patched = True

    def end(self, *, outputs: Any = None, error: Any = None) -> None:
        self.ended = {"outputs": outputs, "error": error}


@pytest.fixture
def fake_run_tree(monkeypatch: pytest.MonkeyPatch) -> type[_FakeRunTree]:
    run_trees = pytest.importorskip("langsmith.run_trees")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(run_trees, "RunTree", _FakeRunTree)
    return _FakeRunTree


def test_pipeline_run_opens_a_posted_parent(fake_run_tree: type[_FakeRunTree]) -> None:
    handle = pipeline_run(
        "ask", inputs={"question": "q"}, tags=["chat", "agentic"], metadata={"mode": "llm"}
    )
    assert handle is not NOOP_RUN
    run = handle._run
    assert isinstance(run, _FakeRunTree)
    assert run.posted
    assert run.kwargs["name"] == "ask"
    assert run.kwargs["tags"] == ["chat", "agentic"]
    assert run.kwargs["extra"] == {"metadata": {"mode": "llm"}}


def test_steps_nest_and_carry_staged_outputs(fake_run_tree: type[_FakeRunTree]) -> None:
    handle = pipeline_run("ask")
    with handle.step("decompose", inputs={"question": "q"}) as step:
        step.out(parts=["a", "b"])
    parent = handle._run
    assert isinstance(parent, _FakeRunTree)
    child = parent.children[0]
    assert child.kwargs["name"] == "decompose"
    assert child.posted and child.patched
    assert child.ended == {"outputs": {"parts": ["a", "b"]}, "error": None}


def test_a_failing_step_closes_as_an_error_and_reraises(
    fake_run_tree: type[_FakeRunTree],
) -> None:
    handle = pipeline_run("ask")
    with pytest.raises(ValueError), handle.step("retrieve"):
        raise ValueError("boom")
    parent = handle._run
    assert isinstance(parent, _FakeRunTree)
    child = parent.children[0]
    assert child.ended is not None and child.ended["error"] is not None
    assert child.patched


def test_end_merges_staged_and_final_outputs(fake_run_tree: type[_FakeRunTree]) -> None:
    handle = pipeline_run("ask")
    handle.out(retrieved=5)
    handle.end(outputs={"outcome": "ok"})
    run = handle._run
    assert isinstance(run, _FakeRunTree)
    assert run.ended == {"outputs": {"retrieved": 5, "outcome": "ok"}, "error": None}
    assert run.patched


def test_a_broken_run_tree_degrades_to_the_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    run_trees = pytest.importorskip("langsmith.run_trees")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    def boom(**kwargs: Any) -> Any:
        raise RuntimeError("no api key")

    monkeypatch.setattr(run_trees, "RunTree", boom)
    assert pipeline_run("ask") is NOOP_RUN


def test_ambient_wraps_with_the_tracing_context(
    fake_run_tree: type[_FakeRunTree], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_helpers = pytest.importorskip("langsmith.run_helpers")
    seen: list[Any] = []

    class _Ctx:
        def __init__(self, *, parent: Any) -> None:
            seen.append(parent)

        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(run_helpers, "tracing_context", _Ctx)
    handle = pipeline_run("ask")
    with handle.ambient():
        pass
    assert seen == [handle._run]
