"""Runtime configuration via environment variables (prefix ``RS_``) and an optional ``.env``."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ResearchScout settings. Override any field via ``RS_<FIELD>`` env vars or a local ``.env``."""

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
    freshness_days: int = 30
    sources_config_path: Path = Path("config/sources.yaml")


def get_settings() -> Settings:
    """Load settings from the environment and ``.env``."""
    return Settings()
