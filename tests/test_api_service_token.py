"""The front door: who may reach the API at all, and whose address it believes.

Both matter more than they look. Without the token the public hostname is an open API; without
the address forwarding every signed-out visitor shares one rate-limit bucket, because they all
arrive from the site's own server.
"""

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import researchscout.api.ratelimit as ratelimit_mod
import researchscout.api.routers.papers as papers_router
from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.api.ratelimit import client_ip
from researchscout.api.service_auth import CLIENT_IP_HEADER, SERVICE_TOKEN_HEADER

_TOKEN = "a-long-shared-secret"


@pytest.fixture(autouse=True)
def _isolated_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit_mod, "_windows", {})


def _client(monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> TestClient:
    monkeypatch.setenv("RS_SERVICE_TOKEN", token or "")
    monkeypatch.setattr(papers_router, "list_papers", lambda *a, **k: [])
    monkeypatch.setattr(papers_router, "count_papers", lambda *a, **k: 0)
    # The feed reads the caller's dismissals, and the session here is a stub. What is under
    # test is the door, not the feed.
    monkeypatch.setattr(papers_router, "dismissed_papers", lambda *a, **k: [])
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_no_token_configured_leaves_the_api_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local install has no front door, and should not grow one by surprise."""
    assert _client(monkeypatch, token=None).get("/v1/papers").status_code == 200


def test_a_request_without_the_token_is_not_told_what_lives_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch, token=_TOKEN).get("/v1/papers")
    assert response.status_code == 404


def test_the_wrong_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, token=_TOKEN)
    assert client.get("/v1/papers", headers={SERVICE_TOKEN_HEADER: "wrong"}).status_code == 404
    # A prefix of the real token must not pass either.
    prefix = {SERVICE_TOKEN_HEADER: _TOKEN[:-1]}
    assert client.get("/v1/papers", headers=prefix).status_code == 404


def test_the_right_token_gets_through(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, token=_TOKEN).get(
        "/v1/papers", headers={SERVICE_TOKEN_HEADER: _TOKEN}
    )
    assert response.status_code == 200


def test_the_health_probe_stays_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container healthcheck calls it from inside, and it reveals nothing."""
    assert _client(monkeypatch, token=_TOKEN).get("/healthz").status_code == 200


def _request(headers: dict[str, str], *, trusted: bool, peer: str = "10.0.0.1") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    request = Request({"type": "http", "headers": raw, "client": (peer, 1234)})
    request.state.trusted_proxy = trusted
    return request


def test_a_forwarded_address_is_believed_from_the_site() -> None:
    request = _request({CLIENT_IP_HEADER: "203.0.113.7"}, trusted=True)
    assert client_ip(request) == "203.0.113.7"


def test_a_forwarded_address_is_ignored_from_anyone_else() -> None:
    """Otherwise a header anyone can set is a fresh rate-limit bucket for the asking."""
    request = _request({CLIENT_IP_HEADER: "203.0.113.7"}, trusted=False)
    assert client_ip(request) == "10.0.0.1"


def test_the_forwarded_chain_is_a_fallback() -> None:
    request = _request({"x-forwarded-for": "203.0.113.7, 10.1.1.1"}, trusted=True)
    assert client_ip(request) == "203.0.113.7"


def test_no_forwarding_falls_back_to_the_socket() -> None:
    assert client_ip(_request({}, trusted=True)) == "10.0.0.1"
