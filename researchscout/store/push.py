"""Web push subscriptions: upsert on subscribe, delete on unsubscribe or expiry."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from researchscout.store.models import PushSubscriptionRow


def save_subscription(session: Session, user_sub: str, endpoint: str, keys: dict[str, str]) -> None:
    """Upsert a browser's subscription; a re-subscribe replaces the row it already owns."""
    stmt = insert(PushSubscriptionRow).values(endpoint=endpoint, user_sub=user_sub, keys=keys)
    stmt = stmt.on_conflict_do_update(
        index_elements=["endpoint"],
        # Subscript, not attribute: ``excluded`` is a column collection, and a column named
        # ``keys`` collides with the collection's own keys() method.
        set_={"user_sub": stmt.excluded.user_sub, "keys": stmt.excluded["keys"]},
    )
    session.execute(stmt)
    session.flush()


def delete_subscription(session: Session, user_sub: str, endpoint: str) -> bool:
    """Remove one of the caller's subscriptions; False when it was not theirs to remove."""
    result = session.execute(
        delete(PushSubscriptionRow)
        .where(
            PushSubscriptionRow.endpoint == endpoint,
            PushSubscriptionRow.user_sub == user_sub,
        )
        .returning(PushSubscriptionRow.endpoint)
    )
    return result.scalar_one_or_none() is not None


def delete_endpoint(session: Session, endpoint: str) -> None:
    """Remove a dead endpoint regardless of owner - the push service said it is gone."""
    session.execute(delete(PushSubscriptionRow).where(PushSubscriptionRow.endpoint == endpoint))


def all_subscriptions(session: Session) -> list[PushSubscriptionRow]:
    """Every stored subscription - a publish notifies every subscribed browser."""
    return list(session.execute(select(PushSubscriptionRow)).scalars())
