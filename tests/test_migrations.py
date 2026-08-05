"""Legacy-schema upgrades must not take dependent rows down with them."""

import sqlite3

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base
from app.models import Hangout, HangoutInvite, Profile, Tag

LEGACY_PROFILES = """
    PRAGMA foreign_keys=OFF;
    BEGIN;
    CREATE TABLE profiles_legacy (
        id INTEGER NOT NULL PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        phone VARCHAR(32) NOT NULL UNIQUE,
        drinks VARCHAR(16) NOT NULL DEFAULT 'unknown',
        smokes VARCHAR(16) NOT NULL DEFAULT 'unknown',
        food_allergies TEXT,
        car_access VARCHAR(16),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
    );
    INSERT INTO profiles_legacy (id, name, phone, drinks, smokes, food_allergies, car_access, created_at)
        SELECT id, name, phone, 'unknown', 'unknown', food_allergies, 'can_drive', created_at
        FROM profiles;
    DROP TABLE profiles;
    ALTER TABLE profiles_legacy RENAME TO profiles;
    COMMIT;
"""


def _counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("profiles", "hangout_invites", "profile_tags")
        }
    finally:
        conn.close()


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A populated database rewound to the pre-`drive` profiles schema."""
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record):
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = Session()
    profile = Profile(name="Ada", phone="+15551230001")
    profile.tags = [Tag(name="close")]
    session.add(profile)
    hangout = Hangout(motive="boardgames")
    session.add(hangout)
    session.flush()
    session.add(HangoutInvite(hangout_id=hangout.id, profile_id=profile.id))
    session.commit()
    session.close()
    engine.dispose()

    raw = sqlite3.connect(db_path)
    raw.executescript(LEGACY_PROFILES)
    raw.commit()
    raw.close()

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    yield db_path, engine
    engine.dispose()


def test_profiles_rebuild_keeps_dependent_rows(legacy_db):
    """`DROP TABLE profiles` must not cascade into invites and tag links.

    SQLite ignores `PRAGMA foreign_keys` inside a transaction, so a rebuild that
    disables enforcement from within one silently deletes every invite the
    upgrade was supposed to carry forward.
    """
    db_path, _ = legacy_db
    assert _counts(db_path) == {"profiles": 1, "hangout_invites": 1, "profile_tags": 1}

    database.init_db()

    assert _counts(db_path) == {"profiles": 1, "hangout_invites": 1, "profile_tags": 1}


def test_profiles_rebuild_migrates_car_access(legacy_db):
    _, engine = legacy_db
    database.init_db()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT drive FROM profiles")).scalar() == "yes"
        # 'unknown' is the legacy stand-in for "not answered".
        assert conn.execute(text("SELECT drinks FROM profiles")).scalar() is None


def test_foreign_keys_still_enforced_after_rebuild(legacy_db):
    """The rebuild turns enforcement off; later connections must get it back."""
    _, engine = legacy_db
    database.init_db()

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
