from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.event_logging import audit_event

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

_PENDING_AUDIT_CHANGES = "hangout_pending_audit_changes"


def _audit_scalar(value):  # type: ignore[no-untyped-def]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _audit_scalar(enum_value)
    return str(value)


def _audit_model_change(obj, operation: str):  # type: ignore[no-untyped-def]
    state = inspect(obj)
    fields: dict[str, object] = {}
    for attribute in state.mapper.column_attrs:
        history = state.attrs[attribute.key].history
        if operation == "updated" and not history.has_changes():
            continue
        if operation == "updated":
            fields[attribute.key] = {
                "old": [_audit_scalar(value) for value in history.deleted],
                "new": [_audit_scalar(value) for value in history.added],
            }
        else:
            fields[attribute.key] = _audit_scalar(getattr(obj, attribute.key, None))
    if operation == "updated" and not fields:
        return None
    return {
        "model": type(obj).__name__,
        "operation": operation,
        "id": _audit_scalar(getattr(obj, "id", None)),
        "fields": fields,
    }


def _collect_audit_changes(session: Session) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for obj in session.new:
        change = _audit_model_change(obj, "created")
        if change:
            changes.append(change)
    for obj in session.dirty:
        if obj in session.new or obj in session.deleted:
            continue
        change = _audit_model_change(obj, "updated")
        if change:
            changes.append(change)
    for obj in session.deleted:
        change = _audit_model_change(obj, "deleted")
        if change:
            changes.append(change)
    return changes


@event.listens_for(Session, "before_commit")
def _audit_transaction_started(session: Session) -> None:
    changes = _collect_audit_changes(session)
    session.info[_PENDING_AUDIT_CHANGES] = changes
    audit_event(
        "database.transaction.commit_started",
        change_count=len(changes),
        changes=changes,
    )


@event.listens_for(Session, "after_commit")
def _audit_transaction_committed(session: Session) -> None:
    changes = session.info.pop(_PENDING_AUDIT_CHANGES, [])
    audit_event(
        "database.transaction.committed",
        change_count=len(changes),
        changes=changes,
    )


@event.listens_for(Session, "after_rollback")
def _audit_transaction_rolled_back(session: Session) -> None:
    changes = session.info.pop(_PENDING_AUDIT_CHANGES, [])
    audit_event(
        "database.transaction.rolled_back",
        attempted_change_count=len(changes),
        attempted_changes=changes,
    )


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Default dietary-restriction catalog entries (seeded once for an empty catalog).
DEFAULT_DIETARY_RESTRICTIONS = ("meat", "pork")
_DIETARY_DEFAULTS_SEEDED_FLAG = "dietary_defaults_seeded"


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()
    _ensure_schema_flags_table()
    _migrate_legacy_food_allergies()
    _ensure_default_dietary_restrictions()


def _table_cols(conn, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _ensure_schema_flags_table() -> None:
    """Lightweight key/value flags for one-time bootstrap steps."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_flags (
                    key VARCHAR(64) PRIMARY KEY NOT NULL,
                    value VARCHAR(256) NOT NULL
                )
                """
            )
        )


def _schema_flag_get(key: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM schema_flags WHERE key = :key"),
            {"key": key},
        ).fetchone()
    return row[0] if row else None


def _schema_flag_set(key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO schema_flags (key, value) VALUES (:key, :value)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            ),
            {"key": key, "value": value},
        )


def _ensure_default_dietary_restrictions() -> None:
    """Seed default dietary restrictions once; user deletions then persist.

    Fresh / empty catalogs get ``meat`` and ``pork``. After the one-time seed
    flag is set, missing defaults are not re-created on startup (PROF-8b).
    """
    from app.models import Allergy

    if _schema_flag_get(_DIETARY_DEFAULTS_SEEDED_FLAG):
        return

    db = SessionLocal()
    try:
        count = db.query(Allergy).count()
        if count == 0:
            for name in DEFAULT_DIETARY_RESTRICTIONS:
                db.add(Allergy(name=name))
            db.commit()
    finally:
        db.close()

    _schema_flag_set(_DIETARY_DEFAULTS_SEEDED_FLAG, "1")


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
                allergy = db.query(Allergy).filter(Allergy.name.ilike(name)).first()
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
            "location": "ALTER TABLE hangouts ADD COLUMN location VARCHAR(255)",
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
            "deleted_at": "ALTER TABLE hangouts ADD COLUMN deleted_at DATETIME",
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

    # Deliberately outside the block above: the rebuild has to run with foreign
    # keys off, which SQLite only honours when no transaction is open.
    rebuilt_profiles = _rebuild_profiles_if_needed()
    rebuilt_hangouts = _rebuild_hangouts_if_needed()

    with engine.begin() as conn:
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

    if rebuilt_profiles or rebuilt_hangouts:
        # Belt and braces: no pooled connection should survive a rebuild without
        # having run the connect-time PRAGMAs.
        engine.dispose()


