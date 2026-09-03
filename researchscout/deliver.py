"""Outbound delivery: how a freshly published issue reaches readers who are not visiting.

The seam is the ``Deliverer`` protocol - web push is the first implementation, and an
email or webhook deliverer plugs in beside it without touching the scheduler. Delivery is
always best-effort from the caller's point of view: a publish must never fail because a
notification could not go out.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from researchscout.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Deliverer(Protocol):
    def deliver(self, *, title: str, body: str, url: str) -> int:
        """Send one notice to every recipient; returns how many went out."""
        ...


class NullDeliverer:
    """The off state: nothing configured, nothing sent, nothing logged."""

    def deliver(self, *, title: str, body: str, url: str) -> int:
        return 0


class WebPushDeliverer:
    """Send through the browsers' push services with the deployment's VAPID keys.

    Endpoints the service reports gone (404/410) are pruned as they fail, so the table
    tracks the browsers that still exist rather than every browser that ever subscribed.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def deliver(self, *, title: str, body: str, url: str) -> int:
        from pywebpush import WebPushException, webpush

        from researchscout.store.db import session_scope
        from researchscout.store.push import all_subscriptions, delete_endpoint

        payload = json.dumps({"title": title, "body": body, "url": url})
        with session_scope() as session:
            subscriptions: list[tuple[str, dict[str, Any]]] = [
                (row.endpoint, dict(row.keys)) for row in all_subscriptions(session)
            ]
        sent = 0
        gone: list[str] = []
        for endpoint, keys in subscriptions:
            try:
                webpush(
                    subscription_info={"endpoint": endpoint, "keys": keys},
                    data=payload,
                    vapid_private_key=self._settings.vapid_private_key,
                    vapid_claims={"sub": self._settings.vapid_subject},
                )
                sent += 1
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (404, 410):
                    gone.append(endpoint)
                else:
                    logger.warning("push to one endpoint failed (%s)", status)
            except Exception:  # noqa: BLE001 - one bad endpoint must not stop the rest
                logger.warning("push to one endpoint failed", exc_info=True)
        if gone:
            with session_scope() as session:
                for endpoint in gone:
                    delete_endpoint(session, endpoint)
            logger.info("pruned %d expired push endpoint(s)", len(gone))
        return sent


def get_deliverer(settings: Settings | None = None) -> Deliverer:
    """The configured deliverer: web push when enabled and keyed, the null one otherwise."""
    settings = settings or get_settings()
    if settings.push_enabled and settings.vapid_private_key and settings.vapid_public_key:
        return WebPushDeliverer(settings)
    return NullDeliverer()


def notify_new_issue(*, title: str, url: str, body: str = "Fresh reading is ready.") -> int:
    """Announce a freshly published digest or report; returns how many notices went out."""
    return get_deliverer().deliver(title=title, body=body, url=url)
