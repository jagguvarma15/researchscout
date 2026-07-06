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


def get_settings() -> Settings:
    """Load settings from the environment and ``.env``."""
    return Settings()
