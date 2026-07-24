"""Runtime configuration via environment variables (prefix ``RS_``) and an optional ``.env``."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings; override any field via ``RS_<FIELD>`` env vars or a local ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="RS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+psycopg://researchscout:researchscout@localhost:5432/researchscout"
    )
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:3b-instruct"
    llm_api_key: str = "ollama"
    freshness_days: int = 30
    sources_config_path: Path = Path("config/sources.yaml")
    oidc_issuer: str = "http://localhost:8080/realms/researchscout"
    oidc_audience: str = "api"
    # Where to fetch signing keys; defaults to the issuer's JWKS endpoint. Override when the
    # issuer URL (what the browser sees) is not reachable from inside the container network.
    oidc_jwks_url: str | None = None
    redis_url: str = "redis://localhost:6379"
    chat_rate_limit: int = 20
    chat_rate_window_seconds: int = 600
    # Host default targets compose's published listener; containers override to kafka:9092.
    kafka_bootstrap_servers: str = "localhost:29092"
    airtable_api_key: str = ""
    airtable_base_id: str = ""
    airtable_saved_table: str = "Saved papers"
    airtable_watchlist_table: str = "Watchlist"
    airtable_digest_table: str = "Digest archive"
    digest_days: int = 7
    digest_top_k: int = 10
    # Observability: OTel is off unless a collector is reachable; LangSmith rides its own
    # standard env vars (LANGSMITH_TRACING / LANGSMITH_API_KEY).
    otel_enabled: bool = False
    otlp_endpoint: str = "http://localhost:4317"
    # Background refresh loop (`scout serve scheduler`), opt-in via the compose `scheduler`
    # profile. Intervals are in seconds; the ingest/signals look-back window is in days.
    scheduler_ingest_window_days: int = 2
    scheduler_ingest_interval_sec: int = 3600
    scheduler_index_interval_sec: int = 900
    scheduler_signals_interval_sec: int = 21600
    scheduler_digest_interval_sec: int = 86400
    scheduler_tick_sec: int = 30
    # Breakthrough scoring: how the signal series becomes a ranking boost. The window bounds how
    # far back momentum is measured; the weights set how much velocity and acceleration count
    # relative to the raw level.
    score_window_days: int = 30
    score_velocity_weight: float = 2.0
    score_acceleration_weight: float = 1.0
    # Optional cross-encoder reranking of the top retrieval candidates (see researchscout.rerank).
    rerank_enabled: bool = False
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_top_n: int = 40
    # Emerging-topic clustering: the window to cluster over and the cosine-distance threshold that
    # decides how tight a cluster is (lower = tighter, more topics). The scheduler rebuilds daily.
    cluster_window_days: int = 30
    cluster_distance_threshold: float = 0.5
    scheduler_topics_interval_sec: int = 86400


def get_settings() -> Settings:
    """Load settings from the environment and ``.env``."""
    return Settings()
