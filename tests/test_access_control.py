"""The access list: who this deployment admits, and who may change that.

Clerk answers *who someone is*. These tests pin the separate question the app
answers itself — whether it wants them — and the fact that the answer is
enforced before any route runs, not just before a workspace is resolved.
"""

from types import SimpleNamespace

import pytest
from clerk_backend_api.security import AuthStatus, RequestState
from sqlalchemy.exc import OperationalError

from app.access import IdentityLookupFailed, _verified_primary_email, sync_bootstrap_admins
from app.config import Settings
from app.database import SessionLocal
from app.models import AccessGrant, AccessRole, WorkspaceMember
from tests.support.access import grant_access


def _clerk_settings(**overrides) -> Settings:
    return Settings(
        clerk_enabled=True,
        clerk_publishable_key="pk_test_example",
        clerk_frontend_api_url="https://example.clerk.accounts.dev",
        clerk_secret_key="sk_test_example",
        public_base_url="http://localhost:9000",
        _env_file=None,
        **overrides,
    )


def _signed_in_as(monkeypatch, sub: str, email: str | None) -> Settings:
    """Clerk says this request is `sub` with `email`; grants are left alone."""
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.access.get_settings", lambda: settings)
    monkeypatch.setattr("app.tenancy.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.web.get_settings", lambda: settings)

    async def fake_authenticate(request, _settings):
        return RequestState(
            status=AuthStatus.SIGNED_IN,
            payload={"sub": sub, "sid": f"sess_{sub}"},
            token="test-token",
        )

    async def fake_email(clerk_user_id: str, _settings):
        return email if clerk_user_id == sub else None

    monkeypatch.setattr("app.auth.authenticate_clerk_request", fake_authenticate)
    monkeypatch.setattr("app.access.email_for_clerk_user", fake_email)
    return settings


# --- The gate ---------------------------------------------------------------


def test_signed_in_stranger_is_refused_and_gets_no_workspace(client, monkeypatch, db):
    """The gap Cloudflare Access used to cover: a Clerk account is no longer
    enough to be handed a workspace on this deployment."""
    _signed_in_as(monkeypatch, "user_stranger", "stranger@example.test")

    page = client.get("/", follow_redirects=False)
    api = client.get("/api/hangouts")

    assert page.status_code == 403
    assert "not on the access list" in page.text
    assert api.status_code == 403
    assert api.json() == {"detail": "Access not granted"}

    members = db.query(WorkspaceMember).filter(WorkspaceMember.clerk_user_id == "user_stranger")
    assert members.count() == 0


def test_refusal_covers_routes_that_never_resolve_a_workspace(client, monkeypatch):
    """`/admin/logs` has no workspace dependency, so a check living in
    `current_workspace` would hand the whole audit stream to a stranger."""
    _signed_in_as(monkeypatch, "user_stranger", "stranger@example.test")

    assert client.get("/admin/logs").status_code == 403
    assert client.get("/settings/sms-simulator", follow_redirects=False).status_code == 403


def test_granted_user_reaches_the_app_and_gets_a_workspace(client, monkeypatch, db):
    _signed_in_as(monkeypatch, "user_ok", "ok@example.test")
    grant_access("ok@example.test", role=AccessRole.member)

    response = client.get("/api/hangouts")

    assert response.status_code == 200
    member = db.query(WorkspaceMember).filter(WorkspaceMember.clerk_user_id == "user_ok").one()
    # The email is recorded so the access list can be read against real people.
    assert member.email == "ok@example.test"


def test_grant_matching_ignores_case(client, monkeypatch):
    _signed_in_as(monkeypatch, "user_case", "Mixed.Case@Example.Test")
    grant_access("mixed.case@example.test", role=AccessRole.member)

    assert client.get("/api/hangouts").status_code == 200


def test_revoking_a_grant_locks_out_an_existing_member(client, monkeypatch, db):
    """Revocation is read from the database per request, so it takes effect on
    the next one rather than whenever a cache happens to expire."""
    _signed_in_as(monkeypatch, "user_revoked", "revoked@example.test")
    grant_access("revoked@example.test", role=AccessRole.member)
    assert client.get("/api/hangouts").status_code == 200

    db.query(AccessGrant).filter(AccessGrant.email == "revoked@example.test").delete()
    db.commit()

    assert client.get("/api/hangouts").status_code == 403


