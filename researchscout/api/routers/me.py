"""The signed-in account: who you are, the terms you accepted, your data, and its deletion.

Everything here is about one caller's own record. The terms endpoint is what the site's
acceptance dialog posts to, and export/delete are how someone exercises the rights the privacy
notice promises - which is why deletion refuses to run at all unless it can also remove the
login at the identity provider.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from researchscout.api.auth import User, require_user
from researchscout.api.deps import get_session
from researchscout.api.schemas import AccountDeleted, MeResponse, MeUpdate, TermsAcceptance
from researchscout.config import get_settings
from researchscout.identity import (
    IdentityDeletionUnavailable,
    delete_identity,
    identity_deletion_configured,
)
from researchscout.store.users import (
    accept_terms,
    delete_user,
    export_user_data,
    get_user,
    upsert_user,
)

router = APIRouter(tags=["me"])


def _me(user: User, session: Session) -> MeResponse:
    required = get_settings().terms_version
    row = get_user(session, user.sub)
    accepted = row.tos_version if row else None
    return MeResponse(
        sub=user.sub,
        username=user.username,
        email=row.email if row else None,
        display_name=row.display_name if row else None,
        avatar=row.avatar if row else None,
        terms_required=required,
        terms_accepted_version=accepted,
        terms_accepted=accepted == required,
    )


@router.get("/me")
def my_account(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MeResponse:
    """The caller's account. The site reads this on load to decide whether to ask for terms."""
    return _me(user, session)


@router.patch("/me")
def update_account(
    body: MeUpdate,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MeResponse:
    """Change the display name or the avatar; absent fields stay as they are.

    Email comes from the identity provider and is read-only here.
    """
    upsert_user(session, user.sub)
    row = get_user(session, user.sub)
    if row is None:  # pragma: no cover - upsert just created it
        raise HTTPException(status_code=500, detail="account row missing")
    if body.display_name is not None:
        row.display_name = body.display_name.strip()
    if body.avatar is not None:
        row.avatar = body.avatar or None
    session.flush()
    return _me(user, session)


@router.post("/me/terms")
def accept_current_terms(
    body: TermsAcceptance,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MeResponse:
    """Record acceptance of the current terms version.

    The posted version must be the one the server is asking for: accepting a version the site
    is no longer serving would leave the record saying something untrue.
    """
    required = get_settings().terms_version
    if body.version != required:
        raise HTTPException(status_code=409, detail=f"current terms version is {required}")
    accept_terms(session, user.sub, required)
    return _me(user, session)


@router.get("/me/export")
def export_account(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Everything stored about the caller, as JSON."""
    return export_user_data(session, user.sub)


@router.delete("/me")
def delete_account(
    user: Annotated[User, Depends(require_user)],
    session: Annotated[Session, Depends(get_session)],
) -> AccountDeleted:
    """Delete the account: the login at the provider first, then every row it owns.

    The provider call goes first because it is the fallible one - if it fails, nothing has been
    deleted yet and the caller can simply try again. In local no-auth mode there is no account
    to delete, so this refuses rather than removing the built-in user other rows depend on.
    """
    settings = get_settings()
    if not settings.oidc_issuer:
        raise HTTPException(
            status_code=403,
            detail="no accounts in local mode: the built-in user cannot be deleted",
        )
    if not identity_deletion_configured():
        raise HTTPException(
            status_code=503,
            detail="account deletion is unavailable: identity provider credentials are not set",
        )
    try:
        delete_identity(user.sub)
    except IdentityDeletionUnavailable as exc:
        raise HTTPException(
            status_code=502, detail=f"identity provider deletion failed: {exc}"
        ) from exc
    return AccountDeleted(deleted=delete_user(session, user.sub))
