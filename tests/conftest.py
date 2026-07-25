from collections.abc import Iterator

import pytest


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
                "TRUNCATE papers, paper_external_ids, raw_items, ingest_state, "
                "paper_embeddings, signals, digests, topics, saved_papers, user_interests "
                "RESTART IDENTITY CASCADE"
            )
        )
        # Commit before yielding: code under test opens its own session, and an uncommitted
        # TRUNCATE holds ACCESS EXCLUSIVE on these tables, so that session would block forever.
        s.commit()
        yield s
