import base64
import binascii
import os
from functools import lru_cache
import logging
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


_ENVIRONMENT_VARIABLE = "HANGOUT_ENV"
_ENVIRONMENT_FILES = {
    "development": ".env.development",
    "production": ".env.production",
}


def active_environment() -> str:
    """Return the explicit runtime environment, defaulting to local development."""
    environment = os.environ.get(_ENVIRONMENT_VARIABLE, "development").strip().lower()
    if environment not in _ENVIRONMENT_FILES:
        expected = ", ".join(sorted(_ENVIRONMENT_FILES))
        raise ValueError(f"{_ENVIRONMENT_VARIABLE} must be one of: {expected}")
    return environment


def environment_file(environment: str | None = None) -> Path:
    """Return the ignored dotenv file assigned to one runtime environment."""
    selected = environment or active_environment()
    try:
        return Path(_ENVIRONMENT_FILES[selected])
    except KeyError as exc:
        expected = ", ".join(sorted(_ENVIRONMENT_FILES))
        raise ValueError(f"{_ENVIRONMENT_VARIABLE} must be one of: {expected}") from exc


class Settings(BaseSettings):
    # get_settings() supplies the environment-specific dotenv file. Keeping the
    # model itself file-free makes direct Settings(...) use deterministic in tests.
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

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

    # Comma-separated emails guaranteed an `admin` access grant at startup.
    # Declarative and only ever additive, so it doubles as the way back in if
    # the last admin is ever removed: put the address here and restart. See
    # docs/tenancy.md.
    access_bootstrap_admins: str = ""

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
    # Rough $/message for Admin cost estimates from local message_logs.
    # Leave empty to show counts only. Not a live Twilio Usage API pull.
    twilio_sms_price_estimate: float | None = None

    # Optional labels + portal deep-links for Admin → Costs (no live billing API).
    azure_resource_group: str = ""
    azure_subscription_id: str = ""

    # Optional server-side key for Places API (New) location autocomplete.
    # Keeping this empty preserves the free-text location field.
    google_maps_api_key: str = ""

    # Up to two follow-up delays in hours after initial invite
    followup_hours: str = "24,48"
    organizer_interval_hours: int = 6
    max_followups: int = 2

    # SMS webhook rate limits (per fixed one-minute window). The webhook is
    # publicly reachable by design, so every hit can cost money; signature
    # verification still runs before the rate limiter, so unsigned floods die
    # at the cheaper check.
    sms_rate_limit_per_phone_per_minute: int = 30
    sms_rate_limit_global_per_minute: int = 300

    @field_validator("twilio_sms_price_estimate", mode="before")
    @classmethod
    def _empty_price_is_none(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

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
        if not self.clerk_secret_key.strip():
            # CLERK_JWT_KEY alone verifies sessions offline but cannot reach
            # Clerk's Backend API, and the access list is keyed by the user's
            # verified email, which only that API can supply. Without it every
            # signed-in user would be refused.
            logger.warning(
                "CLERK_SECRET_KEY is unset; the access list cannot resolve emails "
                "and every signed-in user will be refused"
            )
        return self

    @property
    def access_bootstrap_admin_list(self) -> list[str]:
        return [part.strip() for part in self.access_bootstrap_admins.split(",") if part.strip()]

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


def _clerk_frontend_host(publishable_key: str) -> str | None:
    """Decode the Clerk Frontend API host encoded in a publishable key."""
    for prefix in ("pk_test_", "pk_live_"):
        if publishable_key.startswith(prefix):
            encoded = publishable_key.removeprefix(prefix)
            break
    else:
        return None

    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8").rstrip("$")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _validate_runtime_clerk_environment(settings: Settings, environment: str) -> None:
    """Prevent a local process or a deploy from accidentally using the wrong instance."""
    if not settings.clerk_enabled:
        return

    expected_publishable_prefix = "pk_test_" if environment == "development" else "pk_live_"
    expected_secret_prefix = "sk_test_" if environment == "development" else "sk_live_"
    if not settings.clerk_publishable_key.startswith(expected_publishable_prefix):
        raise ValueError(
            f"HANGOUT_ENV={environment!r} requires a {expected_publishable_prefix} Clerk "
            "publishable key"
        )
    if settings.clerk_secret_key.strip() and not settings.clerk_secret_key.startswith(
        expected_secret_prefix
    ):
        raise ValueError(
            f"HANGOUT_ENV={environment!r} requires a {expected_secret_prefix} Clerk secret key"
        )

    encoded_host = _clerk_frontend_host(settings.clerk_publishable_key)
    configured_host = urlsplit(settings.clerk_frontend_api_url).hostname
    if not encoded_host:
        raise ValueError("CLERK_PUBLISHABLE_KEY must encode a Clerk Frontend API host")
    if not configured_host:
        raise ValueError("CLERK_FRONTEND_API_URL must be an absolute URL")
    if encoded_host != configured_host:
        raise ValueError(
            "CLERK_PUBLISHABLE_KEY and CLERK_FRONTEND_API_URL reference different Clerk instances"
        )


@lru_cache
def get_settings() -> Settings:
    environment = active_environment()
    settings = Settings(_env_file=environment_file(environment))
    _validate_runtime_clerk_environment(settings, environment)
    return settings
