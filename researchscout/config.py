"""Runtime configuration via environment variables (prefix ``RS_``) and an optional ``.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
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
    # Where to fetch signing keys. Left unset, the issuer's OIDC discovery document is asked,
    # which is right for any compliant provider; set it only to skip that request.
    oidc_jwks_url: str | None = None
    # The terms version the site currently asks people to accept. Bumping it makes every
    # account re-accept on their next visit, so change it only when the terms change.
    terms_version: str = "2026-08-01"
    # Deleting an account must also delete the identity at the provider, or "delete my
    # account" is a half-truth. These are an Auth0 machine-to-machine application with the
    # delete:users scope; with an issuer set but these empty, deletion refuses rather than
    # leaving the login behind.
    auth0_domain: str = ""
    auth0_mgmt_client_id: str = ""
    auth0_mgmt_client_secret: str = ""
    # A shared secret the site's own server sends with every API call. Empty (the default)
    # leaves the API open, which is what a local install wants; set it once the API has a
    # public hostname, and anything arriving without it gets a 404.
    service_token: str = ""
    # Signed-out callers get the extractive answer path only, on a tighter budget than an
    # account: no model call, and a bucket keyed by address rather than by account.
    chat_rate_limit_anonymous: int = 5
    # Concurrent model generations, and how long a caller waits for a slot before being told
    # the service is busy. The model shares this machine with Postgres and the API.
    llm_max_concurrency: int = 2
    llm_queue_timeout_seconds: float = 20.0
    # Per-request client behavior: how long one completion may take before the client gives
    # up, and how many times it retries a failed request first. One retry rides out a
    # transient per-minute limit; retrying a spent daily quota is pure waste, so this stays
    # low (the openai default is 2, i.e. three requests per 429).
    llm_timeout_sec: float = 120.0
    llm_max_retries: int = 1
    # Chat rate limiting (in-process fixed window).
    # Web search fallback (arXiv + Semantic Scholar cards on not-found answers). Default
    # ON deliberately (a documented deviation like the guardrail): user-triggered, zero
    # idle cost, gracefully failing; the flag is a kill switch. The import endpoint stays
    # available regardless.
    web_search_enabled: bool = True
    web_search_rate_limit: int = 10
    web_search_rate_window_seconds: int = 60
    chat_rate_limit: int = 20
    chat_rate_window_seconds: int = 600
    # LLM scope pre-check that keeps /v1/chat on research topics. Fails open, so a broken
    # model never blocks a question; the CLI ask path never runs it.
    chat_guardrail: bool = True
    digest_days: int = 7
    digest_top_k: int = 10
    # Background refresh loop (`scout serve scheduler`, or `make scheduler`). Intervals are in
    # seconds; the ingest/signals look-back window is in days.
    # Ingest, embedding, full text and signals normally come from the streaming pipeline.
    # Turn this on for an install that does not run the stream - without it, such a deployment
    # never sees another paper. Never run both: two processes fetching from the same upstreams
    # on one address is exactly what arXiv's three-second floor cannot survive.
    scheduler_batch_pipeline: bool = False
    # Wall-clock scheduling, as comma-separated HH:MM times in scheduler_timezone. Empty (the
    # default) leaves every task on its interval below, which is what a local checkout wants.
    # Set them and the named tasks run at those times instead: ingest/index/fulltext on the
    # pipeline times (arXiv's search index refreshes once a day around midnight ET, so one
    # early-morning slot sees everything a day can bring), the fast signal proxies (HF
    # trending, HN, Bluesky) on their own times, the citation walker on its own, the daily
    # set (model catalogue, digest, topics) on the daily times, and the report on its own so
    # the morning read covers the overnight arrivals. A named zone rather than a fixed
    # offset, so the runs stay put on the local clock across daylight saving.
    scheduler_pipeline_at: str = ""
    scheduler_signals_at: str = ""
    scheduler_citations_at: str = ""
    # The lastUpdatedDate revisions sweep. Unset (the default) = the task is not
    # scheduled at all; there is no interval fallback.
    scheduler_revisions_at: str = ""
    scheduler_daily_at: str = ""
    scheduler_report_at: str = ""
    scheduler_timezone: str = "America/New_York"
    # The minimum look-back under the per-source watermark: each ingest run starts from its
    # last completed walk minus this overlap (never less), so downtime widens the window by
    # itself and the overlap absorbs announcement lag. The max caps that widening - anything
    # longer gone is a deliberate backfill, not a catch-up.
    scheduler_ingest_window_days: int = 2
    scheduler_ingest_max_window_days: int = 30
    scheduler_ingest_interval_sec: int = 3600
    # Stop an ingest run after this many consecutive pages on which every entry was already
    # stored (0 = walk the whole window). Sound because arXiv pages newest-first, so nothing
    # new on N pages means the rest of the window is older still; this is what keeps several
    # runs a day inside the request budget the upstream tolerates.
    scheduler_ingest_early_stop_pages: int = 0
    scheduler_index_interval_sec: int = 900
    # Batch keyword/label enrichment (the stream's categorize stage, run over papers whose
    # keywords are still NULL). The batch bounds one run; the backlog drains across runs.
    scheduler_categorize_interval_sec: int = 3600
    scheduler_categorize_batch: int = 300
    scheduler_signals_interval_sec: int = 21600
    scheduler_citations_interval_sec: int = 86400
    scheduler_fulltext_interval_sec: int = 3600
    scheduler_fulltext_batch: int = 25
    scheduler_digest_interval_sec: int = 86400
    scheduler_report_interval_sec: int = 86400
    scheduler_catalog_interval_sec: int = 86400
    scheduler_health_interval_sec: int = 1800
    scheduler_tick_sec: int = 30
    # Touched by the scheduler on every tick and between long-running items; a container
    # healthcheck reads the file's age to tell a live loop from a wedged one. Empty = off.
    scheduler_heartbeat_path: str = ""
    # The citation walker's daily budgets: how many papers the Semantic Scholar pass may
    # refresh per run (batches of 500), how stale a paper's citation watermark must be before
    # the OpenAlex fallback takes it, and how many the fallback may take per run.
    citations_daily_papers: int = 5000
    citations_fallback_days: int = 7
    citations_fallback_papers: int = 2000
    # Land papers that Hugging Face's curated daily list names but the corpus lacks, so a
    # paper trending before the nightly ingest reaches it keeps its first observations.
    # The scope rule still applies; open forums (HN, Bluesky) never auto-import.
    signal_auto_import: bool = False
    # Raw fetched payloads are kept this many days for replay/debugging, then pruned.
    raw_items_keep_days: int = 30
    # Breakthrough scoring: how the signal series becomes a ranking boost. The window bounds how
    # far back momentum is measured; the weights set how much velocity and acceleration count
    # relative to the raw level.
    score_window_days: int = 30
    score_velocity_weight: float = 2.0
    score_acceleration_weight: float = 1.0
    # Optional cross-encoder reranking of the top retrieval candidates (see researchscout.rerank).
    # The fast-answer found/not-found floor against the cross-encoder sigmoid (clear
    # positives 0.7-0.99, hard negatives under 0.1). When the evidence is cosine-only,
    # ask_min_similarity below applies instead.
    ask_min_relevance: float = 0.30
    # The same floor for the cosine scale (rerank off or skipped): bge-small compresses
    # similarities, so off-topic questions still reach about 0.62 while on-topic hits
    # start around 0.76 (calibrated live 2026-07-31); 0.68 splits the gap with margin.
    ask_min_similarity: float = 0.68
    # Run the cross-encoder inside fast extractive answers. The pass costs about 600ms
    # warm and seconds under memory pressure - most of a fast answer's latency - while
    # cosine plus the calibrated floor keeps the found gate honest. Off skips it for
    # fast mode only; LLM answers keep whatever RS_RERANK_ENABLED says.
    ask_fast_rerank: bool = True
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
    # Streaming pipeline: the brew-managed broker, the topic prefix (rs.raw.v1 and the
    # parsed/enriched taps), the worker's consumer group, and its recovery partitions.
    # The consumer batch sizes the lists the batch stages see; taps off skips the two
    # per-batch tap flushes (scout stream tail goes dark while off).
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_prefix: str = "rs"
    stream_consumer_group: str = "rs-stream"
    stream_recovery_dir: Path = Path(".local/stream-recovery")
    stream_consumer_batch: int = 100
    stream_taps_enabled: bool = True
    # Producer polling: content hourly, fulltext in modest politely-paced batches (the
    # signals cadence reuses scheduler_signals_interval_sec).
    stream_poll_interval_sec: int = 3600
    stream_fulltext_interval_sec: int = 900
    stream_fulltext_batch: int = 25
    # Producer dedup: skip re-publishing papers the pipeline already enriched. Default ON
    # deliberately (a documented deviation from the default-off convention): this is a
    # latency fix, and turning it off restores the old republish-the-whole-window behavior,
    # which is idempotent but floods the worker every poll. The overlap widens the known-id
    # map past the poll window so boundary papers are never missed.
    stream_publish_dedup: bool = True
    stream_dedup_overlap_days: int = 1
    # Streaming categorize stage: minimum centroid cosine for tagging a paper with a live
    # topic, the keyword extraction similarity floor, the candidate cap (top-N n-grams by
    # in-document frequency actually embedded), the LLM fallback for weak extractions,
    # and the optional custom-label classifier over config/labels.yaml.
    stream_topic_match_min: float = 0.55
    stream_keyword_min_similarity: float = 0.35
    stream_keyword_candidates: int = 80
    # "static" scores keyword candidates with model2vec potion-base-8M (needs the
    # static-embed extra; much faster, 256-dim scoring space). The similarity floor above
    # was tuned in bge space - watch the keyword-method dashboard panel before relying on
    # static rankings. The stored paper embedding stays bge either way.
    stream_keyword_embedder: Literal["bge", "static"] = "bge"
    stream_keywords_llm_fallback: bool = True
    stream_labels_enabled: bool = False
    labels_config_path: Path = Path("config/labels.yaml")
    # Which organisations get a row in the provider table on /benchmarks, and the benchmarks
    # that table compares them on. A file rather than a query because "which labs matter" is a
    # judgement call, and one that will need revisiting as the field moves.
    providers_config_path: Path = Path("config/providers.yaml")
    # The commit a deployment runs, served by /v1/system/status - which is how a stale
    # deployment becomes a readable fact instead of a guess. Railway stamps every
    # GitHub-triggered deploy with RAILWAY_GIT_COMMIT_SHA, read directly because template
    # references cannot reach per-deploy variables; RS_BUILD_SHA overrides when set (its
    # alias is listed first). Empty for a source checkout or a CLI upload.
    build_sha: str = Field(
        default="",
        validation_alias=AliasChoices("RS_BUILD_SHA", "RAILWAY_GIT_COMMIT_SHA"),
    )
    # Error reporting. Set a Sentry DSN under either name and the serve entrypoints report
    # unhandled request and task errors; unset (the default), nothing ever initializes.
    sentry_dsn: str = Field(
        default="",
        validation_alias=AliasChoices("RS_SENTRY_DSN", "SENTRY_DSN"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings from the environment and ``.env``, once per process.

    Cached because it is not free. pydantic-settings re-reads and re-parses the dotenv file on
    every instantiation, and this is called from the service-token middleware, from
    ``require_user``, from retrieval and from the feed's sort compiler - several times for a
    single request. Caching also removes a subtler problem: uncached, one request could read
    the file twice and see two different configurations if it changed in between.

    Settings are process-wide and read at start-up everywhere else, so nothing expects an edit
    to ``.env`` to take effect without a restart. Tests that manipulate the environment need
    ``get_settings.cache_clear()``; an autouse fixture in ``tests/conftest.py`` does it for
    them so no individual test has to remember.
    """
    return Settings()
