"""The quota classifier: exception status vs note-string evidence, strict vs broad."""

from researchscout.llm.errors import is_quota_error, is_quota_note, is_quota_status


class _StatusError(Exception):
    status_code = 429


def test_a_429_status_passes_both_forms() -> None:
    exc = _StatusError("Rate limit exceeded")
    assert is_quota_status(exc)
    assert is_quota_error(exc)


def test_the_openrouter_daily_cap_note_matches() -> None:
    note = "Error code: 429 - Rate limit exceeded: free-models-per-day"
    assert is_quota_note(note)


def test_unrelated_notes_do_not_match() -> None:
    assert not is_quota_note("llm down")
    assert not is_quota_note("upstream down")


def test_a_wrapped_message_is_broad_but_not_strict() -> None:
    exc = RuntimeError("failed (429 Too Many Requests)")
    assert is_quota_error(exc)
    assert not is_quota_status(exc)


def test_a_non_integer_status_never_matches_the_strict_form() -> None:
    exc = RuntimeError("boom")
    exc.status_code = "429"  # type: ignore[attr-defined]
    assert not is_quota_status(exc)
