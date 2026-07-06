"""SQLAlchemy engine, session scope, and the declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from researchscout.config import get_settings
from researchscout.obs.otel import instrument_engine


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, created lazily from settings."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url)
        instrument_engine(_engine)
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
