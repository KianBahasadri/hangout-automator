#!/usr/bin/env python3
"""One-shot SQLite → Postgres migration (operator step, not a deploy step).

Read-only on the source SQLite file:
  1. Makes a safe copy (online backup API, WAL included) — the source file is
     never opened for writing.
  2. Runs the legacy init_db() machinery (vendored verbatim from git 5891d75)
     against the *copy* to bring it to its final legacy schema.
  3. Exports every row in FK order and inserts into Postgres preserving ids.
  4. Resets Postgres sequences with setval, then prints a per-table
     source/destination row-count table. Exits non-zero on any mismatch.

`schema_flags` and `app_settings` are deliberately NOT migrated (schema_flags
is dropped by decision; app_settings is a legacy singleton nothing reads). Any
other table not in the expected set fails the run — a table added later cannot
be silently dropped on the floor.

Usage:
    uv run scripts/migrate_sqlite_to_postgres.py --dry-run [sqlite.db]
    uv run scripts/migrate_sqlite_to_postgres.py [--database-url URL] [sqlite.db]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, event, text

# Tables to migrate, in FK order. profile_tags / profile_allergies are M2M.
MIGRATE_ORDER = (
    "tags",
    "allergies",
    "profiles",
    "profile_tags",
    "profile_allergies",
    "hangouts",
    "hangout_invites",
    "message_logs",
)
SKIPPED_TABLES = ("schema_flags", "app_settings")

MODEL_TABLES = {
    "tags": "Tag",
    "allergies": "Allergy",
    "profiles": "Profile",
    "profile_tags": "profile_tags",
    "profile_allergies": "profile_allergies",
    "hangouts": "Hangout",
    "hangout_invites": "HangoutInvite",
    "message_logs": "MessageLog",
}


def _make_legacy_copy(source: Path) -> Path:
    """Online backup (WAL included) into a temp file; returns the copy path."""
    fd, copy_name = tempfile.mkstemp(prefix="hangout-sqlite-", suffix=".db")
    import os

    os.close(fd)
    copy_path = Path(copy_name)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(copy_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return copy_path


def _run_legacy_init(copy_path: Path) -> None:
    """Run the vendored legacy init_db() on the copy (see _legacy_sqlite_database.py)."""
    legacy_src = Path(__file__).with_name("_legacy_sqlite_database.py").read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(legacy_src, "_legacy_sqlite_database.py", "exec"), ns)  # noqa: S102

    copy_engine = create_engine(f"sqlite:///{copy_path}", connect_args={"check_same_thread": False})

    @event.listens_for(copy_engine, "connect")
    def _pragma(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")

    ns["engine"] = copy_engine
    ns["settings"] = SimpleNamespace(database_url=f"sqlite:///{copy_path}")
    ns["SessionLocal"] = ns["sessionmaker"](autocommit=False, autoflush=False, bind=copy_engine)
    try:
        ns["init_db"]()
    finally:
        copy_engine.dispose()


def _source_tables(copy_path: Path) -> list[str]:
    conn = sqlite3.connect(copy_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return sorted(row[0] for row in rows)
    finally:
        conn.close()


def _read_rows(copy_path: Path, table: str) -> list[dict]:
    conn = sqlite3.connect(copy_path)
    conn.row_factory = sqlite3.Row
    try:
        if table in ("profile_tags", "profile_allergies"):
            # M2M join tables have no id column.
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    finally:
        conn.close()


def _model_for(table: str):
    from app import models

    name = MODEL_TABLES[table]
    return getattr(models, name)


def _insert_rows(db_url: str, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    from sqlalchemy import insert

    model = _model_for(table)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(insert(model), rows)
    finally:
        engine.dispose()


def _reset_sequences(db_url: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for table in (
                "tags",
                "allergies",
                "profiles",
                "hangouts",
                "hangout_invites",
                "message_logs",
            ):
                conn.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence(:t, 'id'),"
                        " COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM " + table
                    ),
                    {"t": table},
                )
    finally:
        engine.dispose()


def _prepare_destination(db_url: str) -> None:
    """Verify the destination is a fresh, migrated database and clear the
    baseline seed rows. The SQLite export is the authority: the baseline
    migration seeds meat/pork for fresh installs, but a legacy SQLite file
    already carries its own meat/pork rows (possibly user-deleted, in which
    case they must stay deleted). Anything beyond the seeds means the
    destination was used already — refuse rather than mix data."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            seed_names = {"meat", "pork"}
            for table in MIGRATE_ORDER:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                if table == "allergies" and count:
                    names = {row[0] for row in conn.execute(text("SELECT name FROM allergies"))}
                    if not names <= seed_names:
                        raise SystemExit(
                            f"ERROR: destination table allergies has {sorted(names - seed_names)} "
                            "besides the baseline seed rows; refusing to migrate into a "
                            "non-fresh database."
                        )
                    conn.execute(text("DELETE FROM allergies"))
                    print(
                        "Cleared baseline seed rows from allergies (SQLite data is authoritative)."
                    )
                elif count:
                    raise SystemExit(
                        f"ERROR: destination table {table} already has {count} rows; "
                        "refusing to migrate into a non-fresh database."
                    )
    finally:
        engine.dispose()


