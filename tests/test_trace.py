"""Unit tests for the tracing seam: span records and process log configuration."""

import logging

import pytest

from researchscout.trace import configure_logging, trace_span


def test_trace_span_logs_fields_and_elapsed(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="researchscout.trace"):
        with trace_span("unit", a=1) as span:
            span["b"] = 2
    message = caplog.records[-1].getMessage()
    assert "span unit" in message
    assert "'a': 1" in message
    assert "'b': 2" in message
    assert "elapsed_ms" in message


def test_configure_logging_raises_the_app_tree_and_is_idempotent() -> None:
    root = logging.getLogger()
    app = logging.getLogger("researchscout")
    old_root, old_app = root.level, app.level
    handlers_before = list(root.handlers)
    try:
        configure_logging()
        assert app.level == logging.INFO
        added = [h for h in root.handlers if h not in handlers_before]
        assert len(added) <= 1  # basicConfig no-ops when a handler already exists
        configure_logging()
        added_again = [h for h in root.handlers if h not in handlers_before]
        assert len(added_again) == len(added)  # re-entry never duplicates handlers
    finally:
        root.setLevel(old_root)
        app.setLevel(old_app)
        for handler in [h for h in root.handlers if h not in handlers_before]:
            root.removeHandler(handler)


def test_configure_logging_accepts_a_custom_level() -> None:
    app = logging.getLogger("researchscout")
    old_app = app.level
    try:
        configure_logging(logging.DEBUG)
        assert app.level == logging.DEBUG
    finally:
        app.setLevel(old_app)
