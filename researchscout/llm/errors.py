"""Classify rate-limit and quota failures without importing any provider SDK.

Two evidence forms exist because callers hold different things: live code holds the
exception object (an OpenAI-compatible client carries ``status_code`` on it), while the
health streak check only sees the note string a scheduler ledger row kept. The strict
status form is separate so retry decisions never match a wrapped error whose message
merely mentions rate limiting — a source task throttled per-minute is exactly what a
delayed retry is for, unlike a spent daily quota.
"""

from __future__ import annotations

import re

_QUOTA_NOTE_RE = re.compile(r"rate.?limit|quota|\b429\b", re.IGNORECASE)


def is_quota_note(note: str) -> bool:
    """Does a ledger note string read like a rate-limit or quota failure?"""
    return bool(_QUOTA_NOTE_RE.search(note))


def is_quota_status(exc: BaseException) -> bool:
    """Strict form: an HTTP 429 carried on the exception itself."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status == 429


def is_quota_error(exc: BaseException) -> bool:
    """Broad form for direct LLM call sites: the status or the message shape."""
    return is_quota_status(exc) or is_quota_note(str(exc))
