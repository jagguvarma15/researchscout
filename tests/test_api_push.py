"""The push subscription routes: off is 404, on stores per endpoint per account."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.store.users import upsert_user

pytestmark = pytest.mark.integration

_SUB = {
    "endpoint": "https://push.example/one",
    "keys": {"p256dh": "public-bytes", "auth": "auth-bytes"},
}


def _client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_user] = lambda: User(sub="user-1", username="demo")
    return TestClient(app)


def _enable(set_setting: Callable[[str, str], None]) -> None:
    set_setting("RS_PUSH_ENABLED", "true")
    set_setting("RS_VAPID_PRIVATE_KEY", "priv")
    set_setting("RS_VAPID_PUBLIC_KEY", "pub")


def test_flag_off_every_route_404(session: Session) -> None:
    client = _client(session)
    assert client.get("/v1/me/push-key").status_code == 404
    assert client.post("/v1/me/push-subscriptions", json=_SUB).status_code == 404
    assert (
        client.request(
            "DELETE", "/v1/me/push-subscriptions", json={"endpoint": _SUB["endpoint"]}
        ).status_code
        == 404
    )


def test_subscribe_round_trip(session: Session, set_setting: Callable[[str, str], None]) -> None:
    from researchscout.store.push import all_subscriptions

    upsert_user(session, "user-1")
    session.commit()
    _enable(set_setting)
    client = _client(session)

    assert client.get("/v1/me/push-key").json() == {"key": "pub"}
    assert client.post("/v1/me/push-subscriptions", json=_SUB).json() == {"subscribed": True}
    # A re-subscribe from the same endpoint replaces, never duplicates.
    assert client.post("/v1/me/push-subscriptions", json=_SUB).json() == {"subscribed": True}
    rows = all_subscriptions(session)
    assert [(row.endpoint, row.user_sub) for row in rows] == [
        ("https://push.example/one", "user-1")
    ]
    assert rows[0].keys == {"p256dh": "public-bytes", "auth": "auth-bytes"}

    assert client.request(
        "DELETE", "/v1/me/push-subscriptions", json={"endpoint": _SUB["endpoint"]}
    ).json() == {"subscribed": False}
    assert all_subscriptions(session) == []


def test_subscribe_rejects_a_non_https_endpoint(
    session: Session, set_setting: Callable[[str, str], None]
) -> None:
    _enable(set_setting)
    bad = {**_SUB, "endpoint": "http://push.example/insecure"}
    assert _client(session).post("/v1/me/push-subscriptions", json=bad).status_code == 422