def test_unreachable_clerk_is_a_503_not_a_revoked_grant(client, monkeypatch):
    """Otherwise a Clerk outage tells every legitimate user they were removed
    from the access list, and sends them to an admin who cannot help."""
    settings = _signed_in_as(monkeypatch, "user_ok", "ok@example.test")
    grant_access("ok@example.test", role=AccessRole.member)

    async def unreachable(clerk_user_id: str, _settings):
        raise IdentityLookupFailed("clerk is down")

    monkeypatch.setattr("app.access.email_for_clerk_user", unreachable)

    assert settings.clerk_enabled
    assert client.get("/api/hangouts").status_code == 503
    assert client.get("/", follow_redirects=False).status_code == 503


def test_user_with_no_verified_email_is_refused(client, monkeypatch):
    _signed_in_as(monkeypatch, "user_noemail", None)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 403
    assert "no verified email address" in response.text


@pytest.mark.parametrize(
    "user, expected",
    [
        (
            SimpleNamespace(
                primary_email_address_id="idp",
                email_addresses=[
                    SimpleNamespace(
                        id="idp",
                        email_address="Primary@Example.Test",
                        verification=SimpleNamespace(status="verified"),
                    )
                ],
            ),
            "primary@example.test",
        ),
        # Unverified: sign-up would otherwise let anyone claim an allowed
        # address and inherit its grant.
        (
            SimpleNamespace(
                primary_email_address_id="idp",
                email_addresses=[
                    SimpleNamespace(
                        id="idp",
                        email_address="claimed@example.test",
                        verification=SimpleNamespace(status="unverified"),
                    )
                ],
            ),
            None,
        ),
        # A verified secondary address is not the identity the grant is for.
        (
            SimpleNamespace(
                primary_email_address_id="idp",
                email_addresses=[
                    SimpleNamespace(
                        id="other",
                        email_address="secondary@example.test",
                        verification=SimpleNamespace(status="verified"),
                    )
                ],
            ),
            None,
        ),
        (SimpleNamespace(primary_email_address_id=None, email_addresses=[]), None),
    ],
)
def test_only_a_verified_primary_email_identifies_a_user(user, expected):
    assert _verified_primary_email(user) == expected


# --- Who may edit the list --------------------------------------------------


def test_member_cannot_read_or_change_the_access_list(client, monkeypatch, db):
    _signed_in_as(monkeypatch, "user_member", "member@example.test")
    grant_access("member@example.test", role=AccessRole.member)

    read = client.get("/admin/access")
    write = client.post(
        "/admin/access",
        data={"email": "sneak@example.test", "role": "admin"},
        follow_redirects=False,
    )

    assert read.status_code == 403
    assert write.status_code == 403
    assert db.query(AccessGrant).filter(AccessGrant.email == "sneak@example.test").count() == 0


def test_admin_can_add_and_remove_emails(client, monkeypatch, db):
    _signed_in_as(monkeypatch, "user_admin", "admin@example.test")
    grant_access("admin@example.test", role=AccessRole.admin)

    added = client.post(
        "/admin/access",
        data={"email": "  NewPerson@Example.Test ", "role": "member"},
        follow_redirects=False,
    )
    assert added.status_code == 303

    grant = db.query(AccessGrant).filter(AccessGrant.email == "newperson@example.test").one()
    assert grant.role is AccessRole.member
    # Attribution: who let them in.
    assert grant.created_by == "admin@example.test"

    listing = client.get("/admin/access")
    assert "newperson@example.test" in listing.text
    assert "not signed in yet" in listing.text

    removed = client.post(f"/admin/access/{grant.id}/delete", follow_redirects=False)
    assert removed.status_code == 303
    assert db.query(AccessGrant).filter(AccessGrant.id == grant.id).count() == 0


