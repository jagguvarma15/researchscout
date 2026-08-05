"""SQLAlchemy engine, session scope, and the declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from researchscout.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, created lazily from settings."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            # A pooled connection outlives the server it points at. Restart Postgres under a
            # running API, or leave the scheduler idle past the server's timeout, and the pool
            # still holds handles that fail the moment they are used - one 500 per stale
            # connection, for no reason the caller can act on. A pre-ping spends one round trip
            # finding that out and reconnecting instead.
            pool_pre_ping=True,
            # And retire them on a timer regardless, so a connection is never older than the
            # shortest idle timeout anything between here and the database is likely to have.
            pool_recycle=1800,
        )
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session, committing on success and rolling back on error."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=engine(), expire_on_commit=False)
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
