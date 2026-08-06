from functools import lru_cache
import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 9000
    database_url: str = "sqlite:///./hangout.db"
    public_base_url: str = "http://localhost:9000"

    # Interactive OpenAPI UI (/docs, /redoc, /openapi.json). The app has no
    # authentication, so deployments turn this off to avoid publishing a map of
    # the state-changing endpoints.
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
                    "SMS_PROVIDER=twilio requires all Twilio credentials: "
                    + ", ".join(missing)
                )
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
