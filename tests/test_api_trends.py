"""The trends endpoint: frontier series from dated scores, releases from the catalogue."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from researchscout.api.deps import get_session
from researchscout.api.main import create_app
from researchscout.store import catalog
from researchscout.store.catalog import ModelUpsert

pytestmark = pytest.mark.integration


def _client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def _seed(session: Session) -> None:
    catalog.upsert_models(
        session,
        [
            ModelUpsert(
                name="First Frontier",
                organization="OpenAI",
                publication_date=date(2026, 5, 1),
                source="epoch_ai",
            ),
            ModelUpsert(
                name="Second Frontier",
                organization="Anthropic",
                publication_date=date(2026, 7, 1),
                source="epoch_ai",
            ),
        ],
    )
    # mmlu is in config/providers.yaml's benchmark list, which is what the endpoint reads.
    catalog.replace_benchmark_results(
        session,
        "MMLU",
        date(2020, 9, 1),
        [
            ("First Frontier", 0.70, date(2026, 5, 2), None),
            ("Worse Later", 0.60, date(2026, 6, 1), None),
            ("Second Frontier", 0.90, date(2026, 7, 2), None),
        ],
        known_models=catalog.known_model_ids(session),
    )
    session.commit()


def test_trends_reports_frontier_and_releases(session: Session) -> None:
    _seed(session)
    body = _client(session).get("/v1/trends").json()

    series = {entry["id"]: entry for entry in body["sota"]}
    assert "mmlu" in series
    points = series["mmlu"]["points"]
    # Only the advances survive: the mid-run lower score never joins the frontier.
    assert [point["model_name"] for point in points] == ["First Frontier", "Second Frontier"]
    assert [point["score"] for point in points] == [0.70, 0.90]

    releases = [item["name"] for item in body["releases"]]
    assert releases == ["Second Frontier", "First Frontier"]  # newest first


def test_trends_empty_catalogue(session: Session) -> None:
    body = _client(session).get("/v1/trends").json()
    assert body == {"sota": [], "releases": []}
