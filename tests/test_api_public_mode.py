"""What a signed-out visitor may and may not do once the API is public.

The rule under test: the extractive answer is open to anyone, generated answers need an
account, and a visitor without one is rate limited by address rather than sharing a single
anonymous bucket with everybody else.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import researchscout.api.auth as auth_mod
import researchscout.api.ratelimit as ratelimit_mod
import researchscout.api.routers.ask as ask_router
import researchscout.api.routers.chat as chat_router
import researchscout.api.routers.stream as stream_router
from researchscout.answer import Answer, FastAnswer
from researchscout.api.deps import get_embedder, get_llm, get_session
from researchscout.api.llmgate import llm_slot
from researchscout.api.main import create_app

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_ISSUER = "https://example.us.auth0.com/"


class _FakeSigningKey:
    key = _KEY.public_key()


class _FakeJWKClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey()


def _token() -> str:
    claims: dict[str, Any] = {
        "sub": "auth0|abc",
        "iss": _ISSUER,
        "aud": "api",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    return jwt.encode(claims, _KEY, algorithm="RS256")


def _empty_fast() -> FastAnswer:
    answer = Answer(text="Nothing matched.", cited=[], hallucinated=[], used=[])
    return FastAnswer(answer=answer, found=False, best_relevance=None, entries=[])


@pytest.fixture(autouse=True)
def _isolated_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit_mod, "_windows", {})


# The site's own server is the only caller in production, so these tests speak the same way it
# does: the service token proves where the request came from, which is what lets the API
# believe the visitor address that follows it.
_SERVICE_TOKEN = "test-service-token"


def _from_site(**extra: str) -> dict[str, str]:
    return {"x-rs-service-token": _SERVICE_TOKEN, **extra}


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RS_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RS_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("RS_OIDC_AUDIENCE", "api")
    monkeypatch.setattr(auth_mod, "_jwk_client", lambda: _FakeJWKClient())
    monkeypatch.setattr(chat_router, "answer_fast", lambda *a, **k: _empty_fast())
    monkeypatch.setattr(chat_router, "record_metrics", lambda **kw: None)
    monkeypatch.setattr(ask_router, "answer_fast", lambda *a, **k: _empty_fast())
    monkeypatch.setattr(ask_router, "record_metrics", lambda **kw: None)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_embedder] = lambda: None
    app.dependency_overrides[get_llm] = lambda: None
    return TestClient(app)


def test_anonymous_gets_the_fast_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch).post(
        "/v1/chat", json={"question": "q", "mode": "fast"}, headers=_from_site()
    )
    assert response.status_code == 200
    assert "event: meta" in response.text


def test_anonymous_is_refused_a_generated_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch).post("/v1/chat", json={"question": "q"}, headers=_from_site())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_ask_follows_the_same_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    fast = client.post("/v1/ask", json={"question": "q", "mode": "fast"}, headers=_from_site())
    assert fast.status_code == 200
    assert client.post("/v1/ask", json={"question": "q"}, headers=_from_site()).status_code == 401


def test_a_signed_in_caller_may_ask_for_a_generated_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the answer itself - only that the account clears the gate the anonymous call hits."""
    client = _client(monkeypatch)
    monkeypatch.setattr(
        ask_router,
        "answer",
        lambda *a, **k: Answer(text="ok", cited=[], hallucinated=[], used=[]),
    )
    response = client.post(
        "/v1/ask", json={"question": "q"}, headers=_from_site(Authorization=f"Bearer {_token()}")
    )
    assert response.status_code == 200


def test_anonymous_limits_are_per_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """One visitor must not be able to spend everyone else's allowance."""
    monkeypatch.setenv("RS_CHAT_RATE_LIMIT_ANONYMOUS", "2")
    client = _client(monkeypatch)
    body = {"question": "q", "mode": "fast"}
    first = _from_site(**{"x-rs-client-ip": "203.0.113.1"})
    second = _from_site(**{"x-rs-client-ip": "203.0.113.2"})

    assert client.post("/v1/chat", json=body, headers=first).status_code == 200
    assert client.post("/v1/chat", json=body, headers=first).status_code == 200
    assert client.post("/v1/chat", json=body, headers=first).status_code == 429
    # A different address still has its own budget.
    assert client.post("/v1/chat", json=body, headers=second).status_code == 200


def test_signed_in_limits_follow_the_account_not_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RS_CHAT_RATE_LIMIT", "2")
    client = _client(monkeypatch)
    body = {"question": "q", "mode": "fast"}
    auth = _from_site(Authorization=f"Bearer {_token()}")

    assert client.post("/v1/chat", json=body, headers=auth).status_code == 200
    assert client.post("/v1/chat", json=body, headers=auth).status_code == 200
    # Same account, different address: still the same bucket.
    moved = {**auth, "x-rs-client-ip": "203.0.113.9"}
    assert client.post("/v1/chat", json=body, headers=moved).status_code == 429


def test_pipeline_stats_need_an_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """Throughput and failure detail about the machine behind the site are not public."""
    monkeypatch.setattr(stream_router, "hourly_stats", lambda *a, **k: [])
    client = _client(monkeypatch)
    assert client.get("/v1/stream/stats", headers=_from_site()).status_code == 401
    authed = client.get("/v1/stream/stats", headers=_from_site(Authorization=f"Bearer {_token()}"))
    assert authed.status_code == 200


def test_the_model_gate_refuses_rather_than_queueing_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One visitor cannot occupy the model: past the cap, callers are told to come back."""
    from fastapi import HTTPException

    monkeypatch.setenv("RS_LLM_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("RS_LLM_QUEUE_TIMEOUT_SECONDS", "0.05")
    with llm_slot():
        with pytest.raises(HTTPException) as caught:
            with llm_slot():
                pass  # pragma: no cover - the gate raises before this runs
    assert caught.value.status_code == 503
    # The slot is released again once the holder is done.
    with llm_slot():
        pass
