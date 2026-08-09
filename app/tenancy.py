"""Tenancy: how a request resolves to the workspace it acts in.

Clerk stays a pure identity provider; membership is app-owned
(`workspace_members`). With `CLERK_ENABLED=false` every request resolves to the
seeded `default` workspace so local dev and the test suite keep working.
"""

import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Workspace, WorkspaceMember, WorkspaceRole

logger = logging.getLogger(__name__)


def _default_workspace(db: Session) -> Workspace:
    return db.query(Workspace).filter(Workspace.slug == "default").one()


def current_workspace(request: Request, db: Session = Depends(get_db)) -> Workspace:
    """FastAPI dependency: the workspace this request acts in.

    Never raises for a user with no membership — provisioning one is part of
    resolution (a brand-new user owns a fresh, empty workspace). The 404 rule
    in this codebase is about *resources*, never about membership.

    It does raise 401 for a request that cannot be attributed to any user,
    which is a different thing entirely: no identity, so no workspace.
    """
    if not get_settings().clerk_enabled:
        return _default_workspace(db)

    clerk_user_id = getattr(request.state, "clerk_user_id", None)
    if not clerk_user_id:
        # Authenticated by the middleware but carrying no usable `sub`, so the
        # request cannot be attributed to anyone. This must fail closed: the
        # earlier behaviour returned the shared `default` workspace, which
        # handed an unattributable request another tenant's data. It should be
        # unreachable — the middleware only sets state after
        # is_authenticated — so log it rather than failing silently.
        logger.error(
            "Clerk-authenticated request has no subject claim; refusing",
            extra={"path": request.url.path},
        )
        raise HTTPException(status_code=401, detail="Authentication required")

    member = (
        db.query(WorkspaceMember).filter(WorkspaceMember.clerk_user_id == clerk_user_id).first()
    )
    if member is not None:
        return db.get(Workspace, member.workspace_id)

    return _provision_workspace(db, clerk_user_id)


def _provision_workspace(db: Session, clerk_user_id: str) -> Workspace:
    """First authenticated request from this user: create workspace + owner.

    The slug is derived deterministically from the user id, so the workspace
    `slug` unique constraint is what stops two concurrent first requests from
    creating two workspaces: the loser's INSERT fails and we re-read instead.
    """
    slug = f"user-{clerk_user_id}"[:64]
    workspace = Workspace(name="My workspace", slug=slug)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            clerk_user_id=clerk_user_id,
            role=WorkspaceRole.owner,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        member = (
            db.query(WorkspaceMember).filter(WorkspaceMember.clerk_user_id == clerk_user_id).first()
        )
        if member is not None:
            return db.get(Workspace, member.workspace_id)
        raise
    return workspace


def scoped(db: Session, model: type, workspace: Workspace):
    """Query for `model` restricted to `workspace`'s rows."""
    return db.query(model).filter(model.workspace_id == workspace.id)


def get_scoped(db: Session, model: type, row_id: int, workspace: Workspace):
    """The `row_id` row of `model` inside `workspace`, or None.

    None is what callers turn into a 404 — a foreign workspace's row must never
    be reachable by id.
    """
    return scoped(db, model, workspace).filter(model.id == row_id).first()
