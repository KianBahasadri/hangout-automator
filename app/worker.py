"""Background jobs in their own process.

The web process starts no scheduler. A separate `hangout-worker` process runs
this entry point so a second web process cannot double-send invites or
digests. Job bodies are the same ones that used to live in `app/main.py`,
including their request_context / audit_event instrumentation — that logging
is load-bearing (see docs/background-jobs.md).

Two layers of locking keep concurrent workers from double-sending: a Postgres
advisory lock around each tick (app/locks.py), and `FOR UPDATE SKIP LOCKED`
row claiming inside the sweeps themselves (app/services.py).
"""

from __future__ import annotations

import logging
from time import perf_counter

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.database import SessionLocal, engine
from app.event_logging import audit_event, configure_logging, request_context
from app.locks import JOB_KEYS, advisory_lock
from app.services import process_followups, process_organizer_intervals

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _job_followups() -> None:
    with request_context() as job_id:
        started = perf_counter()
        audit_event("background_job.started", job="followups", job_id=job_id)
        db = SessionLocal()
        try:
            with advisory_lock(engine, JOB_KEYS["followups"]) as locked:
                if not locked:
                    audit_event(
                        "background_job.skipped_locked",
                        job="followups",
                        job_id=job_id,
                        reason="advisory_lock_not_acquired",
                    )
                    logger.info("Skipping followups tick: another worker holds the lock")
                    return
                n = process_followups(db)
            logger.info("Sent %s follow-up SMS", n)
            audit_event(
                "background_job.completed",
                job="followups",
                job_id=job_id,
                sent_count=n,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
        except Exception:
            logger.exception("Follow-up job failed")
            audit_event(
                "background_job.failed",
                level=logging.ERROR,
                exc_info=True,
                job="followups",
                job_id=job_id,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
        finally:
            db.close()


def _job_organizer() -> None:
    with request_context() as job_id:
        started = perf_counter()
        audit_event("background_job.started", job="organizer_intervals", job_id=job_id)
        db = SessionLocal()
        try:
            with advisory_lock(engine, JOB_KEYS["organizer"]) as locked:
                if not locked:
                    audit_event(
                        "background_job.skipped_locked",
                        job="organizer_intervals",
                        job_id=job_id,
                        reason="advisory_lock_not_acquired",
                    )
                    logger.info("Skipping organizer tick: another worker holds the lock")
                    return
                n = process_organizer_intervals(db)
            logger.info("Sent %s organizer interval SMS", n)
            audit_event(
                "background_job.completed",
                job="organizer_intervals",
                job_id=job_id,
                sent_count=n,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
        except Exception:
            logger.exception("Organizer interval job failed")
            audit_event(
                "background_job.failed",
                level=logging.ERROR,
                exc_info=True,
                job="organizer_intervals",
                job_id=job_id,
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
        finally:
            db.close()


def main() -> None:
    scheduler = BlockingScheduler()
    scheduler.add_job(_job_followups, "interval", minutes=5, id="followups")
    scheduler.add_job(_job_organizer, "interval", minutes=10, id="organizer")
    logger.info("Hangout Automator worker started")
    audit_event(
        "worker.started",
        jobs={"followups": "5 minutes", "organizer_intervals": "10 minutes"},
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        audit_event("worker.stopped")


if __name__ == "__main__":
    main()
