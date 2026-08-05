# Local development

## Stack

- Python `>=3.12,<3.14` (local script prefers 3.12 for wheel availability)
- FastAPI, Uvicorn, Jinja2, SQLAlchemy 2, Pydantic Settings
- APScheduler, Twilio SDK, httpx, python-multipart
- Package name `hangout-automator` `0.1.0` (`pyproject.toml`)

## Run

Preferred:

```bash
uv run dev
```

Runs Uvicorn on `app.main:app` with reload, host/port from settings (default `0.0.0.0:9000`).

Also:

```bash
./scripts/run_local.sh
```

Creates `.venv` (uv or venv), installs `requirements.txt`, sets local defaults, starts Uvicorn on `0.0.0.0:9000` with `--reload`.

Open `http://127.0.0.1:9000`. Interactive OpenAPI UI is at `/docs`.

## Environment

Copy `.env.example` as needed. Settings are loaded by `app/config.py` (`pydantic-settings`).

| Setting | Env var | Default (code) |
|---------|---------|----------------|
| Bind host / port | `APP_HOST` / `APP_PORT` | `0.0.0.0` / `9000` |
| Database | `DATABASE_URL` | `sqlite:///./hangout.db` |
| Public URL | `PUBLIC_BASE_URL` | `http://localhost:9000` |
| SMS provider | `SMS_PROVIDER` | `mock` (`mock` or `twilio`) |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | empty |
| Follow-up delays (hours) | `FOLLOWUP_HOURS` | `24,48` (first `max_followups` values used) |
| Organizer interval fallback | `ORGANIZER_INTERVAL_HOURS` | `6` |

`max_followups` is fixed at `2` in settings. Invalid follow-up hour parts become `24.0`; empty list falls back to `[24.0, 48.0]`.

`.env`, `*.db`, `.venv`, and Terraform state are gitignored — do not put secrets in docs or commits.

## Startup side effects

On app start (`app/main.py` lifespan): `init_db()` runs, then the two APScheduler jobs register. Static files are mounted at `/static`.
