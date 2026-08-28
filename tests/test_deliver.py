"""The delivery seam: off is silent, on sends per subscription and prunes the dead."""

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from researchscout.config import get_settings
from researchscout.deliver import NullDeliverer, WebPushDeliverer, get_deliverer


def test_default_is_the_null_deliverer() -> None:
    deliverer = get_deliverer()
    assert isinstance(deliverer, NullDeliverer)
    assert deliverer.deliver(title="t", body="b", url="/digests") == 0


def test_keys_and_flag_pick_web_push(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_PUSH_ENABLED", "true")
    monkeypatch.setenv("RS_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RS_VAPID_PUBLIC_KEY", "pub")
    assert isinstance(get_deliverer(), WebPushDeliverer)

    # The flag without keys stays off: half-configured push must not half-run.
    monkeypatch.setenv("RS_VAPID_PRIVATE_KEY", "")
    get_settings.cache_clear()
    assert isinstance(get_deliverer(), NullDeliverer)


@pytest.mark.integration
def test_web_push_sends_and_prunes_gone_endpoints(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pywebpush

    from researchscout.store.push import all_subscriptions, save_subscription
    from researchscout.store.users import upsert_user

    upsert_user(session, "user-1")
    save_subscription(session, "user-1", "https://push.example/alive", {"p256dh": "a", "auth": "b"})
    save_subscription(session, "user-1", "https://push.example/gone", {"p256dh": "c", "auth": "d"})
    session.commit()

    sent: list[str] = []

    def fake_webpush(*, subscription_info: dict, **kwargs: object) -> None:
        endpoint = subscription_info["endpoint"]
        if endpoint.endswith("/gone"):
            raise pywebpush.WebPushException("gone", response=SimpleNamespace(status_code=410))
        sent.append(endpoint)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    monkeypatch.setenv("RS_PUSH_ENABLED", "true")
    monkeypatch.setenv("RS_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RS_VAPID_PUBLIC_KEY", "pub")
    get_settings.cache_clear()

    delivered = WebPushDeliverer(get_settings()).deliver(
        title="Digest", body="Fresh reading is ready.", url="/digests/2026-w35"
    )
    assert delivered == 1
    assert sent == ["https://push.example/alive"]
    remaining = [row.endpoint for row in all_subscriptions(session)]
    assert remaining == ["https://push.example/alive"]