def _destination_counts(db_url: str) -> dict[str, int]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return {
                table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                for table in MIGRATE_ORDER
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", nargs="?", default="hangout.db")
    parser.add_argument("--database-url", default=None, help="Destination Postgres URL")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; import nothing")
    args = parser.parse_args()

    if args.database_url is None:
        from app.config import get_settings

        args.database_url = get_settings().database_url

    source = Path(args.sqlite_path)
    if not source.is_file():
        print(f"ERROR: SQLite file not found: {source}", file=sys.stderr)
        return 2

    print(f"Source (read-only): {source}")

    copy_path = _make_legacy_copy(source)
    try:
        print(f"Legacy init_db() on copy: {copy_path}")
        _run_legacy_init(copy_path)

        tables = _source_tables(copy_path)
        unknown = set(tables) - set(MIGRATE_ORDER) - set(SKIPPED_TABLES)
        if unknown:
            print(
                f"ERROR: unaccounted source table(s) {sorted(unknown)} — "
                "add them to MIGRATE_ORDER or SKIPPED_TABLES before migrating.",
                file=sys.stderr,
            )
            return 1
        skipped = sorted(set(tables) & set(SKIPPED_TABLES))
        if skipped:
            print(f"Skipped by design (not migrated): {', '.join(skipped)}")

        source_counts = {table: len(_read_rows(copy_path, table)) for table in MIGRATE_ORDER}

        if args.dry_run:
            print("DRY RUN — no rows will be written to Postgres.")
            _print_count_table(source_counts, None)
            return 0

        _prepare_destination(args.database_url)
        for table in MIGRATE_ORDER:
            rows = _read_rows(copy_path, table)
            print(f"Importing {table}: {len(rows)} rows")
            _insert_rows(args.database_url, table, rows)
        _reset_sequences(args.database_url)
        dest_counts = _destination_counts(args.database_url)

        _print_count_table(source_counts, dest_counts)
        mismatches = {
            table for table in MIGRATE_ORDER if source_counts[table] != dest_counts[table]
        }
        if mismatches:
            print(f"ERROR: row-count mismatch on {sorted(mismatches)}", file=sys.stderr)
            return 1
        print("Migration complete: all row counts match.")
        return 0
    finally:
        copy_path.unlink(missing_ok=True)


def _print_count_table(source_counts: dict[str, int], dest_counts: dict[str, int] | None) -> None:
    width = max(len(t) for t in MIGRATE_ORDER)
    print(f"\n{'table':<{width}}  {'source':>7}  {'dest':>7}  {'ok':>3}")
    print("-" * (width + 22))
    all_ok = True
    for table in MIGRATE_ORDER:
        src = source_counts[table]
        if dest_counts is None:
            print(f"{table:<{width}}  {src:>7}  {'-':>7}  {'':>3}")
            continue
        dst = dest_counts[table]
        ok = src == dst
        all_ok = all_ok and ok
        print(f"{table:<{width}}  {src:>7}  {dst:>7}  {'OK' if ok else 'MISMATCH':>3}")
    if dest_counts is not None:
        print("-" * (width + 22))
        print("VERDICT: " + ("all counts match" if all_ok else "MISMATCHES PRESENT"))


if __name__ == "__main__":
    sys.exit(main())
