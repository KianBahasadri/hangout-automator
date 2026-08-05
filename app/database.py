from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()
    _migrate_legacy_food_allergies()


def _table_cols(conn, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _migrate_legacy_food_allergies() -> None:
    """Split free-text profile.food_allergies into Allergy catalog + M2M links."""
    if not settings.database_url.startswith("sqlite"):
        return
    from app.models import Allergy, Profile

    def _norm(name: str) -> str:
        return " ".join((name or "").strip().split())

    db = SessionLocal()
    try:
        profiles = db.query(Profile).filter(Profile.food_allergies.isnot(None)).all()
        changed = False
        for profile in profiles:
            raw = (profile.food_allergies or "").strip()
            if not raw:
                continue
            # Already linked via catalog — clear legacy text
            if profile.allergies:
                profile.food_allergies = None
                changed = True
                continue
            names = [_norm(part) for part in raw.replace(";", ",").split(",")]
            names = [n for n in names if n]
            if not names:
                profile.food_allergies = None
                changed = True
                continue
            linked: list[Allergy] = []
            for name in names:
                allergy = (
                    db.query(Allergy)
                    .filter(Allergy.name.ilike(name))
                    .first()
                )
                if not allergy:
                    allergy = Allergy(name=name)
                    db.add(allergy)
                    db.flush()
                linked.append(allergy)
            profile.allergies = linked
            profile.food_allergies = None
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()


def _ensure_sqlite_columns() -> None:
    """Add columns introduced after initial create_all (SQLite has no migrations)."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        hangout_cols = _table_cols(conn, "hangouts")
        alters = {
            "weed_involved": "ALTER TABLE hangouts ADD COLUMN weed_involved VARCHAR(16)",
            "organizer_profile_id": (
                "ALTER TABLE hangouts ADD COLUMN organizer_profile_id INTEGER "
                "REFERENCES profiles(id) ON DELETE SET NULL"
            ),
            "notify_interval_hours": (
                "ALTER TABLE hangouts ADD COLUMN notify_interval_hours INTEGER NOT NULL DEFAULT 6"
            ),
            "notify_interval_only_if_changed": (
                "ALTER TABLE hangouts ADD COLUMN notify_interval_only_if_changed "
                "BOOLEAN NOT NULL DEFAULT 1"
            ),
            "last_digest_fingerprint": (
                "ALTER TABLE hangouts ADD COLUMN last_digest_fingerprint VARCHAR(512)"
            ),
            "notify_on_new_confirm": (
                "ALTER TABLE hangouts ADD COLUMN notify_on_new_confirm BOOLEAN NOT NULL DEFAULT 1"
            ),
            "notify_on_decline": (
                "ALTER TABLE hangouts ADD COLUMN notify_on_decline BOOLEAN NOT NULL DEFAULT 0"
            ),
            "notify_on_allergy": (
                "ALTER TABLE hangouts ADD COLUMN notify_on_allergy BOOLEAN NOT NULL DEFAULT 1"
            ),
            "notify_on_ride_needed": (
                "ALTER TABLE hangouts ADD COLUMN notify_on_ride_needed BOOLEAN NOT NULL DEFAULT 1"
            ),
            "notify_confirm_goal": (
                "ALTER TABLE hangouts ADD COLUMN notify_confirm_goal INTEGER NOT NULL DEFAULT 0"
            ),
            "notify_confirm_goal_sent": (
                "ALTER TABLE hangouts ADD COLUMN notify_confirm_goal_sent BOOLEAN NOT NULL DEFAULT 0"
            ),
            "notify_threshold_cooldown_minutes": (
                "ALTER TABLE hangouts ADD COLUMN notify_threshold_cooldown_minutes "
                "INTEGER NOT NULL DEFAULT 0"
            ),
        }
        for col, sql in alters.items():
            if col not in hangout_cols:
                conn.execute(text(sql))

        profile_cols = _table_cols(conn, "profiles")
        if "drive" not in profile_cols:
            conn.execute(text("ALTER TABLE profiles ADD COLUMN drive VARCHAR(16)"))
            profile_cols.add("drive")
        if "car_access" in profile_cols:
            conn.execute(
                text(
                    """
                    UPDATE profiles SET drive = CASE car_access
                        WHEN 'can_drive' THEN 'yes'
                        WHEN 'cannot' THEN 'no'
                        WHEN 'maybe' THEN 'maybe'
                        ELSE NULL
                    END
                    WHERE drive IS NULL
                    """
                )
            )

        _rebuild_profiles_if_needed(conn)

        # Prefer blank (NULL) over legacy "unknown"
        for table, cols in (
            ("profiles", ("drinks", "smokes")),
            ("hangouts", ("alcohol_involved", "weed_involved")),
        ):
            existing = _table_cols(conn, table)
            for col in cols:
                if col not in existing:
                    continue
                try:
                    conn.execute(text(f"UPDATE {table} SET {col} = NULL WHERE {col} = 'unknown'"))
                except Exception:
                    pass


def _rebuild_profiles_if_needed(conn) -> None:  # type: ignore[no-untyped-def]
    """Recreate profiles so optional enum columns are nullable (SQLite can't ALTER nullability)."""
    rows = conn.execute(text("PRAGMA table_info(profiles)")).fetchall()
    if not rows:
        return
    # row: (cid, name, type, notnull, dflt_value, pk)
    by_name = {row[1]: row for row in rows}
    needs_rebuild = False
    for col in ("drinks", "smokes"):
        info = by_name.get(col)
        if info and info[3] == 1:  # notnull
            needs_rebuild = True
    if "car_access" in by_name:
        needs_rebuild = True
    if not needs_rebuild:
        return

    has_car = "car_access" in by_name
    has_drive = "drive" in by_name
    if has_car and has_drive:
        drive_expr = """COALESCE(
            NULLIF(drive, ''),
            CASE car_access
                WHEN 'can_drive' THEN 'yes'
                WHEN 'cannot' THEN 'no'
                WHEN 'maybe' THEN 'maybe'
                ELSE NULL
            END
        )"""
    elif has_drive:
        drive_expr = "NULLIF(drive, '')"
    elif has_car:
        drive_expr = """CASE car_access
            WHEN 'can_drive' THEN 'yes'
            WHEN 'cannot' THEN 'no'
            WHEN 'maybe' THEN 'maybe'
            ELSE NULL
        END"""
    else:
        drive_expr = "NULL"

    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE profiles_new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                phone VARCHAR(32) NOT NULL UNIQUE,
                drinks VARCHAR(16),
                smokes VARCHAR(16),
                food_allergies TEXT,
                drive VARCHAR(16),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            INSERT INTO profiles_new (id, name, phone, drinks, smokes, food_allergies, drive, created_at)
            SELECT
                id,
                name,
                phone,
                CASE WHEN drinks IN ('', 'unknown') THEN NULL ELSE drinks END,
                CASE WHEN smokes IN ('', 'unknown') THEN NULL ELSE smokes END,
                food_allergies,
                {drive_expr},
                created_at
            FROM profiles
            """
        )
    )
    conn.execute(text("DROP TABLE profiles"))
    conn.execute(text("ALTER TABLE profiles_new RENAME TO profiles"))
    conn.execute(text("PRAGMA foreign_keys=ON"))
