# Hangout Automator

MVP website that plans hangouts and invites people by SMS.

- **Clerk authentication** — set `CLERK_ENABLED=true` to require a Clerk session for the UI and API; a Twilio webhook remains public only for Twilio signature validation
- **Multi-tenant** — each authenticated user gets their own workspace; `CLERK_ENABLED=false` runs everything in the default workspace
- **FastAPI** web UI + JSON API
- **PostgreSQL** (Alembic migrations; local dev via Docker Compose)
- **SMS** via Twilio or mock (console log)
- **Separate worker process** for background jobs, with two-layer send locking
- **Terraform** deploys an Azure VM (no public IP) + PostgreSQL Flexible Server, reached through a Cloudflare Tunnel

See [docs/functional-specification.md](docs/functional-specification.md) for product requirements.

## Quick start (local)

Requires Docker for Postgres:

```bash
./scripts/db_up.sh          # start the compose Postgres and wait for readiness
uv run alembic upgrade head # apply schema migrations
uv run dev                  # web process (http://127.0.0.1:9000)
uv run worker               # separate background-jobs process
```

`uv run dev` creates the project environment and installs dependencies automatically. The existing
`./scripts/run_local.sh` command is also still supported.

Mock SMS prints invites and replies to the terminal. Simulate an inbound reply:

```bash
curl -X POST http://127.0.0.1:9000/webhooks/sms \
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

Copy `.env.example` to `.env` or export env vars:

| Variable | Default | Notes |
|----------|---------|--------|
| `SMS_PROVIDER` | `mock` | `mock` or `twilio` |
| `TWILIO_*` | empty | Required when provider is twilio |
| `DATABASE_URL` | `postgresql+psycopg://hangout:hangout@localhost:5432/hangout` | Compose Postgres |
| `FOLLOWUP_HOURS` | `24,48` | Delays for follow-up 1 and 2 |
| `ORGANIZER_INTERVAL_HOURS` | `6` | Digest spacing |
| `PUBLIC_BASE_URL` | `http://localhost:9000` | Canonical public URL; must match the Twilio console webhook URL when `SMS_PROVIDER=twilio` (signature validation) |
| `ENABLE_API_DOCS` | `true` | Serves `/docs`, `/redoc`, `/openapi.json`. Deployments set it to `false` |

Twilio inbound webhook: `POST /webhooks/sms`

## Tests

```bash
uv run --group dev pytest
```

CI runs this suite plus `ruff` on every push and pull request
(`.github/workflows/ci.yml`). `./scripts/verify_plan.sh` is the aggregate gate
for the growth plan — see [docs/testing.md](docs/testing.md).

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
backend, and both Azure and Cloudflare inputs come from the ignored `.env` via
a wrapper script:

```bash
./scripts/bootstrap_state.sh apply   # once: creates the remote state backend
./scripts/terraform.sh init
./scripts/terraform.sh plan
./scripts/terraform.sh apply
```

Do not run bare `terraform apply` — it has neither the Cloudflare credentials
nor the remote state. **[docs/deploy.md](docs/deploy.md) is the full and
authoritative deployment guide**, including the go-live checklist, secret
handling, and the security gate to clear before switching `SMS_PROVIDER` to
`twilio`.

Cost: `Standard_B2ats_v2` is a low-cost burstable SKU; you still pay for the
NAT gateway public IP, disks, and bandwidth. `./scripts/terraform.sh destroy`
when unused (the data disk and the database server are `prevent_destroy`).

## Project layout

```
app/                 FastAPI application
  main.py            web process (no scheduler)
  worker.py          background jobs process
  tenancy.py         workspace resolution
  locks.py           advisory locks for the sweeps
  models.py
  services.py        SMS invites, RSVP, follow-ups, organizer digests
  routers/           API, web UI, webhooks
  templates/         Jinja HTML
migrations/          Alembic schema migrations
terraform/           Azure VM + Flexible Server + Cloudflare Tunnel + cloud-init
docs/                Topic docs (start at docs/README.md) + functional spec
scripts/             Local run, db_up, migrate, verify_plan, Terraform wrapper
```
