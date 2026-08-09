# Local development

## Stack

- Python `>=3.12,<3.14` (local script prefers 3.12 for wheel availability)
- FastAPI, Uvicorn, Jinja2, SQLAlchemy 2, Pydantic Settings
- APScheduler, Twilio SDK, Clerk backend SDK, httpx, python-multipart
- Package name `hangout-automator` `0.1.0` (`pyproject.toml`)

## Database (Postgres via Docker)

Local development uses PostgreSQL in Docker Compose — SQLite is not supported.
Start it once (requires Docker):

```bash
./scripts/db_up.sh
```

The compose `postgres:17` service listens on `localhost:5432` with database
`hangout` and `hangout_test` (the test suite's database). The default
`DATABASE_URL` already points at it. Apply schema migrations before running the
app:

```bash
uv run alembic upgrade head
```

## Run

Preferred:

```bash
uv run dev
```

Runs Uvicorn on `app.main:app` with reload, host/port from settings (default `0.0.0.0:9000`).

Background jobs run in a second, separate process — start it in its own
terminal, since both are long-running:

```bash
uv run worker
```

Without it the app serves normally but no follow-up or organizer SMS is ever
sent (see [background-jobs.md](./background-jobs.md)).

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
| Database | `DATABASE_URL` | `postgresql+psycopg://hangout:hangout@localhost:5432/hangout` |
| Public URL | `PUBLIC_BASE_URL` | `http://localhost:9000` |
| Clerk auth switch | `CLERK_ENABLED` | `false` |
| Clerk browser key | `CLERK_PUBLISHABLE_KEY` | empty |
| Clerk frontend API URL | `CLERK_FRONTEND_API_URL` | empty |
| Clerk backend verification | `CLERK_SECRET_KEY` or `CLERK_JWT_KEY` | empty |
| Clerk authorized origins | `CLERK_AUTHORIZED_PARTIES` | `PUBLIC_BASE_URL` when empty |
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

To enable Clerk locally, create an application in the Clerk Dashboard and set
`CLERK_ENABLED=true`, its publishable key, frontend API URL, and either the
backend secret key or PEM JWT key. `CLERK_AUTHORIZED_PARTIES` is a
comma-separated list of browser origins such as
`http://localhost:9000,https://app.example.com`; the verifier checks the
session token's authorized-party claim against this list. With Clerk enabled,
the UI and `/api/*` routes require a session, while `/api/health`, static
assets, and `/sign-in` remain reachable for health checks and browser
bootstrapping. `POST /webhooks/sms` is public only when
`SMS_PROVIDER=twilio`; in that mode its Twilio signature validation is the
integration authentication layer. A mock-provider webhook remains protected
by Clerk.

The sign-in page mounts Clerk's browser component and safely returns to the
original internal path after login. Authenticated pages mount Clerk's user
button, which supplies the sign-out action; no application-specific password or
logout endpoint is stored in this repo.

`.env`, `*.db`, `.venv`, and Terraform state are gitignored — do not put secrets in docs or commits.

## Startup side effects

On app start (`app/main.py` lifespan): audit events are emitted and the
scheduler registers (a separate worker process runs it in deployments — see
[background-jobs.md](./background-jobs.md)). **No `init_db()`**: schema
migrations are an explicit `alembic upgrade head` step, never an app-startup
side effect. Static files are mounted at `/static`.
