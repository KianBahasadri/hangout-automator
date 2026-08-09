"""The advisory lock must not be stranded by pool churn.

The sweeps commit mid-tick. A lock taken on the sweep session's connection
would be stranded: the commit returns that connection to the pool, another
session checks it out, and the unlock then runs on a different connection,
silently fails, and the lock survives for the life of the pooled connection.
The lock must live on a connection that is not returned to the pool until the
tick is over (see docs/background-jobs.md).
"""

from sqlalchemy import text

from app.database import SessionLocal, engine
from app.locks import JOB_KEYS, advisory_lock


def _advisory_lock_holders(key: int) -> list[int]:
    """Pids holding an advisory lock for `key` (classid 0 = single-key form)."""
    with engine.connect() as conn:
        return [
            row.pid
            for row in conn.execute(
                text(
                    "SELECT pid FROM pg_locks"
                    " WHERE locktype = 'advisory' AND classid = 0 AND objid = :key"
                ),
                {"key": key},
            )
        ]


def test_advisory_lock_is_released_after_tick_despite_pool_churn(db):
    """A tick that commits and churns the pool must not strand the lock."""
    key = JOB_KEYS["followups"]

    with advisory_lock(engine, key) as acquired:
        assert acquired is True

        # A sweep commits mid-tick; the session's connection returns to the pool.
        db.execute(text("SELECT 1"))
        db.commit()

        # Pool churn: competing sessions check out and return connections.
        for _ in range(5):
            other = SessionLocal()
            other.execute(text("SELECT 1"))
            other.commit()
            other.close()

        # Pin the pool's connections open while the tick ends, the way a burst
        # of other work would, so an unlock on a pooled connection cannot
        # borrow the lock-holding connection back.
        held = [engine.connect() for _ in range(5)]
        try:
            assert _advisory_lock_holders(key), "lock not visible during the tick"
        finally:
            for conn in held:
                conn.close()

    # After the tick the lock must be fully gone: no pid, any session.
    assert _advisory_lock_holders(key) == [], f"advisory lock {key} stranded after the tick"
