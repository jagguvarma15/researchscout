"""The LLM usage seam: purpose tagging, best-effort recording, and the daily rollup."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import researchscout.llm.usage as usage_mod
from researchscout.llm.usage import (
    PURPOSE_SYNTHESIS,
    LlmCallUsage,
    current_purpose,
    last_usage,
    llm_purpose,
    record_usage,
)


def _call(**overrides: object) -> LlmCallUsage:
    fields: dict[str, object] = {
        "purpose": "synthesis",
        "model": "m",
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "latency_ms": 500,
        "outcome": "ok",
        "detail": None,
    }
    fields.update(overrides)
    return LlmCallUsage(**fields)  # type: ignore[arg-type]


def test_the_purpose_travels_only_inside_the_block() -> None:
    assert current_purpose() == "other"
    with llm_purpose(PURPOSE_SYNTHESIS):
        assert current_purpose() == "synthesis"
        with llm_purpose("guardrail"):
            assert current_purpose() == "guardrail"
        assert current_purpose() == "synthesis"
    assert current_purpose() == "other"


def test_the_purpose_resets_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError), llm_purpose("digest"):
        raise RuntimeError("boom")
    assert current_purpose() == "other"


def test_record_usage_swallows_a_dead_ledger_but_keeps_last_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metrics failure must never surface into an answer; the in-context copy survives."""

    def broken_scope() -> None:
        raise RuntimeError("no database here")

    monkeypatch.setattr("researchscout.store.db.session_scope", broken_scope)
    call = _call()
    record_usage(call)
    assert last_usage() is call


def test_record_usage_appends_via_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[LlmCallUsage] = []

    class _Scope:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("researchscout.store.db.session_scope", lambda: _Scope())
    monkeypatch.setattr(
        "researchscout.store.llm_usage.add_usage",
        lambda session, usage: recorded.append(usage),
    )
    call = _call(outcome="quota", detail="RateLimitError(...)")
    record_usage(call)
    assert recorded == [call]
    assert last_usage() is call


# --- DB-backed rollup and retention (integration: needs the pgvector container) ---


@pytest.mark.integration
def test_add_usage_lands_a_row_with_caps(session: Session) -> None:
    from researchscout.store.llm_usage import add_usage
    from researchscout.store.models import LlmUsageRow

    add_usage(session, _call(purpose="p" * 60, model="m" * 200, detail="d" * 500))
    row = session.execute(select(LlmUsageRow)).scalar_one()
    assert len(row.purpose) == 30
    assert len(row.model) == 120
    assert row.detail is not None and len(row.detail) == 200
    assert row.prompt_tokens == 100 and row.completion_tokens == 20
    assert row.outcome == "ok"


@pytest.mark.integration
def test_usage_summary_rolls_up_the_utc_day(session: Session) -> None:
    from researchscout.store.llm_usage import add_usage, usage_summary
    from researchscout.store.models import LlmUsageRow

    add_usage(session, _call(purpose="synthesis"))
    add_usage(session, _call(purpose="synthesis", outcome="quota", detail="429"))
    add_usage(session, _call(purpose="guardrail", prompt_tokens=10, completion_tokens=1))
    add_usage(session, _call(purpose="guardrail", outcome="error", detail="boom"))
    # An old row must not count toward today.
    add_usage(session, _call(purpose="digest"))
    session.flush()
    session.execute(
        update(LlmUsageRow)
        .where(LlmUsageRow.purpose == "digest")
        .values(called_at=datetime.now(UTC) - timedelta(days=2))
    )

    summary = usage_summary(session)
    assert summary.calls_today == 4
    assert summary.prompt_tokens_today == 310
    assert summary.completion_tokens_today == 61
    by_name = {entry.purpose: entry for entry in summary.by_purpose}
    assert set(by_name) == {"synthesis", "guardrail"}
    assert by_name["synthesis"].calls == 2
    assert by_name["synthesis"].ok == 1 and by_name["synthesis"].quota == 1
    assert by_name["guardrail"].errors == 1
    assert summary.last_quota_at is not None


@pytest.mark.integration
def test_usage_summary_without_rows_is_empty(session: Session) -> None:
    from researchscout.store.llm_usage import usage_summary

    summary = usage_summary(session)
    assert summary.calls_today == 0
    assert summary.by_purpose == []
    assert summary.last_quota_at is None


@pytest.mark.integration
def test_prune_llm_usage_drops_only_old_rows(session: Session) -> None:
    from researchscout.store.llm_usage import add_usage, prune_llm_usage
    from researchscout.store.models import LlmUsageRow

    add_usage(session, _call(purpose="old"))
    add_usage(session, _call(purpose="new"))
    session.flush()
    session.execute(
        update(LlmUsageRow)
        .where(LlmUsageRow.purpose == "old")
        .values(called_at=datetime.now(UTC) - timedelta(days=120))
    )

    prune_llm_usage(session, keep_days=90)
    kept = session.execute(select(LlmUsageRow.purpose)).scalars().all()
    assert kept == ["new"]


def test_usage_module_exports_the_shared_vocabulary() -> None:
    """The ledger purposes and the LangSmith tags are one vocabulary, defined once."""
    names = [name for name in vars(usage_mod) if name.startswith("PURPOSE_")]
    values = {getattr(usage_mod, name) for name in names}
    assert len(values) == len(names) == 7
