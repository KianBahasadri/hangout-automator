# Overview

Hangout Automator is a single shared-dataset FastAPI app: anyone who can open the site sees and edits the same profiles and hangouts. There is no authentication and no multi-tenancy.

## Runtime shape

- **Web UI** — Jinja templates served by `app/routers/web.py`
- **JSON API** — `app/routers/api.py` under `/api` (also powers profile autosave)
- **SMS webhook** — `POST /webhooks/sms` in `app/routers/webhooks.py`
- **Background jobs** — APScheduler in `app/main.py` lifespan: follow-ups every 5 minutes, organizer interval digests every 10 minutes
- **Persistence** — SQLAlchemy + SQLite by default (`DATABASE_URL`)

Entry points: settings-aware launcher `python -m app.server` (production),
console script `dev` → `app.dev:main` (reload), and the ASGI app
`app.main:app`.

## Package layout

```
app/
  main.py           FastAPI app, static mount, scheduler
  server.py         settings-aware Uvicorn launcher
  event_logging.py  JSONL audit file, correlation IDs, HTTP trace middleware
  config.py         pydantic-settings
  database.py       engine, sessions, SQLite ensure/migrate
  ids.py            row-id bounds for paths, payloads, and form fields
  models.py         ORM + enums
  schemas.py        API Pydantic models
  services.py       invites, RSVP, follow-ups, organizer SMS
  messages.py       SMS copy + reply parse
  sms.py            mock/Twilio + phone normalize
  routers/          api, web, webhooks
  templates/        Jinja pages
  static/           CSS, JS, icons
docs/               topic docs + functional specification
tests/              pytest suite + generated route/form smoke matrix
terraform/          Azure VM + cloud-init
scripts/            local run + rsync deploy
```

Product intent (MVP requirements) lives in [functional-specification.md](./functional-specification.md). Implementation details are split across the other topic files listed in [README.md](./README.md).
