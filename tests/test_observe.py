"""The error-reporting seam: everything is a safe no-op until a DSN initializes it."""

import pytest

import researchscout.observe as observe_mod
from researchscout.config import Settings
from researchscout.observe import capture_exception, flush, init_observability


def test_everything_is_a_no_op_without_a_dsn() -> None:
    init_observability(Settings(_env_file=None))
    assert observe_mod._initialized is False
    capture_exception(RuntimeError("nobody is listening"))  # must not raise
    flush()


def test_init_starts_sentry_when_the_dsn_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    seen: dict[str, object] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(observe_mod, "_initialized", False)
    monkeypatch.setenv("RS_SENTRY_DSN", "https://key@o1.ingest.us.sentry.io/1")
    monkeypatch.setenv("RS_BUILD_SHA", "abc123def456")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)

    init_observability(Settings())

    assert observe_mod._initialized is True
    assert seen["dsn"] == "https://key@o1.ingest.us.sentry.io/1"
    assert seen["release"] == "abc123def456"
    assert seen["environment"] == "development"
    assert seen["traces_sample_rate"] == 0.0  # errors only: the free tier is for tracebacks
    assert seen["send_default_pii"] is False


def test_capture_and_flush_reach_the_sdk_once_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sentry_sdk

    calls: list[str] = []
    monkeypatch.setattr(observe_mod, "_initialized", True)
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: calls.append("capture"))
    monkeypatch.setattr(sentry_sdk, "flush", lambda timeout: calls.append(f"flush:{timeout}"))

    capture_exception(RuntimeError("boom"))
    flush(timeout=2.0)
    assert calls == ["capture", "flush:2.0"]
