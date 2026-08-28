"""The flagged highlight sync routes: off is byte-identical to absent."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.schema import Author, Paper
from researchscout.store.papers import upsert_paper
from researchscout.store.users import upsert_user

pytestmark = pytest.mark.integration


def _client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_user] = lambda: User(sub="user-1", username="demo")
    return TestClient(app)


def _seed(session: Session) -> None:
    upsert_paper(
        session,
        Paper(
            id="arxiv:2401.00001",
            external_ids={"arxiv": "2401.00001"},
            title="T",
            abstract="A",
            authors=[Author(name="X")],
            categories=["cs.LG"],
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            source="arxiv",
        ),
    )
    upsert_user(session, "user-1")
    session.commit()


_MARK = {
    "id": "abc-1",
    "page": 3,
    "color": "amber",
    "text": "the key sentence",
    "note": "compare with section 5",
    "rects": [{"x": 1.0, "y": 2.0, "w": 30.0, "h": 4.0}],
}


def test_flag_off_both_routes_404(session: Session) -> None:
    client = _client(session)
    assert client.get("/v1/me/highlights/arxiv:2401.00001").status_code == 404
    assert client.put("/v1/me/highlights/arxiv:2401.00001", json={"items": []}).status_code == 404


def test_round_trip_and_bulk_replace(
    session: Session, set_setting: Callable[[str, str], None]
) -> None:
    _seed(session)
    set_setting("RS_HIGHLIGHTS_SYNC", "true")
    client = _client(session)

    put = client.put("/v1/me/highlights/arxiv:2401.00001", json={"items": [_MARK]})
    assert put.status_code == 200
    assert put.json() == {"stored": 1}

    body = client.get("/v1/me/highlights/arxiv:2401.00001").json()
    assert body["items"] == [_MARK]

    # The write is a bulk replace: a noteless second mark supplants the first entirely,
    # and its absent note lands as SQL NULL, never the JSON-null imposter.
    second = {**_MARK, "id": "abc-2"}
    second.pop("note")
    assert client.put("/v1/me/highlights/arxiv:2401.00001", json={"items": [second]}).json() == {
        "stored": 1
    }
    body = client.get("/v1/me/highlights/arxiv:2401.00001").json()
    assert [item["id"] for item in body["items"]] == ["abc-2"]
    assert body["items"][0]["note"] is None
    stored = session.execute(
        text("SELECT note IS NULL FROM user_highlights WHERE highlight_id = 'abc-2'")
    ).scalar_one()
    assert stored is True

    # Empty clears.
    assert client.put("/v1/me/highlights/arxiv:2401.00001", json={"items": []}).json() == {
        "stored": 0
    }
    assert client.get("/v1/me/highlights/arxiv:2401.00001").json() == {"items": []}


def test_put_unknown_paper_is_404(
    session: Session, set_setting: Callable[[str, str], None]
) -> None:
    set_setting("RS_HIGHLIGHTS_SYNC", "true")
    response = _client(session).put("/v1/me/highlights/arxiv:9999.00000", json={"items": [_MARK]})
    assert response.status_code == 404
