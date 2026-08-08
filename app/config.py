from functools import lru_cache
import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 9000
    database_url: str = "postgresql+psycopg://hangout:hangout@localhost:5432/hangout"
    public_base_url: str = "http://localhost:9000"

    # Clerk authentication is opt-in so a fresh local checkout remains usable
    # before a Clerk application has been created. When enabled, all app and
    # JSON API routes require a verified Clerk session; the SMS webhook and
    # health check remain public server-to-server endpoints.
    clerk_enabled: bool = False
    clerk_publishable_key: str = ""
    clerk_frontend_api_url: str = ""
    clerk_secret_key: str = ""
    clerk_jwt_key: str = ""
    # Comma-separated browser origins allowed by Clerk's authorized-party
    # (azp) check. An empty value falls back to PUBLIC_BASE_URL.
    clerk_authorized_parties: str = ""

    # Interactive OpenAPI UI (/docs, /redoc, /openapi.json). When Clerk is
    # enabled, these routes are protected by the same auth middleware.
    enable_api_docs: bool = True

    # The application writes a structured JSONL audit stream in addition to
    # the console/journal stream. Deployments place this on the persistent data
    # disk; local development keeps it under the repository's logs/ directory.
    log_file: str = "logs/server.log"
    log_level: str = "INFO"
    log_max_bytes: int = 50_000_000
    log_backup_count: int = 10
    log_body_max_bytes: int = 262_144

    sms_provider: str = "mock"  # mock | twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Optional server-side key for Places API (New) location autocomplete.
    # Keeping this empty preserves the free-text location field.
    google_maps_api_key: str = ""

    # Up to two follow-up delays in hours after initial invite
    followup_hours: str = "24,48"
    organizer_interval_hours: int = 6
    max_followups: int = 2

    @model_validator(mode="after")
    def _validate_sms_config(self) -> "Settings":
        provider = (self.sms_provider or "mock").lower().strip()
        if provider not in ("mock", "twilio"):
            raise ValueError(
                f"Invalid SMS_PROVIDER {self.sms_provider!r} — must be 'mock' or 'twilio'"
            )
        if provider == "twilio":
            missing = [
                name
                for name, value in (
                    ("TWILIO_ACCOUNT_SID", self.twilio_account_sid),
                    ("TWILIO_AUTH_TOKEN", self.twilio_auth_token),
                    ("TWILIO_FROM_NUMBER", self.twilio_from_number),
                )
                if not (value or "").strip()
            ]
            if missing:
                raise ValueError(
                    "SMS_PROVIDER=twilio requires all Twilio credentials: " + ", ".join(missing)
                )
        return self

    @model_validator(mode="after")
    def _validate_clerk_config(self) -> "Settings":
        if not self.clerk_enabled:
            return self

        missing = [
            name
            for name, value in (
                ("CLERK_PUBLISHABLE_KEY", self.clerk_publishable_key),
                ("CLERK_FRONTEND_API_URL", self.clerk_frontend_api_url),
            )
            if not (value or "").strip()
        ]
        if not self.clerk_secret_key.strip() and not self.clerk_jwt_key.strip():
            missing.append("CLERK_SECRET_KEY or CLERK_JWT_KEY")
        if missing:
            raise ValueError("CLERK_ENABLED=true requires: " + ", ".join(missing))
        return self

    @property
    def followup_hour_list(self) -> list[float]:
        parts = [p.strip() for p in self.followup_hours.split(",") if p.strip()]
        hours: list[float] = []
        for p in parts[: self.max_followups]:
            try:
                hours.append(float(p))
            except ValueError:
                logger.warning("Invalid followup hour %r — using 24", p)
                hours.append(24.0)
        return hours or [24.0, 48.0]

    @property
    def clerk_authorized_party_list(self) -> list[str]:
        """Return normalized origins for Clerk's authorized-party check."""
        configured = [
            part.strip().rstrip("/")
            for part in self.clerk_authorized_parties.split(",")
            if part.strip()
        ]
        return configured or [self.public_base_url.strip().rstrip("/")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
