from collections.abc import Callable, Iterator

import pytest


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Give every test its own view of the configuration.

    ``get_settings`` is cached per process, which is right in production and wrong in a test
    suite where half the files set ``RS_*`` with ``monkeypatch.setenv``. Clearing on both sides
    means a test neither inherits the previous one's environment nor leaves its own behind, and
    no individual test has to remember to do it.
    """
    from researchscout.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_sources_config() -> Iterator[None]:
    """Give every test its own view of the sources registry file.

    ``_load_config`` caches the parsed YAML the way ``get_settings`` caches the environment,
    and the API-sources tests each point it at a fresh temp file - same reasoning, same
    both-sides clear.
    """
    from researchscout.sources.base import _load_config

    _load_config.cache_clear()
    yield
    _load_config.cache_clear()


@pytest.fixture
def set_setting(monkeypatch: pytest.MonkeyPatch) -> Callable[[str, str], None]:
    """Change an ``RS_*`` variable part-way through a test and have it be seen.

    Configuration is read once per process, so setting the environment after something has
    already read it changes nothing - which is the production contract, not an oversight. A
    test that flips a flag between two calls is asking for the thing a restart would do, so it
    says so through this rather than by setting the variable and hoping.

    Tests that only set the environment before the first read can keep using ``monkeypatch``.
    """
    from researchscout.config import get_settings

    def _set(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    return _set


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """Spin up a throwaway pgvector Postgres for integration tests (needs Docker)."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def session(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """A clean, migrated session bound to the test database (truncated per test)."""
    monkeypatch.setenv("RS_DATABASE_URL", pg_url)

    import researchscout.store.db as db_mod

    if db_mod._engine is not None:
        # Else every test leaks a pool against the container's connection cap.
        db_mod._engine.dispose()
    db_mod._engine = None
    db_mod._session_factory = None

    from alembic.config import Config
    from sqlalchemy import text

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")

    with db_mod.session_scope() as s:
        s.execute(
            text(
                "TRUNCATE papers, paper_external_ids, raw_items, ingest_state, ask_metrics, "
                "paper_embeddings, paper_chunks, signals, digests, topics, saved_papers, "
                "user_interests, citation_edges, citation_fetches, events, pipeline_lineage, "
                "account_searches, account_recent_papers, account_dismissals, account_filters, "
                "ai_models, benchmarks, benchmark_results, scheduler_runs, llm_usage, "
                "users RESTART IDENTITY CASCADE"
            )
        )
        # Migration 0019 guarantees the built-in local user exists; truncating users removes
        # it, so restore it here or every no-auth write hits the new foreign key.
        s.execute(text("INSERT INTO users (sub) VALUES ('local')"))
        # Commit before yielding: code under test opens its own session, and an uncommitted
        # TRUNCATE holds ACCESS EXCLUSIVE on these tables, so that session would block forever.
        s.commit()
        yield s
