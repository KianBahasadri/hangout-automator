from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.auth import ClerkAuthMiddleware
from app.event_logging import EventTraceMiddleware, audit_event, configure_logging
from app.routers import api, web, webhooks

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _bootstrap_access(settings) -> None:
    """Seed admin grants, and say so loudly when nobody can get in.

    An empty access list with Clerk enabled locks everyone out, including the
    operator, and the symptom (a 403 on every page) does not name its cause.

    Nothing in here may stop the server. It is the first thing to touch the
    database, so a Postgres that is down — or a deploy that lands the code
    before `alembic upgrade head` — would otherwise turn a startup convenience
    into a boot loop that takes `/api/health` with it.
    """
    from app.access import admin_count, sync_bootstrap_admins
    from app.database import SessionLocal

    created = sync_bootstrap_admins(settings.access_bootstrap_admin_list)
    if created:
        audit_event("access.bootstrap_admins_granted", emails=created)

    try:
        db = SessionLocal()
        try:
            admins = admin_count(db)
        finally:
            db.close()
    except Exception:
        # "Could not count" is not "counted zero" — the same distinction the
        # middleware draws between a Clerk outage and a missing grant. Naming
        # the two likely causes matters: the raw traceback is a wall of
        # SQLAlchemy frames that never mentions Postgres or migrations.
        logger.exception(
            "Could not read the access list at startup, so sign-in will fail: the "
            "database is unreachable or its migrations have not been applied "
            "(alembic upgrade head)"
        )
        audit_event("access.bootstrap_unavailable", level=logging.ERROR)
        return

    if admins == 0:
        logger.error(
            "No admin access grants exist and ACCESS_BOOTSTRAP_ADMINS is empty — "
            "every signed-in user will be refused. Set ACCESS_BOOTSTRAP_ADMINS and restart."
        )
    audit_event("access.bootstrap_complete", admin_count=admins)


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
        if settings.clerk_enabled:
            _bootstrap_access(settings)
        logger.info("Hangout Automator started (SMS provider=%s)", settings.sms_provider)
        audit_event(
            "server.started",
            # Background jobs run in the separate `hangout-worker` process;
            # scheduling is external. A deploy without the worker is visible
            # here: followups/organizer events simply never appear.
            scheduling="external_worker",
            jobs={"followups": "5 minutes", "organizer_intervals": "10 minutes"},
        )
    except Exception:
        audit_event("server.startup_failed", level=logging.CRITICAL, exc_info=True)
        raise

    yield

    audit_event("server.stopping")
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
