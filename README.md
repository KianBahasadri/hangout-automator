# Hangout Automator

MVP website that plans hangouts and invites people by SMS.

- **No authentication** — open the URL and use it. Anyone who reaches the site can read every profile and send SMS, so read the release security gate in [docs/deploy.md](docs/deploy.md) before going public
- **No multi-tenancy** — one shared dataset for the whole app
- **FastAPI** web UI + JSON API
- **SQLite** on disk
- **SMS** via Twilio or mock (console log)
- **Terraform** deploys a cheap Azure Linux VM (B1s) with no public IP, reached through a Cloudflare Tunnel

See [docs/functional-specification.md](docs/functional-specification.md) for product requirements.

## Quick start (local)

```bash
uv run dev
```

Open http://127.0.0.1:9000

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
| Invites | Individual SMS per profile; CONFIRM / REMIND / NO / INFO / INFO 2; bulk-select by tag or field |
| Follow-ups | Up to 2 automated follow-ups if no response, then stop |
| Organizer SMS | Optional; pick organizer profile; customize interval cadence + threshold events |
| Status | Full invitee picture on the hangout page |

## Configuration

Copy `.env.example` to `.env` or export env vars:

| Variable | Default | Notes |
|----------|---------|--------|
| `SMS_PROVIDER` | `mock` | `mock` or `twilio` |
| `TWILIO_*` | empty | Required when provider is twilio |
| `DATABASE_URL` | `sqlite:///./hangout.db` | |
| `FOLLOWUP_HOURS` | `24,48` | Delays for follow-up 1 and 2 |
| `ORGANIZER_INTERVAL_HOURS` | `6` | Digest spacing |
| `PUBLIC_BASE_URL` | `http://localhost:9000` | Canonical public URL; must match the Twilio console webhook URL when `SMS_PROVIDER=twilio` (signature validation) |
| `ENABLE_API_DOCS` | `true` | Serves `/docs`, `/redoc`, `/openapi.json`. Deployments set it to `false` |

Twilio inbound webhook: `POST /webhooks/sms`

## Tests

```bash
uv run --group dev pytest
```

There is no CI — run this before you push. See [docs/testing.md](docs/testing.md).

## API

- Interactive docs: `/docs`
- `GET/POST /api/profiles`
- `GET/POST /api/hangouts`
- `POST /api/hangouts/{id}/setup`
- `POST /webhooks/sms`

## Deploy

Terraform provisions an Azure VM with **no public IP**, reached only through a
Cloudflare Tunnel. State lives in a remote Azure Storage backend, and both
Azure and Cloudflare inputs come from the ignored `.env` via a wrapper script:

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

Cost: `Standard_B1s` is a low-cost burstable SKU; you still pay for the NAT
gateway public IP, disks, and bandwidth. `./scripts/terraform.sh destroy` when
unused (the data disk is `prevent_destroy`).

## Project layout

```
app/                 FastAPI application
  main.py
  models.py
  services.py        SMS invites, RSVP, follow-ups, organizer digests
  routers/           API, web UI, webhooks
  templates/         Jinja HTML
terraform/           Azure VM + Cloudflare Tunnel + cloud-init
docs/                Topic docs (start at docs/README.md) + functional spec
scripts/             Local run, Terraform wrapper, state bootstrap
```
