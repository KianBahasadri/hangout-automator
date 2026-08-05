# Hangout Automator

MVP website that plans hangouts and invites people by SMS.

- **No authentication** — open the URL and use it
- **No multi-tenancy** — one shared dataset for the whole app
- **FastAPI** web UI + JSON API
- **SQLite** on disk
- **SMS** via Twilio or mock (console log)
- **Terraform** deploys a cheap Azure Linux VM (B1s) with nginx + systemd

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
| Profiles | Name + phone required; drinks, smokes, drive, tags optional (blank = unset); food allergies picked from a shared list managed in Settings |
| Hangouts | All detail fields optional; draft or set up immediately |
| Invites | Individual SMS per profile; CONFIRM / REMIND / NO; bulk-select by tag or field |
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
| `PUBLIC_BASE_URL` | `http://localhost:9000` | |

Twilio inbound webhook: `POST /webhooks/sms`

## API

- Interactive docs: `/docs`
- `GET/POST /api/profiles`
- `GET/POST /api/hangouts`
- `POST /api/hangouts/{id}/setup`
- `GET/PUT /api/settings`
- `POST /webhooks/sms`

## Azure deploy (Terraform)

Prerequisites: Azure CLI logged in (`az login`), Terraform ≥ 1.5, an SSH key pair.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: ssh_public_key, git_repo_url, optional Twilio secrets

terraform init
terraform apply
```

Outputs include `app_url`, `ssh_command`, and `sms_webhook_url`.

Cloud-init on the VM:

1. Installs Python, nginx, git  
2. Clones `git_repo_url` into `/opt/hangout-automator`  
3. Creates a venv and installs requirements  
4. Runs uvicorn under systemd; nginx proxies port 80 → 8000  

If you prefer not to clone from git, leave `git_repo_url` empty and push code with:

```bash
./scripts/deploy_rsync.sh hangout@YOUR_PUBLIC_IP
```

(First boot still needs the service unit from cloud-init; rsync only updates app files.)

### Cost note

`Standard_B1s` is a low-cost burstable SKU. You still pay for the public IP, disk, and bandwidth. Destroy when unused:

```bash
cd terraform && terraform destroy
```

## Project layout

```
app/                 FastAPI application
  main.py
  models.py
  services.py        SMS invites, RSVP, follow-ups, organizer digests
  routers/           API, web UI, webhooks
  templates/         Jinja HTML
terraform/           Azure VM + networking + cloud-init
docs/                Functional specification
scripts/             Local run + rsync deploy
```
