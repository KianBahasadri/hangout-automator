from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.event_logging import BodyCapture, audit_event
from app.services import process_inbound_sms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _twiml(message: str) -> Response:
    if not message:
        # Empty response: acknowledge the webhook without sending an SMS back.
        xml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        return Response(content=xml, media_type="application/xml")
    # Escape minimal XML special chars
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _canonical_webhook_url(request: Request) -> str:
    """Public URL Twilio calls, derived from PUBLIC_BASE_URL (reverse-proxy aware)."""
    base = get_settings().public_base_url.rstrip("/")
    url = f"{base}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return url


def _valid_twilio_request(request: Request, params: dict | str) -> bool:
    """Verify the X-Twilio-Signature header when the Twilio provider is active.

    params is the parsed form dict, or the raw JSON body string (Twilio signs
    JSON payloads via a bodySHA256 query parameter). Mock/local JSON testing
    skips the check because the provider is not twilio.
    """
    settings = get_settings()
    if (settings.sms_provider or "mock").lower().strip() != "twilio" or not settings.twilio_auth_token:
        return True
    from twilio.request_validator import RequestValidator

    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        return False
    if isinstance(params, str) and "bodySHA256" not in request.query_params:
        # Twilio always appends bodySHA256 when it signs a raw (JSON) body.
        # Without it the validator would try to treat the body string as a
        # params mapping and raise, so reject before calling it.
        return False
    return RequestValidator(settings.twilio_auth_token).validate(
        _canonical_webhook_url(request), params, signature
    )


@router.post("/sms")
async def inbound_sms(request: Request, db: Session = Depends(get_db)) -> Response:
    """
    Inbound SMS webhook.

    Twilio posts form fields From / Body (or JSON with a bodySHA256 query
    parameter). Local testing can POST JSON or form with from/body.
    """
    content_type = request.headers.get("content-type", "")

    phone, text = "", ""
    signature_valid: bool | None = None
    signature_required = (
        (get_settings().sms_provider or "mock").lower().strip() == "twilio"
        and bool(get_settings().twilio_auth_token)
    )
    if "application/json" in content_type:
        raw = await request.body()
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            captured = BodyCapture(get_settings().log_body_max_bytes)
            captured.add(raw)
            body_fields = captured.fields(content_type)
            audit_event(
                "sms.webhook.rejected",
                reason="invalid_json",
                content_type=content_type,
                raw_body=body_fields.get("body", ""),
                raw_body_bytes=body_fields["bytes"],
                raw_body_sha256=body_fields["sha256"],
                raw_body_truncated=body_fields["truncated"],
            )
            return Response(status_code=400, content="Invalid JSON")
        phone = str(data.get("From") or data.get("from") or "")
        text = str(data.get("Body") or data.get("body") or "")
        signature_valid = _valid_twilio_request(request, raw.decode("utf-8", "replace"))
        audit_event(
            "sms.webhook.received",
            content_type=content_type,
            from_phone=phone,
            body=text,
            signature_required=signature_required,
            signature_present=bool(request.headers.get("X-Twilio-Signature")),
            signature_valid=signature_valid,
        )
        if not signature_valid:
            logger.warning("Rejected webhook with invalid Twilio signature")
            audit_event(
                "sms.webhook.rejected",
                reason="invalid_twilio_signature",
                from_phone=phone,
                body=text,
                signature_required=signature_required,
            )
            return Response(status_code=403, content="Invalid Twilio signature")
    else:
        form = await request.form()
        phone = str(form.get("From") or form.get("from") or "")
        text = str(form.get("Body") or form.get("body") or "")
        signature_valid = _valid_twilio_request(request, dict(form))
        audit_event(
            "sms.webhook.received",
            content_type=content_type,
            from_phone=phone,
            body=text,
            signature_required=signature_required,
            signature_present=bool(request.headers.get("X-Twilio-Signature")),
            signature_valid=signature_valid,
        )
        if not signature_valid:
            logger.warning("Rejected webhook with invalid Twilio signature")
            audit_event(
                "sms.webhook.rejected",
                reason="invalid_twilio_signature",
                from_phone=phone,
                body=text,
                signature_required=signature_required,
            )
            return Response(status_code=403, content="Invalid Twilio signature")

    if not phone:
        audit_event(
            "sms.webhook.rejected",
            reason="missing_from",
            body=text,
            signature_required=signature_required,
            signature_valid=signature_valid,
        )
        return Response(status_code=400, content="Missing From")

    logger.info("Inbound SMS from=%s body=%s", phone, text)
    reply = process_inbound_sms(db, phone, text)
    audit_event(
        "sms.webhook.completed",
        from_phone=phone,
        body=text,
        reply=reply,
        signature_required=signature_required,
        signature_valid=signature_valid,
    )
    return _twiml(reply)
