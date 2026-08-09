"""Cross-process job coordination with Postgres advisory locks.

Layer 1 of the send-locking model (layer 2 is `FOR UPDATE SKIP LOCKED` inside
the sweeps themselves, see app/services.py). An advisory lock around each job
tick makes N worker processes run a given sweep at most once, even when they
start at the same moment.

The lock is session-scoped: held until explicit unlock or session close, and
never released by COMMIT/ROLLBACK inside the tick.
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

# Stable integer keys per job name. These are persisted in audit events and
# must never change or be reused for a different job.
JOB_KEYS: dict[str, int] = {
    "followups": 9001,
    "organizer": 9002,
}


@contextmanager
def advisory_lock(session: Session, key: int) -> Iterator[bool]:
    """Yield True when this process acquired the advisory lock for `key`.

    The caller is expected to skip the tick and emit an audit event when the
    yield value is False — some other worker is already running it.
    """
    acquired = bool(
        session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
    )
    try:
        yield acquired
    finally:
        if acquired:
            session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
