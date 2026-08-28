from datetime import UTC, datetime

import httpx
import pytest

from researchscout.schema import SignalType
from researchscout.sources.base import RawItem
from researchscout.sources.bluesky import BlueskySource

SINCE = datetime(2024, 1, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, tzinfo=UTC)


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self.is_success = 200 <= status < 300
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._body


def _post(
    handle: str,
    *,
    uri: str | None = None,
    text: str = "",
    likes: int = 0,
    reposts: int = 0,
    quotes: int = 0,
    replies: int = 0,
) -> dict:
    post: dict = {
        "author": {"handle": handle},
        "record": {"text": text},
        "likeCount": likes,
        "repostCount": reposts,
        "quoteCount": quotes,
        "replyCount": replies,
    }
    if uri is not None:
        post["embed"] = {"external": {"uri": uri}}
    return post


def test_fetch_aggregates_engagement_and_excludes_bots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts = [
        _post(
            "researcher.bsky.social",
            uri="https://arxiv.org/abs/2401.00001",
            likes=12,
            reposts=3,
            quotes=1,
            replies=4,
        ),
        _post(  # link only in the text, no embed
            "reader.bsky.social",
            text="great paper arxiv.org/pdf/2401.00001v2",
            likes=5,
            replies=1,
        ),
        _post(  # excluded aggregator bot with huge counts
            "arxiv-daily-bot.bsky.social",
            uri="https://arxiv.org/abs/2401.00001",
            likes=500,
            replies=100,
        ),
        _post("other.bsky.social", text="no link here", likes=9),
    ]
    body = {"posts": posts}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, body))
    source = BlueskySource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {"2401.00001": "arxiv:2401.00001"})

    items, cursor = source.fetch(SINCE, None)

    assert cursor is None
    payloads = {item.payload["metric"]: item.payload for item in items}
    assert set(payloads) == {"engagement", "replies"}
    assert payloads["engagement"]["value"] == 21  # (12+3+1) + 5; the bot's 500 excluded
    assert payloads["replies"]["value"] == 5  # 4 + 1; the bot's 100 excluded
    assert payloads["engagement"]["posts"] == 2


def test_fetch_pages_until_cursor_runs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    def fake_get(url: str, *, params: dict, **kwargs: object) -> _Resp:
        calls.append(params.get("cursor"))
        page = len(calls)
        body: dict = {"posts": [_post(f"user{page}.bsky.social")]}
        if page < 3:
            body["cursor"] = f"c{page}"
        return _Resp(200, body)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("researchscout.sources.bluesky.time.sleep", lambda seconds: None)
    source = BlueskySource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {})
    source.fetch(SINCE, None)
    assert calls == [None, "c1", "c2"]


def test_fetch_cuts_the_window_client_side(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []
    fresh = _post("a.bsky.social", uri="https://arxiv.org/abs/2401.00001", likes=2)
    fresh["record"]["createdAt"] = "2024-02-01T00:00:00.000Z"
    stale = _post("b.bsky.social", uri="https://arxiv.org/abs/2401.00001", likes=9)
    stale["record"]["createdAt"] = "2023-12-01T00:00:00.000Z"

    def fake_get(url: str, *, params: dict, **kwargs: object) -> _Resp:
        calls.append(params.get("cursor"))
        return _Resp(200, {"posts": [fresh, stale], "cursor": "more"})

    monkeypatch.setattr(httpx, "get", fake_get)
    source = BlueskySource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {"2401.00001": "arxiv:2401.00001"})

    items, _ = source.fetch(SINCE, None)

    assert calls == [None]  # the stale post ends the walk despite the cursor
    payloads = {item.payload["metric"]: item.payload for item in items}
    assert payloads["engagement"]["value"] == 2  # only the in-window post counted


def test_normalize_maps_engagement_and_replies() -> None:
    source = BlueskySource()
    engagement = source.normalize(
        RawItem(
            source="bluesky",
            fetched_at=NOW,
            payload={
                "paper_id": "arxiv:2401.00001",
                "metric": "engagement",
                "value": 21,
                "posts": 2,
            },
        )
    )
    assert engagement.type == SignalType.social_mention
    assert engagement.value == 21.0
    assert engagement.metadata["posts"] == 2
    assert engagement.observed_at == NOW

    replies = source.normalize(
        RawItem(
            source="bluesky",
            fetched_at=NOW,
            payload={"paper_id": "arxiv:2401.00001", "metric": "replies", "value": 5},
        )
    )
    assert replies.type == SignalType.discussion
    assert replies.value == 5.0


def test_old_style_arxiv_ids_are_recognized() -> None:
    from researchscout.sources.bluesky import _ARXIV_RE

    match = _ARXIV_RE.search("https://arxiv.org/abs/math/0309136v2")
    assert match is not None and match.group(1) == "math/0309136"
    match = _ARXIV_RE.search("https://arxiv.org/pdf/cond-mat.str-el/0212346")
    assert match is not None and match.group(1) == "cond-mat.str-el/0212346"


def test_credentialless_fetch_sends_no_auth_and_no_since(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_get(url: str, *, params: dict, headers: dict, **kwargs: object) -> _Resp:
        seen["params"] = params
        seen["headers"] = headers
        return _Resp(200, {"posts": []})

    monkeypatch.delenv("BLUESKY_IDENTIFIER", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    monkeypatch.setattr(httpx, "get", fake_get)
    source = BlueskySource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {})
    source.fetch(SINCE, None)
    assert "since" not in seen["params"]
    assert "Authorization" not in seen["headers"]


def test_credentials_log_in_filter_server_side_and_skip_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUESKY_IDENTIFIER", "scout.example.com")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    logins: list[dict] = []
    searches: list[dict] = []

    def fake_post(url: str, *, json: dict, **kwargs: object) -> _Resp:
        logins.append(json)
        return _Resp(200, {"accessJwt": "token-1"})

    def fake_get(url: str, *, params: dict, headers: dict, **kwargs: object) -> _Resp:
        searches.append({"params": dict(params), "headers": dict(headers)})
        page = len(searches)
        body: dict = {"posts": [_post(f"user{page}.bsky.social")]}
        if page < 2:
            body["cursor"] = "c1"
        return _Resp(200, body)

    def forbidden_sleep(seconds: float) -> None:
        raise AssertionError("authenticated walks never pace")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("researchscout.sources.bluesky.time.sleep", forbidden_sleep)
    source = BlueskySource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {})
    source.fetch(SINCE, None)

    assert logins == [{"identifier": "scout.example.com", "password": "app-pass"}]  # one session
    assert len(searches) == 2
    assert searches[0]["params"]["since"] == "2024-01-01T00:00:00Z"
    assert searches[0]["headers"]["Authorization"] == "Bearer token-1"


def test_an_expired_session_refreshes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUESKY_IDENTIFIER", "scout.example.com")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-pass")
    tokens = iter(["stale-token", "fresh-token"])
    attempts: list[str] = []

    def fake_post(url: str, **kwargs: object) -> _Resp:
        return _Resp(200, {"accessJwt": next(tokens)})

    def fake_get(url: str, *, params: dict, headers: dict, **kwargs: object) -> _Resp:
        token = headers["Authorization"]
        attempts.append(token)
        if token == "Bearer stale-token":
            return _Resp(401, {})
        return _Resp(200, {"posts": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    source = BlueskySource()
    monkeypatch.setattr(source, "_match_stored", lambda ids: {})
    source.fetch(SINCE, None)
    assert attempts == ["Bearer stale-token", "Bearer fresh-token"]
