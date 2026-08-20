"""Error reporting seam: Sentry when a DSN is configured, silence otherwise.

Every function here is safe to call unconditionally. Without a DSN - or without the
sentry-sdk package installed - each one is a no-op, so the disabled path stays identical
to a deployment that has never heard of Sentry, the same contract every optional
integration in this codebase keeps. FastAPI request errors are captured by the SDK's own
default integrations once init runs; the explicit capture calls exist for the scheduler
loop, whose failures are swallowed by design and would otherwise never leave the log.
"""

from __future__ import annotations

import logging
import os

from researchscout.config import Settings

logger = logging.getLogger(__name__)

_initialized = False


def init_observability(settings: Settings) -> None:
    """Start Sentry when a DSN is configured; stay silent when it is not."""
    global _initialized
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("a Sentry DSN is set but sentry-sdk is not installed; reporting off")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        release=settings.build_sha or None,
        environment="production" if os.environ.get("RAILWAY_ENVIRONMENT_NAME") else "development",
        # Errors only: the free tier's monthly event budget is for tracebacks, not spans.
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    _initialized = True
    logger.info("sentry: error reporting on (release %s)", settings.build_sha[:12] or "unstamped")


def capture_exception(exc: BaseException) -> None:
    """Report one exception; a no-op when observability never initialized."""
    if not _initialized:
        return
    import sentry_sdk

    sentry_sdk.capture_exception(exc)


def flush(timeout: float = 2.0) -> None:
    """Drain the transport queue - required before a hard process exit, which skips the
    atexit hook the SDK normally flushes from."""
    if not _initialized:
        return
    import sentry_sdk

    sentry_sdk.flush(timeout=timeout)
