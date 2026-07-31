"""Runtime configuration via environment variables (prefix ``RS_``) and an optional ``.env``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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
    # arXiv asks for about 3 seconds between API requests; 0 disables the pause (tests).
    arxiv_page_delay_sec: float = 3.0
    # Empty (the default) runs the API in local no-auth mode with a built-in user; set any
    # OIDC issuer to require Bearer tokens instead.
    oidc_issuer: str = ""
    oidc_audience: str = "api"
    # Where to fetch signing keys; defaults to the issuer's JWKS endpoint.
    oidc_jwks_url: str | None = None
    # Chat rate limiting (in-process fixed window).
    chat_rate_limit: int = 20
    chat_rate_window_seconds: int = 600
    # LLM scope pre-check that keeps /v1/chat on research topics. Fails open, so a broken
    # model never blocks a question; the CLI ask path never runs it.
    chat_guardrail: bool = True
    digest_days: int = 7
    digest_top_k: int = 10
    # Background refresh loop (`scout serve scheduler`, or `make scheduler`). Intervals are in
    # seconds; the ingest/signals look-back window is in days.
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
    # algo "hdbscan" (sklearn-native) needs no fixed count and leaves outliers unclustered; the
    # threshold only applies to the default agglomerative path.
    cluster_window_days: int = 30
    cluster_distance_threshold: float = 0.5
    cluster_algo: Literal["agglomerative", "hdbscan"] = "agglomerative"
    scheduler_topics_interval_sec: int = 86400
    # Agentic ask: decompose the question into sub-questions and follow one hop of references.
    agentic_ask: bool = False
    # Chunk-level retrieval: a third RRF leg over full-text chunks (needs scout fulltext +
    # scout index --chunks), and best-chunk excerpts in grounded answers.
    chunk_retrieval: bool = False
    # For You v2: 0 keeps the legacy single-centroid, interests-only feed. >=1 builds a profile
    # from saved papers (weighted down by save age) plus interests, clustered into up to N
    # centroids; the matching centroid names the "why this paper" reason.
    foryou_centroids: int = 0
    # 1.0 = pure relevance (MMR off); 0.6-0.8 trades relevance for diversity.
    foryou_mmr_lambda: float = 1.0
    # Feed slots reserved for high-momentum papers outside every centroid (0 = off).
    foryou_explore_slots: int = 0
    foryou_half_life_days: float = 75.0
    # Streaming categorize stage: minimum centroid cosine for tagging a paper with a live
    # topic, the keyword extraction similarity floor, the LLM fallback for weak extractions,
    # and the optional custom-label classifier over config/labels.yaml.
    stream_topic_match_min: float = 0.55
    stream_keyword_min_similarity: float = 0.35
    stream_keywords_llm_fallback: bool = True
    stream_labels_enabled: bool = False
    labels_config_path: Path = Path("config/labels.yaml")


def get_settings() -> Settings:
    """Load settings from the environment and ``.env``."""
    return Settings()
