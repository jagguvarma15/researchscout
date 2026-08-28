"""Web push subscriptions, behind RS_PUSH_ENABLED.

Off (the default) both routes 404 and nothing about the deployment changes; the settings
toggle in the web app reads that as "not offered here". The public VAPID key rides the
subscribe route's 404-vs-200 answer: the client needs it to subscribe, and serving it from
the same flag keeps one switch honest.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import PushSubscriptionBody, PushUnsubscribeBody
from researchscout.config import get_settings
from researchscout.store.push import delete_subscription, save_subscription

router = APIRouter(tags=["push"])


def _require_enabled() -> None:
    settings = get_settings()
    if not (settings.push_enabled and settings.vapid_public_key):
        raise HTTPException(status_code=404, detail="push delivery is not enabled")


@router.get("/me/push-key")
def push_key(
    user: Annotated[User, Depends(require_user)],
) -> dict[str, str]:
    """The deployment's public VAPID key - what the browser subscribes against."""
    _require_enabled()
    return {"key": get_settings().vapid_public_key}


@router.post("/me/push-subscriptions")
def subscribe(
    body: PushSubscriptionBody,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Store this browser's subscription (idempotent per endpoint)."""
    _require_enabled()
    save_subscription(
        session, user.sub, body.endpoint, {"p256dh": body.keys.p256dh, "auth": body.keys.auth}
    )
    return {"subscribed": True}


@router.delete("/me/push-subscriptions")
def unsubscribe(
    body: PushUnsubscribeBody,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Forget this browser's subscription (idempotent)."""
    _require_enabled()
    delete_subscription(session, user.sub, body.endpoint)
    return {"subscribed": False}