def test_admin_form_rejects_a_non_email(client, monkeypatch, db):
    _signed_in_as(monkeypatch, "user_admin", "admin@example.test")
    grant_access("admin@example.test", role=AccessRole.admin)

    response = client.post(
        "/admin/access",
        data={"email": "not-an-email", "role": "member"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/access?notice=invalid-email"
    assert db.query(AccessGrant).count() == 1


def test_the_last_admin_cannot_be_removed_or_demoted(client, monkeypatch, db):
    """Otherwise the list becomes uneditable by anyone with a browser."""
    _signed_in_as(monkeypatch, "user_admin", "admin@example.test")
    grant_access("admin@example.test", role=AccessRole.admin)
    only_admin = db.query(AccessGrant).filter(AccessGrant.email == "admin@example.test").one()

    removed = client.post(f"/admin/access/{only_admin.id}/delete", follow_redirects=False)
    demoted = client.post(
        "/admin/access",
        data={"email": "admin@example.test", "role": "member"},
        follow_redirects=False,
    )

    assert removed.headers["location"] == "/admin/access?notice=last-admin"
    assert demoted.headers["location"] == "/admin/access?notice=last-admin"
    db.expire_all()
    assert db.query(AccessGrant).filter(AccessGrant.email == "admin@example.test").one().role is (
        AccessRole.admin
    )


def test_a_second_admin_makes_the_first_removable(client, monkeypatch, db):
    _signed_in_as(monkeypatch, "user_admin", "admin@example.test")
    grant_access("admin@example.test", role=AccessRole.admin)
    grant_access("admin2@example.test", role=AccessRole.admin)
    first = db.query(AccessGrant).filter(AccessGrant.email == "admin@example.test").one()

    response = client.post(f"/admin/access/{first.id}/delete", follow_redirects=False)

    assert response.headers["location"] == "/admin/access?notice=removed"
    assert db.query(AccessGrant).filter(AccessGrant.email == "admin@example.test").count() == 0


def test_access_pages_stay_open_when_clerk_is_disabled(client, db):
    """Local development has no identity at all; the page must not 403 into
    being unreachable."""
    assert client.get("/admin/access").status_code == 200


# --- Bootstrap --------------------------------------------------------------


def test_bootstrap_grants_admin_and_is_idempotent(db):
    created = sync_bootstrap_admins(["First@Example.Test", "second@example.test"])
    assert set(created) == {"first@example.test", "second@example.test"}

    again = sync_bootstrap_admins(["First@Example.Test", "second@example.test"])
    assert again == []
    assert db.query(AccessGrant).filter(AccessGrant.role == AccessRole.admin).count() == 2


def test_bootstrap_promotes_an_existing_member_and_skips_junk(db):
    grant_access("promote@example.test", role=AccessRole.member)

    created = sync_bootstrap_admins(["promote@example.test", "not-an-email", "  "])

    assert created == ["promote@example.test"]
    db.expire_all()
    grant = db.query(AccessGrant).filter(AccessGrant.email == "promote@example.test").one()
    assert grant.role is AccessRole.admin
    assert db.query(AccessGrant).count() == 1


def test_startup_bootstrap_runs_and_reports_the_admin_count(db, caplog):
    """The lifespan hook, not just the function it calls."""
    from app.main import _bootstrap_access

    _bootstrap_access(_clerk_settings(access_bootstrap_admins="boot@example.test, ,x@y.zz"))

    emails = {email for (email,) in db.query(AccessGrant.email)}
    assert emails == {"boot@example.test", "x@y.zz"}
    assert "No admin access grants exist" not in caplog.text


def test_startup_logs_an_error_when_nobody_can_sign_in(db, caplog):
    from app.main import _bootstrap_access

    _bootstrap_access(_clerk_settings(access_bootstrap_admins=""))

    assert db.query(AccessGrant).count() == 0
    assert "No admin access grants exist" in caplog.text


def test_startup_survives_a_database_it_cannot_read(db, caplog, monkeypatch):
    """A boot-time convenience must never become a boot loop.

    Both real causes reach here as an exception out of `admin_count`: Postgres
    down, and a deploy that restarts the service before `alembic upgrade head`
    creates `access_grants`. Crashing on either takes /api/health down too, and
    under systemd restarts forever.
    """
    from app import access
    from app.main import _bootstrap_access

    def explode(_db):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(access, "admin_count", explode)

    _bootstrap_access(_clerk_settings(access_bootstrap_admins="boot@example.test"))

    assert "alembic upgrade head" in caplog.text
    # Not "counted zero": never claim the list is empty when nothing answered.
    assert "No admin access grants exist" not in caplog.text
    # The seeding half still ran, so a transient outage does not lose the seed.
    assert db.query(AccessGrant).filter(AccessGrant.email == "boot@example.test").count() == 1


def test_bootstrap_never_deletes_what_an_admin_added(db):
    """It is additive on purpose: the env var is the way back in after a
    lockout, not a declaration that the database must match it exactly."""
    grant_access("added-in-ui@example.test", role=AccessRole.member)

    sync_bootstrap_admins(["boot@example.test"])

    emails = {email for (email,) in SessionLocal().query(AccessGrant.email)}
    assert emails == {"added-in-ui@example.test", "boot@example.test"}
