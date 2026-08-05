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
    """Normalize to E.164-ish: digits with leading +. Bare 10-digit US → +1…"""
    cleaned = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+")
    # Drop duplicate leading pluses / keep digits after first +
    if "+" in cleaned:
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        cleaned = "+" + digits if digits else ""
    if cleaned and not cleaned.startswith("+") and len(cleaned) == 10:
        cleaned = "+1" + cleaned
    elif cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def format_phone(phone: str | None) -> str:
    """Pretty display form. NANP → +1 (XXX) XXX-XXXX; otherwise +digits."""
    if phone is None:
        return ""
    raw = str(phone).strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""

    # North American Numbering Plan
    if len(digits) == 10:
        a, b, c = digits[0:3], digits[3:6], digits[6:10]
        return f"+1 ({a}) {b}-{c}"
    if len(digits) == 11 and digits[0] == "1":
        a, b, c = digits[1:4], digits[4:7], digits[7:11]
        return f"+1 ({a}) {b}-{c}"

    # International / other: keep + and group remaining digits in threes
    return "+" + " ".join(digits[i : i + 3] for i in range(0, len(digits), 3))
