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

from sqlalchemy import Engine, text


# Stable integer keys per job name. These are persisted in audit events and
# must never change or be reused for a different job.
JOB_KEYS: dict[str, int] = {
    "followups": 9001,
    "organizer": 9002,
}


@contextmanager
def advisory_lock(engine: Engine, key: int) -> Iterator[bool]:
    """Yield True when this process acquired the advisory lock for `key`.

    The caller is expected to skip the tick and emit an audit event when the
    yield value is False — some other worker is already running it.

    The lock is acquired and released on a dedicated connection that is NOT
    returned to the pool for the duration of the tick. A lock taken on the
    sweep session's own connection would be stranded: the sweeps commit
    mid-tick, returning that connection to the pool; another session checks
    it out; and the unlock then runs on a different connection, silently
    fails, and the lock survives for the life of the pooled connection. This
    is liveness, not safety — `FOR UPDATE SKIP LOCKED` still prevents
    double-sends — but a stranded lock starves every later tick.

    `pg_try_advisory_xact_lock` is deliberately NOT used: it releases at the
    first COMMIT inside the sweep, which is exactly what must not happen.
    """
    with engine.connect() as conn:
        acquired = bool(
            conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        )
        # The lock is session-scoped, so ending the transaction leaves it
        # held; do not sit idle-in-transaction for the whole tick.
        conn.rollback()
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
