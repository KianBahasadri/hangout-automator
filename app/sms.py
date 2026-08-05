from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class SmsProvider(ABC):
    @abstractmethod
    def send(self, to: str, body: str) -> tuple[bool, str | None]:
        """Return (success, error_message)."""


class MockSmsProvider(SmsProvider):
    def send(self, to: str, body: str) -> tuple[bool, str | None]:
        logger.info("[MOCK SMS] to=%s body=%s", to, body)
        print(f"\n{'=' * 60}\n[MOCK SMS] → {to}\n{body}\n{'=' * 60}\n", flush=True)
        return True, None


class TwilioSmsProvider(SmsProvider):
    def __init__(self, settings: Settings) -> None:
        from twilio.rest import Client

        if not settings.twilio_account_sid or not settings.twilio_auth_token or not settings.twilio_from_number:
            raise ValueError("Twilio credentials incomplete (SID, token, from number required)")
        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._from = settings.twilio_from_number

    def send(self, to: str, body: str) -> tuple[bool, str | None]:
        try:
            self._client.messages.create(to=to, from_=self._from, body=body)
            return True, None
        except Exception as exc:  # noqa: BLE001 — surface provider errors to UI
            logger.exception("Twilio send failed to %s", to)
            return False, str(exc)


def get_sms_provider(settings: Settings | None = None) -> SmsProvider:
    settings = settings or get_settings()
    provider = (settings.sms_provider or "mock").lower().strip()
    if provider == "twilio":
        return TwilioSmsProvider(settings)
    return MockSmsProvider()


def normalize_phone(phone: str) -> str:
    """Light normalization: strip spaces/dashes; keep leading +."""
    cleaned = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+")
    if cleaned and not cleaned.startswith("+") and len(cleaned) == 10:
        # Assume US if 10 digits without country code
        cleaned = "+1" + cleaned
    elif cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned
