from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from researchscout.api.main import create_app

_CONFIG = """
sources:
  arxiv:
    enabled: true
    kind: content
    attribution:
      name: arXiv
      homepage: https://arxiv.org
      terms: https://info.arxiv.org/help/api/tou.html
      data_license: Metadata CC0 1.0
      provides: Titles and abstracts
  code_adoption:
    enabled: false
    kind: signal
    token: ghp_supersecrettoken
    attribution:
      name: GitHub
      homepage: https://github.com
      terms: https://docs.github.com/terms
      data_license: Repository metadata
      provides: Star counts
  openreview:
    enabled: false
    kind: signal
"""


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(_CONFIG)
    monkeypatch.setenv("RS_SOURCES_CONFIG_PATH", str(cfg))
    return TestClient(create_app())


def _by_name(body: dict[str, list[dict[str, object]]]) -> dict[str, dict[str, object]]:
    return {item["name"]: item for item in body["items"]}  # type: ignore[index]


def test_sources_lists_every_registered_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _client(tmp_path, monkeypatch).get("/v1/sources")
    assert response.status_code == 200
    items = _by_name(response.json())

    # Registered but absent from this config, so it lists as disabled rather than vanishing.
    assert items["bluesky"]["enabled"] is False
    arxiv = items["arxiv"]
    assert arxiv["kind"] == "content"
    assert arxiv["enabled"] is True
    assert arxiv["display_name"] == "arXiv"
    assert arxiv["terms_url"] == "https://info.arxiv.org/help/api/tou.html"
    assert arxiv["data_license"] == "Metadata CC0 1.0"


def test_sources_reports_disabled_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = _by_name(_client(tmp_path, monkeypatch).get("/v1/sources").json())
    assert items["code_adoption"]["enabled"] is False
    assert items["code_adoption"]["display_name"] == "GitHub"


def test_sources_marks_undeclared_attribution_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source without an attribution block shows the gap instead of disappearing."""
    items = _by_name(_client(tmp_path, monkeypatch).get("/v1/sources").json())
    assert items["openreview"]["display_name"] is None
    assert items["openreview"]["data_license"] is None


def test_sources_never_leaks_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config blocks hold api_key/token/mailto; only attribution is public."""
    response = _client(tmp_path, monkeypatch).get("/v1/sources")
    assert "ghp_supersecrettoken" not in response.text
    assert "token" not in response.json()["items"][0]