def _rebuild_profiles_if_needed() -> bool:
    """Recreate profiles so optional enum columns are nullable (SQLite can't ALTER nullability).

    Runs on its own autocommit connection because `PRAGMA foreign_keys` is a
    silent no-op inside a transaction. With enforcement left on, `DROP TABLE
    profiles` performs an implicit `DELETE FROM` that cascades into invites,
    tag links, and allergy links — deleting the data this migration exists to
    preserve. The swap still gets an explicit transaction of its own so a crash
    mid-rebuild cannot leave the database without a profiles table.

    Returns True when the table was actually rebuilt.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(profiles)").fetchall()
        if not rows:
            return False
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
            return False

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

        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            conn.exec_driver_sql("BEGIN")
            conn.exec_driver_sql(
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
            conn.exec_driver_sql(
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
            conn.exec_driver_sql("DROP TABLE profiles")
            conn.exec_driver_sql("ALTER TABLE profiles_new RENAME TO profiles")
            conn.exec_driver_sql("COMMIT")
        except Exception:
            conn.exec_driver_sql("ROLLBACK")
            raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return True


def _rebuild_hangouts_if_needed() -> bool:
    """Recreate hangouts so alcohol/weed are nullable (SQLite can't ALTER nullability).

    Older DBs created alcohol_involved / weed_involved as NOT NULL (sometimes with
    a default of 'unknown'). The app now stores blank as NULL, so inserts with
    empty optional enums fail with IntegrityError until this rebuild runs.

    Same foreign-key / autocommit caveats as `_rebuild_profiles_if_needed`.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(hangouts)").fetchall()
        if not rows:
            return False
        by_name = {row[1]: row for row in rows}
        needs_rebuild = False
        for col in ("alcohol_involved", "weed_involved"):
            info = by_name.get(col)
            if info and info[3] == 1:  # notnull
                needs_rebuild = True
        if not needs_rebuild:
            return False

        def col_or(default: str, *names: str) -> str:
            for name in names:
                if name in by_name:
                    return name
            return default

        def yn(col: str) -> str:
            if col not in by_name:
                return "NULL"
            return f"CASE WHEN {col} IN ('', 'unknown') THEN NULL ELSE {col} END"

        def bool_col(col: str, default: str) -> str:
            return col if col in by_name else default

        def int_col(col: str, default: str) -> str:
            return col if col in by_name else default

        select_cols = f"""
            id,
            {col_or("NULL", "day_date")} AS day_date,
            {col_or("NULL", "time")} AS time,
            {col_or("NULL", "duration")} AS duration,
            {col_or("NULL", "location")} AS location,
            {col_or("NULL", "motive")} AS motive,
            {yn("alcohol_involved")} AS alcohol_involved,
            {yn("weed_involved")} AS weed_involved,
            {col_or("NULL", "notes")} AS notes,
            COALESCE({col_or("'draft'", "status")}, 'draft') AS status,
            {col_or("NULL", "organizer_profile_id")} AS organizer_profile_id,
            {col_or("NULL", "organizer_phone")} AS organizer_phone,
            COALESCE({bool_col("notify_enabled", "0")}, 0) AS notify_enabled,
            COALESCE({bool_col("notify_interval", "0")}, 0) AS notify_interval,
            COALESCE({bool_col("notify_threshold", "0")}, 0) AS notify_threshold,
            COALESCE({int_col("notify_interval_hours", "6")}, 6) AS notify_interval_hours,
            COALESCE({bool_col("notify_interval_only_if_changed", "1")}, 1)
                AS notify_interval_only_if_changed,
            {col_or("NULL", "last_digest_fingerprint")} AS last_digest_fingerprint,
            COALESCE({bool_col("notify_on_new_confirm", "1")}, 1) AS notify_on_new_confirm,
            COALESCE({bool_col("notify_on_decline", "0")}, 0) AS notify_on_decline,
            COALESCE({bool_col("notify_on_allergy", "1")}, 1) AS notify_on_allergy,
            COALESCE({bool_col("notify_on_ride_needed", "1")}, 1) AS notify_on_ride_needed,
            COALESCE({int_col("notify_confirm_goal", "0")}, 0) AS notify_confirm_goal,
            COALESCE({bool_col("notify_confirm_goal_sent", "0")}, 0) AS notify_confirm_goal_sent,
            COALESCE({int_col("notify_threshold_cooldown_minutes", "0")}, 0)
                AS notify_threshold_cooldown_minutes,
            {col_or("NULL", "last_organizer_notify_at")} AS last_organizer_notify_at,
            {col_or("NULL", "activated_at")} AS activated_at,
            created_at
        """

        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            conn.exec_driver_sql("BEGIN")
            conn.exec_driver_sql(
                """
                CREATE TABLE hangouts_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    day_date VARCHAR(64),
                    time VARCHAR(64),
                    duration VARCHAR(64),
                    location VARCHAR(255),
                    motive VARCHAR(255),
                    alcohol_involved VARCHAR(16),
                    weed_involved VARCHAR(16),
                    notes TEXT,
                    status VARCHAR(16) NOT NULL,
                    organizer_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
                    organizer_phone VARCHAR(32),
                    notify_enabled BOOLEAN NOT NULL DEFAULT 0,
                    notify_interval BOOLEAN NOT NULL DEFAULT 0,
                    notify_threshold BOOLEAN NOT NULL DEFAULT 0,
                    notify_interval_hours INTEGER NOT NULL DEFAULT 6,
                    notify_interval_only_if_changed BOOLEAN NOT NULL DEFAULT 1,
                    last_digest_fingerprint VARCHAR(512),
                    notify_on_new_confirm BOOLEAN NOT NULL DEFAULT 1,
                    notify_on_decline BOOLEAN NOT NULL DEFAULT 0,
                    notify_on_allergy BOOLEAN NOT NULL DEFAULT 1,
                    notify_on_ride_needed BOOLEAN NOT NULL DEFAULT 1,
                    notify_confirm_goal INTEGER NOT NULL DEFAULT 0,
                    notify_confirm_goal_sent BOOLEAN NOT NULL DEFAULT 0,
                    notify_threshold_cooldown_minutes INTEGER NOT NULL DEFAULT 0,
                    last_organizer_notify_at DATETIME,
                    activated_at DATETIME,
                    deleted_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """
            )
            # select_cols ends with activated_at, created_at — insert deleted_at from source when present
            deleted_expr = "deleted_at" if "deleted_at" in by_name else "NULL"
            # Rebuild select_cols to place deleted_at before created_at
            select_cols_with_deleted = select_cols.rsplit(",\n            created_at", 1)[0] + (
                f",\n            {deleted_expr} AS deleted_at,\n            created_at"
            )
            conn.exec_driver_sql(
                f"""
                INSERT INTO hangouts_new (
                    id, day_date, time, duration, location, motive,
                    alcohol_involved, weed_involved, notes, status,
                    organizer_profile_id, organizer_phone,
                    notify_enabled, notify_interval, notify_threshold,
                    notify_interval_hours, notify_interval_only_if_changed,
                    last_digest_fingerprint,
                    notify_on_new_confirm, notify_on_decline, notify_on_allergy,
                    notify_on_ride_needed, notify_confirm_goal, notify_confirm_goal_sent,
                    notify_threshold_cooldown_minutes, last_organizer_notify_at,
                    activated_at, deleted_at, created_at
                )
                SELECT {select_cols_with_deleted}
                FROM hangouts
                """
            )
            conn.exec_driver_sql("DROP TABLE hangouts")
            conn.exec_driver_sql("ALTER TABLE hangouts_new RENAME TO hangouts")
            conn.exec_driver_sql("COMMIT")
        except Exception:
            conn.exec_driver_sql("ROLLBACK")
            raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return True
