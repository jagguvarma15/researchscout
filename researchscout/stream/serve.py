"""Composition root for the streaming worker behind ``scout stream serve``.

Settings resolve exactly once here and flow down as plain values; nothing in the hot path
re-reads the environment. The worker runs single-process single-worker (one partition per
topic on one machine), with Bytewax SQLite recovery partitions under .local/stream-recovery
giving resume-consistent at-least-once delivery; every sink write upserts on a natural key,
so replays converge.
"""

from __future__ import annotations

import logging
import threading

from bytewax.recovery import RecoveryConfig, init_db_dir
from bytewax.testing import run_main

from researchscout.config import Settings, get_settings
from researchscout.embed.factory import default_embedder
from researchscout.llm.openai_compat import OpenAICompatLLM
from researchscout.scheduler import Scheduler
from researchscout.store.db import session_scope
from researchscout.stream.broker import KafkaBroker, StreamTopics, ensure_topics
from researchscout.stream.categorize import Categorizer, load_labels
from researchscout.stream.flow import FlowDeps, build_flow
from researchscout.stream.inject import Injector
from researchscout.stream.parse import parse_stage
from researchscout.stream.producers import build_producer_tasks

logger = logging.getLogger(__name__)


def _producer_scheduler(settings: Settings, topics: StreamTopics) -> Scheduler:
    broker = KafkaBroker(settings.kafka_bootstrap)
    return Scheduler(
        build_producer_tasks(settings, broker, topics), tick_sec=settings.scheduler_tick_sec
    )


def run_stream(*, producer_only: bool = False, worker_only: bool = False) -> None:
    """Run producers and the worker together (or one side alone for debugging)."""
    settings = get_settings()
    if not settings.sources_config_path.exists():
        raise SystemExit(
            f"sources config not found at {settings.sources_config_path} - "
            "run from the repository root"
        )
    topics = StreamTopics.for_prefix(settings.kafka_topic_prefix)
    ensure_topics(settings.kafka_bootstrap, topics)

    if producer_only:
        logger.info("producers polling into %s (no worker)", topics.raw)
        _producer_scheduler(settings, topics).run_forever(lambda: False)
        return
    if not worker_only:
        scheduler = _producer_scheduler(settings, topics)
        thread = threading.Thread(
            target=lambda: scheduler.run_forever(lambda: False), name="producers", daemon=True
        )
        thread.start()
        logger.info("producers polling into %s", topics.raw)
    _run_worker(settings, topics)


def _run_worker(settings: Settings, topics: StreamTopics) -> None:
    """Run the parse/categorize/inject worker until interrupted."""
    embedder = default_embedder()
    labels = load_labels(settings.labels_config_path) if settings.stream_labels_enabled else []
    keyword_embedder = None
    if settings.stream_keyword_embedder == "static":
        from researchscout.embed.static import StaticKeywordEmbedder

        keyword_embedder = StaticKeywordEmbedder()
    categorizer = Categorizer(
        embedder,
        OpenAICompatLLM(),
        session_scope,
        topic_match_min=settings.stream_topic_match_min,
        keyword_min_similarity=settings.stream_keyword_min_similarity,
        keywords_llm_fallback=settings.stream_keywords_llm_fallback,
        labels=labels,
        keyword_candidate_cap=settings.stream_keyword_candidates,
        keyword_embedder=keyword_embedder,
    )
    injector = Injector(embedder, session_scope)
    deps = FlowDeps(
        parse=parse_stage,
        categorize_batch=categorizer.run_batch,
        inject_batch=injector.run_batch,
    )
    flow = build_flow(
        settings.kafka_bootstrap,
        topics,
        settings.stream_consumer_group,
        deps,
        batch_size=settings.stream_consumer_batch,
        taps=settings.stream_taps_enabled,
    )

    recovery_dir = settings.stream_recovery_dir
    recovery_dir.mkdir(parents=True, exist_ok=True)
    if not any(recovery_dir.iterdir()):
        init_db_dir(recovery_dir, 1)  # type: ignore[no-untyped-call]
        logger.info("initialized recovery partitions in %s", recovery_dir)
    logger.info("stream worker consuming %s on %s", topics.raw, settings.kafka_bootstrap)
    recovery = RecoveryConfig(recovery_dir)  # type: ignore[no-untyped-call]
    run_main(flow, recovery_config=recovery)  # type: ignore[no-untyped-call]
