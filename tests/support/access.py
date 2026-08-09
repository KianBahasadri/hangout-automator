"""Grant a fake Clerk user access, for tests that sign somebody in.

Every protected route now runs behind the access list, and resolving a Clerk
user id to an email is a Backend API call. Tests must not make that call, so
they patch the resolver and insert the matching `access_grants` row here rather
than each re-inventing the seam.
"""

from __future__ import annotations

from app.access import forget_cached_email, grant_for_email
from app.database import SessionLocal
from app.models import AccessGrant, AccessRole


def email_for_sub(sub: str) -> str:
    return f"{sub}@example.test"


def allow_clerk_user(
    monkeypatch,
    sub: str,
    *,
    email: str | None = None,
    role: AccessRole = AccessRole.admin,
) -> str:
    """Resolve `sub` to `email` without Clerk, and grant that email access.

    Admin by default: most tests drive the whole app surface, and a member
    would be refused on the access pages for reasons unrelated to what they
    assert.
    """
    address = (email or email_for_sub(sub)).lower()

    async def fake_email_for_clerk_user(clerk_user_id: str, settings) -> str | None:
        return address if clerk_user_id == sub else None

    monkeypatch.setattr("app.access.email_for_clerk_user", fake_email_for_clerk_user)
    forget_cached_email(sub)
    grant_access(address, role=role)
    return address


def grant_access(email: str, *, role: AccessRole = AccessRole.member) -> None:
    """Insert or promote a grant, in its own session (fixtures share none)."""
    db = SessionLocal()
    try:
        existing = grant_for_email(db, email)
        if existing is None:
            db.add(AccessGrant(email=email.lower(), role=role))
        else:
            existing.role = role
        db.commit()
    finally:
        db.close()
