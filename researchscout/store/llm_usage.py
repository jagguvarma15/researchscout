"""The LLM usage ledger: append per-call rows, roll up the day, prune the history.

"Today" is the UTC day, on purpose: the provider's own quota day is close enough, and a
deterministic boundary beats a clever one — the summary answers "what has spent the
budget since midnight", not "exactly when the provider resets".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from researchscout.llm.usage import LlmCallUsage
from researchscout.store.models import LlmUsageRow

# How much history stays: enough to see a season of spend, not a log store.
_KEEP_DAYS = 90
_QUOTA_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class PurposeCalls:
    """One purpose's share of today: how many calls, how they ended, what they cost."""

    purpose: str
    calls: int
    ok: int
    quota: int
    errors: int
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class UsageSummary:
    """Today's model spend, plus the last time a call died on quota (7-day lookback)."""

    calls_today: int
    prompt_tokens_today: int
    completion_tokens_today: int
    by_purpose: list[PurposeCalls]
    last_quota_at: datetime | None


def add_usage(session: Session, usage: LlmCallUsage) -> None:
    """Append one call to the ledger."""
    session.add(
        LlmUsageRow(
            purpose=usage.purpose[:30],
            model=usage.model[:120],
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            latency_ms=usage.latency_ms,
            outcome=usage.outcome[:10],
            detail=usage.detail[:200] if usage.detail else None,
        )
    )


def usage_summary(session: Session) -> UsageSummary:
    """Roll up the UTC day by purpose and outcome, newest-heaviest purposes first."""
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.execute(
        select(
            LlmUsageRow.purpose,
            LlmUsageRow.outcome,
            func.count(LlmUsageRow.id),
            func.coalesce(func.sum(LlmUsageRow.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageRow.completion_tokens), 0),
        )
        .where(LlmUsageRow.called_at >= midnight)
        .group_by(LlmUsageRow.purpose, LlmUsageRow.outcome)
    ).all()

    buckets: dict[str, dict[str, int]] = {}
    for purpose, outcome, calls, prompt, completion in rows:
        bucket = buckets.setdefault(
            purpose, {"calls": 0, "ok": 0, "quota": 0, "errors": 0, "prompt": 0, "completion": 0}
        )
        bucket["calls"] += calls
        bucket["prompt"] += prompt
        bucket["completion"] += completion
        if outcome == "ok":
            bucket["ok"] += calls
        elif outcome == "quota":
            bucket["quota"] += calls
        elif outcome == "error":
            bucket["errors"] += calls

    by_purpose = sorted(
        (
            PurposeCalls(
                purpose=purpose,
                calls=bucket["calls"],
                ok=bucket["ok"],
                quota=bucket["quota"],
                errors=bucket["errors"],
                prompt_tokens=bucket["prompt"],
                completion_tokens=bucket["completion"],
            )
            for purpose, bucket in buckets.items()
        ),
        key=lambda entry: (-entry.calls, entry.purpose),
    )
    last_quota_at = session.execute(
        select(func.max(LlmUsageRow.called_at)).where(
            LlmUsageRow.outcome == "quota",
            LlmUsageRow.called_at >= now - timedelta(days=_QUOTA_LOOKBACK_DAYS),
        )
    ).scalar_one_or_none()
    return UsageSummary(
        calls_today=sum(entry.calls for entry in by_purpose),
        prompt_tokens_today=sum(entry.prompt_tokens for entry in by_purpose),
        completion_tokens_today=sum(entry.completion_tokens for entry in by_purpose),
        by_purpose=by_purpose,
        last_quota_at=last_quota_at,
    )


def prune_llm_usage(session: Session, *, keep_days: int = _KEEP_DAYS) -> None:
    """Drop ledger rows older than the retention window."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    session.execute(delete(LlmUsageRow).where(LlmUsageRow.called_at < cutoff))
