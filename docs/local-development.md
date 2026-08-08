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

Creates `.venv` (uv or venv), installs `requirements.txt`, and starts the same
settings-based Uvicorn launcher with `--reload`.

Run the test suite (pytest, dev dependency group):

```bash
uv run --group dev pytest
```

What the suite covers and how it is structured: [testing.md](./testing.md).

Open `http://127.0.0.1:9000` by default. Interactive OpenAPI UI is at `/docs`
(served only when `ENABLE_API_DOCS` is true).

## Environment

Copy `.env.example` as needed. Settings are loaded by `app/config.py` (`pydantic-settings`).
Both local launch commands read `APP_HOST` and `APP_PORT` from `.env` (or from
the process environment), so changing `APP_PORT` changes the listening port.

| Setting | Env var | Default (code) |
|---------|---------|----------------|
| Bind host / port | `APP_HOST` / `APP_PORT` | `0.0.0.0` / `9000` |
| Database | `DATABASE_URL` | `sqlite:///./hangout.db` |
| Public URL | `PUBLIC_BASE_URL` | `http://localhost:9000` |
| OpenAPI UI | `ENABLE_API_DOCS` | `true` (deployments set `false`) |
| SMS provider | `SMS_PROVIDER` | `mock` (`mock` or `twilio`) |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | empty |
| Google Places autocomplete | `GOOGLE_MAPS_API_KEY` | empty (free-text location fallback) |
| Follow-up delays (hours) | `FOLLOWUP_HOURS` | `24,48` (first `max_followups` values used) |
| Organizer interval fallback | `ORGANIZER_INTERVAL_HOURS` | `6` |

`max_followups` is fixed at `2` in settings. Invalid follow-up hour parts become `24.0`; empty list falls back to `[24.0, 48.0]`.

`SMS_PROVIDER=twilio` without all three Twilio credentials is a startup error (fail-fast); `mock` is the default and needs nothing. `PUBLIC_BASE_URL` is used to validate Twilio webhook signatures when provider is twilio — see [sms-and-rsvp.md](./sms-and-rsvp.md).

When `GOOGLE_MAPS_API_KEY` is set, the new-hangout Location field uses the
Places API (New) through the app's server-side proxy. The key is never sent to
the browser. The app requests only autocomplete prediction text and Place
Details `formattedAddress` / `location`; without the key, the field remains
ordinary free text.

`.env`, `*.db`, `.venv`, and Terraform state are gitignored — do not put secrets in docs or commits.

## Startup side effects

On app start (`app/main.py` lifespan): `init_db()` runs, then the two APScheduler jobs register. Static files are mounted at `/static`.
