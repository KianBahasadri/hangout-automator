import os
from pathlib import Path

# Postgres only. TEST_DATABASE_URL overrides the database (CI provides it);
# the default is the local compose instance with the test database name.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://hangout:hangout@localhost:5432/hangout_test",
)
os.environ["SMS_PROVIDER"] = "mock"
os.environ["FOLLOWUP_HOURS"] = "1,2"
# Keep the application import hermetic when a developer's .env enables Clerk.
# Auth-specific tests construct their own enabled Settings and monkeypatch the
# middleware verifier explicitly.
os.environ["CLERK_ENABLED"] = "false"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.database import DEFAULT_DIETARY_RESTRICTIONS, SessionLocal
from app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Bring the test database to head once per session, like a deploy step."""
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db):
    yield
    from sqlalchemy import text

    from app.database import Base, engine

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        # The default dietary restrictions are a one-shot data migration under
        # Alembic, so no per-test bootstrap re-seeds them. Re-insert them here
        # (test infrastructure, not app behavior) so every test starts from the
        # same "fresh seeded database" state the lifespan bootstrap used to give.
        conn.execute(
            text("INSERT INTO allergies (name) VALUES (:name)"),
            [{"name": name} for name in DEFAULT_DIETARY_RESTRICTIONS],
        )


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_no_raise():
    """Return HTTP 500 responses so API tests can assert no exception escapes."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def sample_data(db):
    """One row of everything, so routes that take an id have something real to hit.

    Returns the ids by *path parameter name* (`profile_id`, `hangout_id`, …) so
    smoke tests can fill any route template from it, plus a `hangouts` map of
    the three lifecycle states.
    """
    from app.models import (
        Allergy,
        Drive,
        Hangout,
        HangoutInvite,
        HangoutStatus,
        Profile,
        Tag,
        YesNo,
    )

    tag = Tag(name="Core")
    allergy = Allergy(name="Peanuts")
    db.add_all([tag, allergy])
    db.flush()

    profile = Profile(
        name="Sam Rivera",
        phone="+15551110001",
        drinks=YesNo.yes,
        smokes=YesNo.no,
        drive=Drive.maybe,
    )
    profile.tags = [tag]
    profile.allergies = [allergy]
    db.add(profile)
    db.flush()

    hangouts: dict[str, int] = {}
    for state in (HangoutStatus.draft, HangoutStatus.active, HangoutStatus.closed):
        hangout = Hangout(
            status=state,
            motive=f"{state.value.title()} plans",
            day_date="2026-08-08",
            time="19:00",
            organizer_profile_id=profile.id,
            organizer_phone=profile.phone,
        )
        db.add(hangout)
        db.flush()
        db.add(HangoutInvite(hangout_id=hangout.id, profile_id=profile.id))
        hangouts[state.value] = hangout.id
    db.commit()

    return {
        "tag_id": tag.id,
        "allergy_id": allergy.id,
        "profile_id": profile.id,
        "hangout_id": hangouts["draft"],
        "hangouts": hangouts,
    }
