# Overview

Hangout Automator is a multi-tenant FastAPI app: every tenant row belongs to a
`workspace`, and each authenticated user sees only their own workspace's
profiles and hangouts ([tenancy.md](./tenancy.md)). When `CLERK_ENABLED=true`,
its browser UI and JSON API require a verified Clerk session and resolve the
request to the user's workspace (provisioned on first use). With
`CLERK_ENABLED=false` everything runs in the seeded `default` workspace, which
is how local development behaves until Clerk is configured.

## Runtime shape

- **Web UI** — Jinja templates served by `app/routers/web.py`
- **JSON API** — `app/routers/api.py` under `/api` (also powers profile autosave)
- **Authentication** — `app/auth.py` verifies Clerk sessions in middleware; `/sign-in` is rendered with ClerkJS when enabled
- **SMS webhook** — `POST /webhooks/sms` in `app/routers/webhooks.py`
- **Background jobs** — separate `hangout-worker` process, follow-ups every 5 minutes, organizer interval digests every 10 minutes ([background-jobs.md](./background-jobs.md))
- **Persistence** — PostgreSQL via SQLAlchemy + Alembic (`DATABASE_URL`)

Entry points, all in `app/server.py`: settings-aware launcher
`python -m app.server` (production), console script `dev` → `app.server:dev`
(reload), and the ASGI app `app.main:app`. The worker is separate:
console script `worker` → `app.worker:main`.

## Package layout

```
app/
  main.py           FastAPI app, static mount, auth middleware, audit lifespan
  server.py         settings-aware Uvicorn launchers (`dev` and `main`)
  worker.py         background jobs entry point (hangout-worker process)
  tenancy.py        workspace resolution + scoped queries
  locks.py          Postgres advisory locks for the worker sweeps
  event_logging.py  JSONL audit file, correlation IDs, HTTP trace middleware
  config.py         pydantic-settings
  database.py       engine, sessions, audit listeners
  ids.py            row-id bounds for paths, payloads, and form fields
  models.py         ORM + enums
  schemas.py        API Pydantic models
  services.py       invites, RSVP, follow-ups, organizer SMS, row claiming
  messages.py       SMS copy + reply parse
  sms.py            mock/Twilio + phone normalize
  routers/          api, web, webhooks
  templates/        Jinja pages
  static/           CSS, JS, icons
migrations/         Alembic schema migrations (versions/)
docs/               topic docs (archive/ is historical)
tests/              pytest suite + generated route/form smoke matrix
terraform/          Azure VM + Flexible Server + cloud-init
scripts/            db_up.sh, check.sh, deploy/
```

Implementation details are split across the topic files listed in
[README.md](./README.md).
