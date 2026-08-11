"""Who may sign in to this deployment, and who may change that list.

Clerk is only an identity provider: it says *who* someone is, never whether
this deployment wants them. Clerk's own allowlist and blocklist are paid-plan
features on this instance, and its restrictions endpoint is write-only
(`GET /v1/instance/restrictions` answers 405), so a Clerk-side list could
neither be used nor audited. The list therefore lives here, as one
`access_grants` row per allowed email.

Enforcement is in the auth middleware rather than in `current_workspace`,
because some protected routes (`/admin/logs`, `/settings/sms-simulator`)
never resolve a workspace and would otherwise be reachable by any signed-in
stranger.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

from clerk_backend_api import Clerk
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import AccessGrant, AccessRole

logger = logging.getLogger(__name__)

# Deliberately permissive: the authority on whether an address exists is Clerk,
# which already verified it. This only rejects obvious typos in the admin form.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

# A Clerk user's email is stable, so it is safe to cache; the *grant* is read
# from the database on every request, which is what makes a revocation take
# effect immediately rather than after this TTL.
_EMAIL_CACHE_TTL_SECONDS = 300.0
_email_cache: dict[str, tuple[float, str]] = {}
_email_cache_lock = threading.Lock()

# Standalone page: it must render for someone the app is refusing, so it does
# not extend base.html (whose nav and Clerk globals belong to the signed-in
# app).
_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@lru_cache(maxsize=8)
def clerk_client(secret_key: str) -> Clerk:
    """Reuse Clerk's verifier/client while allowing credential rotation in tests."""
    return Clerk(bearer_auth=secret_key or None)


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value)) and len(value) <= 255


def grant_for_email(db: Session, email: str) -> AccessGrant | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    return db.query(AccessGrant).filter(AccessGrant.email == normalized).first()


def role_for_email(email: str) -> str | None:
    """The grant role for `email`, or None when the address is not allowed.

    Opens its own session: the caller is the auth middleware, which runs
    outside FastAPI's dependency scope. Returns the role's *value* rather than
    the ORM object, which would be detached once the session closes.
    """
    db = SessionLocal()
    try:
        grant = grant_for_email(db, email)
        return grant.role.value if grant is not None else None
    finally:
        db.close()


def _verified_primary_email(user: object) -> str | None:
    """The user's primary email, but only if Clerk has verified it.

    An unverified address must never match the access list: sign-up would
    otherwise let anyone claim an allowed address and inherit its grant.
    """
    primary_id = getattr(user, "primary_email_address_id", None)
    for address in getattr(user, "email_addresses", None) or []:
        if primary_id and getattr(address, "id", None) != primary_id:
            continue
        verification = getattr(address, "verification", None)
        if getattr(verification, "status", None) != "verified":
            continue
        return normalize_email(getattr(address, "email_address", None))
    return None


class IdentityLookupFailed(Exception):
    """Clerk could not be asked who this user is.

    Distinct from "Clerk says they have no verified email": the first is an
    outage and must read as 503, the second is a real answer and reads as 403.
    Collapsing them would tell a legitimate user they had been removed from the
    access list every time Clerk's API hiccuped.
    """


def _fetch_clerk_email(clerk_user_id: str, secret_key: str) -> str | None:
    if not secret_key:
        # CLERK_JWT_KEY alone verifies sessions but cannot reach the Backend
        # API, so identity cannot be resolved to an email. Settings validation
        # warns about this combination; this is the runtime backstop.
        logger.error("CLERK_SECRET_KEY is required to resolve a Clerk user's email")
        raise IdentityLookupFailed("no secret key")
    try:
        user = clerk_client(secret_key).users.get(user_id=clerk_user_id)
    except Exception as exc:
        logger.exception("Clerk user lookup failed", extra={"clerk_user_id": clerk_user_id})
        raise IdentityLookupFailed("Clerk user lookup failed") from exc
    return _verified_primary_email(user) if user is not None else None


async def email_for_clerk_user(clerk_user_id: str, settings: Settings) -> str | None:
    """The verified primary email for a Clerk user id, cached in-process.

    Raises `IdentityLookupFailed` when Clerk could not be reached at all.
    """
    now = time.monotonic()
    with _email_cache_lock:
        cached = _email_cache.get(clerk_user_id)
        if cached is not None and cached[0] > now:
            return cached[1]

    email = await asyncio.to_thread(
        _fetch_clerk_email, clerk_user_id, settings.clerk_secret_key.strip()
    )
    if email:
        with _email_cache_lock:
            _email_cache[clerk_user_id] = (now + _EMAIL_CACHE_TTL_SECONDS, email)
    return email


def forget_cached_email(clerk_user_id: str) -> None:
    with _email_cache_lock:
        _email_cache.pop(clerk_user_id, None)


def access_denied_response(request: Request, email: str | None) -> Response:
    """Refuse a signed-in user who holds no grant.

    403, not 401: they authenticated fine, this deployment just does not admit
    them. Sending them back to /sign-in would loop, since signing in again
    changes nothing.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Access not granted"}, status_code=403)
    return _templates.TemplateResponse(
        request,
        "no_access.html",
        {"email": email},
        status_code=403,
    )


def sync_bootstrap_admins(emails: list[str]) -> list[str]:
    """Ensure every ACCESS_BOOTSTRAP_ADMINS address holds an admin grant.

    Runs at startup. Only ever creates or promotes — never deletes — so it is
    the recovery lever when the last admin is locked out: put the address back
    in the env and restart. The flip side, documented in docs/tenancy.md, is
    that an address left in the env comes back after a restart even if an admin
    removed it in the UI.
    """
    created: list[str] = []
    db = SessionLocal()
    try:
        for raw in emails:
            email = normalize_email(raw)
            if not email:
                continue
            if not is_valid_email(email):
                logger.error("Ignoring invalid ACCESS_BOOTSTRAP_ADMINS entry %r", raw)
                continue
            grant = grant_for_email(db, email)
            if grant is None:
                db.add(AccessGrant(email=email, role=AccessRole.admin))
                created.append(email)
            elif grant.role is not AccessRole.admin:
                grant.role = AccessRole.admin
                created.append(email)
        if created:
            db.commit()
    except Exception:
        db.rollback()
        # A failure here must not stop the server: the access list may already
        # be correct in the database, and refusing to boot would turn a
        # bootstrap problem into an outage.
        logger.exception("Bootstrap admin sync failed")
        return []
    finally:
        db.close()
    return created


def current_access_role(request: Request) -> str | None:
    """The role the middleware resolved for this request, if any."""
    return getattr(request.state, "access_role", None)


def require_admin(request: Request) -> None:
    """FastAPI dependency: refuse the request unless it comes from an admin.

    With Clerk disabled there is no identity at all — local development and the
    test suite run as the single implicit operator, so the access pages stay
    usable rather than becoming unreachable.
    """
    if not get_settings().clerk_enabled:
        return
    if current_access_role(request) != AccessRole.admin.value:
        raise HTTPException(status_code=403, detail="Admin access required")


def admin_count(db: Session) -> int:
    return db.query(AccessGrant).filter(AccessGrant.role == AccessRole.admin).count()
