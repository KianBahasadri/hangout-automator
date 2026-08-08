from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.auth import ClerkAuthMiddleware
from app.database import SessionLocal, init_db
from app.event_logging import EventTraceMiddleware, audit_event, configure_logging, request_context
from app.routers import api, web, webhooks
from app.services import process_followups, process_organizer_intervals

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _job_followups() -> None:
    with request_context() as job_id:
        started = perf_counter()
        audit_event("background_job.started", job="followups", job_id=job_id)
        db = SessionLocal()
        try:
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    audit_event(
        "server.starting",
        host=settings.app_host,
        port=settings.app_port,
        sms_provider=settings.sms_provider,
        database_backend=settings.database_url.split(":", 1)[0],
        log_file=settings.log_file,
        log_level=settings.log_level,
    )
    try:
        init_db()
        audit_event(
            "database.initialized",
            database_backend=settings.database_url.split(":", 1)[0],
        )
        scheduler.add_job(
            _job_followups, "interval", minutes=5, id="followups", replace_existing=True
        )
        scheduler.add_job(
            _job_organizer,
            "interval",
            minutes=10,
            id="organizer",
            replace_existing=True,
        )
        scheduler.start()
        audit_event(
            "scheduler.started",
            jobs={"followups": "5 minutes", "organizer_intervals": "10 minutes"},
        )
        logger.info("Hangout Automator started (SMS provider=%s)", settings.sms_provider)
        audit_event("server.started")
    except Exception:
        audit_event("server.startup_failed", level=logging.CRITICAL, exc_info=True)
        raise

    yield

    audit_event("server.stopping")
    scheduler.shutdown(wait=False)
    audit_event("server.stopped")


_docs_enabled = settings.enable_api_docs

app = FastAPI(
    title="Hangout Automator",
    description="Plan hangouts and invite people via SMS with optional Clerk authentication.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Keep request tracing outermost so rejected Clerk requests are still visible
# in the structured audit log.
app.add_middleware(ClerkAuthMiddleware)
app.add_middleware(EventTraceMiddleware, body_limit=settings.log_body_max_bytes)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(api.router)
app.include_router(webhooks.router)
app.include_router(web.router)
