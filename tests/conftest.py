import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='hangout-test-')}/test.db"
os.environ["SMS_PROVIDER"] = "mock"
os.environ["FOLLOWUP_HOURS"] = "1,2"
# Keep the application import hermetic when a developer's .env enables Clerk.
# Auth-specific tests construct their own enabled Settings and monkeypatch the
# middleware verifier explicitly.
os.environ["CLERK_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    init_db()
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
        # One-time bootstrap flags (not an ORM model); clear so the next
        # init_db() re-seeds default dietary restrictions for empty catalogs.
        conn.execute(text("DELETE FROM schema_flags"))


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
