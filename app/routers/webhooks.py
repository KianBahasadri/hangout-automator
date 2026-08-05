from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services import process_inbound_sms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _twiml(message: str) -> Response:
    # Escape minimal XML special chars
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _valid_twilio_request(request: Request, form) -> bool:
    """Verify the X-Twilio-Signature header when the Twilio provider is active."""
    settings = get_settings()
    if settings.sms_provider != "twilio" or not settings.twilio_auth_token:
        return True
    from twilio.request_validator import RequestValidator

    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        return False
    return RequestValidator(settings.twilio_auth_token).validate(str(request.url), dict(form), signature)


@router.post("/sms")
async def inbound_sms(request: Request, db: Session = Depends(get_db)) -> Response:
    """
    Inbound SMS webhook.

    Twilio posts form fields From / Body.
    Local testing can POST JSON or form with from/body.
    """
    content_type = request.headers.get("content-type", "")

    phone, text = "", ""
    if "application/json" in content_type:
        data = await request.json()
        phone = data.get("From") or data.get("from") or ""
        text = data.get("Body") or data.get("body") or ""
    else:
        form = await request.form()
        if not _valid_twilio_request(request, form):
            logger.warning("Rejected webhook with invalid Twilio signature")
            return Response(status_code=403, content="Invalid Twilio signature")
        phone = str(form.get("From") or form.get("from") or "")
        text = str(form.get("Body") or form.get("body") or "")

    if not phone:
        return Response(status_code=400, content="Missing From")

    logger.info("Inbound SMS from=%s body=%s", phone, text)
    reply = process_inbound_sms(db, phone, text)
    return _twiml(reply)
