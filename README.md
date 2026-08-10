# Hangout Automator

MVP website that plans hangouts and invites people by SMS.

- **Clerk authentication** — set `CLERK_ENABLED=true` to require a Clerk session for the UI and API; a Twilio webhook remains public only for Twilio signature validation
- **Multi-tenant** — each authenticated user gets their own workspace; `CLERK_ENABLED=false` runs everything in the default workspace
- **FastAPI** web UI + JSON API
- **PostgreSQL** (Alembic migrations; local dev via Docker Compose)
- **SMS** via Twilio or mock (console log)
- **Separate worker process** for background jobs, with two-layer send locking
- **Terraform** deploys an Azure VM (no public IP) + PostgreSQL Flexible Server, reached through a Cloudflare Tunnel

See [docs/README.md](docs/README.md) for how the system works.

## Quick start (local)

Requires Docker for Postgres:

```bash
./scripts/db_up.sh          # start the compose Postgres and wait for readiness
uv run alembic upgrade head # apply schema migrations
```

Then run the two processes, each in its own terminal — both are long-running:

```bash
uv run dev                  # web process (http://127.0.0.1:8000)
uv run worker               # background jobs: follow-ups and organizer digests
```

`uv run dev` creates the project environment and installs dependencies
automatically. Without the worker the app serves fine but no follow-up or
organizer SMS is ever sent.

Mock SMS prints invites and replies to the terminal. Simulate an inbound reply:

```bash
curl -X POST http://127.0.0.1:8000/webhooks/sms \
  -H 'Content-Type: application/json' \
  -d '{"from":"+15551234567","body":"confirm"}'
```

## Features

| Area | Behavior |
|------|----------|
| Profiles | Name + phone required; drinks, smokes, drive, tags optional (blank = unset); dietary restrictions picked from a shared list managed in Settings (defaults: meat, pork) |
| Hangouts | All detail fields optional; draft or set up immediately |
| Invites | Individual SMS per profile; CONFIRM / NO / INFO / MORE INFO; bulk-select by tag or field |
| Follow-ups | Up to 2 automated follow-ups if no response, then stop |
| Organizer SMS | Optional; pick organizer profile; customize interval cadence + threshold events |
| Status | Full invitee picture on the hangout page |

## Configuration

Copy `.env.example` to `.env.development` for local work, or export env vars:

| Variable | Default | Notes |
|----------|---------|--------|
| `SMS_PROVIDER` | `mock` | `mock` or `twilio` |
| `TWILIO_*` | empty | Required when provider is twilio |
| `DATABASE_URL` | `postgresql+psycopg://hangout:hangout@localhost:5432/hangout` | Compose Postgres |
| `FOLLOWUP_HOURS` | `24,48` | Delays for follow-up 1 and 2 |
| `ORGANIZER_INTERVAL_HOURS` | `6` | Digest spacing |
| `APP_PORT` | `8000` | Development-template HTTP port (the code fallback is `9000`) |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | Development-template canonical URL; must match the Twilio console webhook URL when `SMS_PROVIDER=twilio` (signature validation) |
| `ENABLE_API_DOCS` | `true` | Serves `/docs`, `/redoc`, `/openapi.json`. Deployments set it to `false` |

Twilio inbound webhook: `POST /webhooks/sms`

## Tests

```bash
uv run --group dev pytest
```

CI runs this suite plus `ruff` on every push and pull request
(`.github/workflows/ci.yml`). `./scripts/check.sh` is the aggregate local gate
(pytest, ruff, Alembic, and a few structural invariants) — see
[docs/testing.md](docs/testing.md).

## API

- Interactive docs: `/docs`
- `GET/POST /api/profiles`
- `GET/POST /api/hangouts`
- `POST /api/hangouts/{id}/setup`
- `POST /webhooks/sms`

## Deploy

Terraform provisions an Azure VM with **no public IP**, reached only through a
Cloudflare Tunnel, plus an Azure Database for PostgreSQL Flexible Server
(private endpoint, 35-day backups). State lives in a remote Azure Storage
backend, and both Azure and Cloudflare inputs come from the ignored
`.env.production` via a wrapper script:

```bash
./scripts/deploy/bootstrap_state.sh apply   # once: creates the remote state backend
./scripts/deploy/terraform.sh init
./scripts/deploy/terraform.sh plan
./scripts/deploy/terraform.sh apply
```

Do not run bare `terraform apply` — it has neither the Cloudflare credentials
nor the remote state. **[docs/deploy.md](docs/deploy.md) is the full and
authoritative deployment guide**, including the go-live checklist, secret
handling, and the security gate to clear before switching `SMS_PROVIDER` to
`twilio`.

Cost: `Standard_B2ats_v2` is a low-cost burstable SKU; you still pay for the
NAT gateway public IP, disks, and bandwidth. `./scripts/deploy/terraform.sh destroy`
when unused (the data disk and the database server are `prevent_destroy`).

## Project layout

```
app/                    FastAPI application
  main.py               web process (no scheduler)
  worker.py             background jobs process
  server.py             Uvicorn launchers for `dev` / `main`
  tenancy.py            workspace resolution
  locks.py              advisory locks for the sweeps
  models.py
  services.py           SMS invites, RSVP, follow-ups, organizer digests
  routers/              API, web UI, webhooks
  templates/            Jinja HTML
  static/               CSS, JS, icons
migrations/             Alembic schema migrations
tests/                  pytest suite + generated route/form smoke matrix
terraform/              Azure VM + Flexible Server + Cloudflare Tunnel + cloud-init
docs/                   Topic docs (start at docs/README.md) + functional spec
  archive/              Point-in-time records; not maintained
scripts/
  db_up.sh              start the local compose Postgres
  check.sh              aggregate local gate (pytest, ruff, alembic, invariants)
  deploy/               Terraform wrapper, state bootstrap, Twilio webhook, rsync
```
