import pytest

from researchscout.config import Settings, get_settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.embedding_model == "BAAI/bge-small-en-v1.5"
    assert s.llm_base_url == "http://localhost:11434/v1"
    assert s.llm_model == "qwen2.5:3b-instruct"
    assert s.freshness_days == 30
    assert s.chat_guardrail is True
    assert s.cluster_algo == "agglomerative"
    assert s.foryou_centroids == 0  # legacy single-centroid feed until opted in
    assert s.foryou_mmr_lambda == 1.0
    assert s.foryou_explore_slots == 0
    assert s.chunk_retrieval is False
    assert s.embed_max_concurrency == 2


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_FRESHNESS_DAYS", "7")
    monkeypatch.setenv("RS_LLM_MODEL", "llama3.2:3b")
    monkeypatch.setenv("RS_CHAT_GUARDRAIL", "false")
    monkeypatch.setenv("RS_CLUSTER_ALGO", "hdbscan")
    monkeypatch.setenv("RS_FORYOU_CENTROIDS", "3")
    s = get_settings()
    assert s.freshness_days == 7
    assert s.llm_model == "llama3.2:3b"
    assert s.chat_guardrail is False
    assert s.cluster_algo == "hdbscan"
    assert s.foryou_centroids == 3


def test_build_sha_falls_back_to_the_platform_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Railway stamps deploys with RAILWAY_GIT_COMMIT_SHA; RS_BUILD_SHA still wins when set."""
    monkeypatch.delenv("RS_BUILD_SHA", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc1234def")
    assert Settings().build_sha == "abc1234def"
    monkeypatch.setenv("RS_BUILD_SHA", "override99")
    assert Settings().build_sha == "override99"


def test_sentry_dsn_reads_either_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bare SENTRY_DSN works (the SDK's conventional name); RS_SENTRY_DSN wins when set."""
    monkeypatch.delenv("RS_SENTRY_DSN", raising=False)
    assert Settings(_env_file=None).sentry_dsn == ""
    monkeypatch.setenv("SENTRY_DSN", "https://key@o1.ingest.us.sentry.io/1")
    assert Settings().sentry_dsn == "https://key@o1.ingest.us.sentry.io/1"
    monkeypatch.setenv("RS_SENTRY_DSN", "https://key@o1.ingest.us.sentry.io/2")
    assert Settings().sentry_dsn == "https://key@o1.ingest.us.sentry.io/2"
