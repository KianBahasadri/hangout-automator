from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.routers import api, web, webhooks
from app.services import process_followups, process_organizer_intervals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _job_followups() -> None:
    db = SessionLocal()
    try:
        n = process_followups(db)
        if n:
            logger.info("Sent %s follow-up SMS", n)
    except Exception:
        logger.exception("Follow-up job failed")
    finally:
        db.close()


def _job_organizer() -> None:
    db = SessionLocal()
    try:
        n = process_organizer_intervals(db)
        if n:
            logger.info("Sent %s organizer interval SMS", n)
    except Exception:
        logger.exception("Organizer interval job failed")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler.add_job(_job_followups, "interval", minutes=5, id="followups", replace_existing=True)
    scheduler.add_job(_job_organizer, "interval", minutes=10, id="organizer", replace_existing=True)
    scheduler.start()
    logger.info("Hangout Automator started (SMS provider=%s)", get_settings().sms_provider)
    yield
    scheduler.shutdown(wait=False)


_docs_enabled = get_settings().enable_api_docs

app = FastAPI(
    title="Hangout Automator",
    description="MVP: plan hangouts and invite people via SMS (no auth).",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(api.router)
app.include_router(webhooks.router)
app.include_router(web.router)
