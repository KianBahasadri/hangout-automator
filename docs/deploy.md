# Deploy

## Terraform (Azure VM)

Directory: `terraform/`. Requires Terraform ≥ 1.5, AzureRM provider ~> 4.0,
and Cloudflare provider ~> 5.22. Run it through `./scripts/terraform.sh` so
the ignored `.env` is loaded and dotenv values are mapped to Terraform inputs.

Provisions roughly: resource group, VNet `10.20.0.0/16`, private subnet
`10.20.1.0/24`, an NSG with no inbound allow rules, an Ubuntu 24.04 LTS Gen2
VM (default size `Standard_B1s`, admin user `hangout`), a remotely managed
Cloudflare Tunnel, its hostname route, and the `hangout.bahasadri.com` CNAME.
The VM has no public IP; an Azure NAT Gateway supplies outbound-only access for
first-boot package installation and `cloudflared`, and a separate managed disk
holds the SQLite database.

Notable variables (`variables.tf` / `terraform.tfvars.example`): `prefix`,
`location` (default `eastus`), required `ssh_public_key`, Cloudflare account and
zone IDs, `cloudflare_hostname` (default `hangout.bahasadri.com`), optional
`git_repo_url` / `git_branch` (default `main`), optional pinned `git_revision`,
SMS/Twilio settings, `public_base_url`, `followup_hours`, and
`organizer_interval_hours`.

Validation (fails at plan/apply, before anything is created):

- `sms_provider` must be `mock` or `twilio`
- `sms_provider=twilio` requires `twilio_account_sid`, `twilio_auth_token`, and `twilio_from_number`

Outputs: resource group, `app_url`, `sms_webhook_url`, Cloudflare Tunnel ID,
and the public hostname. There is deliberately no public-IP or SSH-command
output.

## Cloudflare and infrastructure as code

“Hosted on Cloudflare” can mean either Cloudflare at the edge or a full
Cloudflare Workers rewrite. The current application is a long-running FastAPI
process with a SQLite file and APScheduler jobs, so the low-risk deployment
keeps the Azure VM as the compute layer and puts Cloudflare in front of it.

### Target path: Cloudflare Tunnel + Azure compute (no VM public IP)

The selected scope is deliberately narrow: Cloudflare manages the
`bahasadri.com` zone and one Tunnel only. It is not the application runtime,
database, Workers/Pages host, WAF, or identity layer. Terraform should own the
complete boundary:

- Azure VM, NAT-backed private connectivity, network security group, systemd,
  and the persistent application disk
- a Cloudflare Tunnel from the VM to `http://127.0.0.1:8000`
- the Tunnel hostname route and DNS record for `bahasadri.com` (or an agreed
  subdomain)
- the canonical `PUBLIC_BASE_URL` passed to the app so Twilio signatures match
  the public URL

Cloudflare documents both a [Terraform provider](https://developers.cloudflare.com/api/terraform/)
and a [Terraform-managed Tunnel deployment](https://developers.cloudflare.com/tunnel/deployment-guides/terraform/).
The Cloudflare provider should read `CLOUDFLARE_API_TOKEN` from the shell or CI
secret store; do not put that token in `.tfvars` or repository files.

`cloudflared` runs on the VM and makes an outbound connection to Cloudflare;
Terraform creates the tunnel, hostname route, and DNS record. The VM therefore
does not need a public IP or inbound HTTP rule. Direct SSH is intentionally not
available. If post-deploy access is needed, use Azure Run Command, Serial
Console, Bastion, or add a separately reviewed private management path; these
are not required for the normal Terraform/cloud-init deployment.

### Full Cloudflare Workers migration (separate project)

Pages/Workers are not a drop-in target for this repository. Cloudflare Python
Workers use a `fetch` entrypoint, are currently beta, do not provide persistent
local files, and do not support functional `threading`; the current app relies
on a Python process, SQLite persistence, and APScheduler. A Workers deployment
would therefore require a deliberate rewrite to a Worker entrypoint, D1/KV or
Durable Objects for state, and Cron Triggers/Queues/Workflows for scheduled
jobs. See Cloudflare’s [Python Workers overview](https://developers.cloudflare.com/workers/languages/python/)
and [Python runtime limitations](https://developers.cloudflare.com/workers/languages/python/stdlib/).

Do not select this path unless a platform rewrite is intended.

### IaC ownership and workflow

The desired ownership is:

1. Terraform plans and applies Azure plus Cloudflare resources.
2. Terraform renders the VM environment and cloud-init from variables; secrets
   come from environment variables or a secret manager, never committed files.
3. CI runs `terraform fmt -check` and `terraform validate`; an operator reviews
   a credentialed `terraform plan` before each production apply.
4. Only an approved operator runs `terraform apply`; production state should be
   stored in a locked remote backend (Azure Storage or Terraform Cloud), not a
   developer laptop.
5. Cloud-init owns first-boot package/service setup. Application releases need
   a future private release pipeline; the legacy public-SSH rsync script is not
   part of this topology.

The repository now contains the Azure and Cloudflare resources, the dotenv-aware
Terraform wrapper, and the cloud-init unit that installs and starts
`cloudflared`. Remote state is still a separate bootstrap decision described
below.

The provider lock files (`terraform/.terraform.lock.hcl` and
`terraform/bootstrap-state/.terraform.lock.hcl`) are part of the IaC source and
should be committed so CI and operators resolve the same provider builds.

### Remote Terraform state

Terraform state is the inventory Terraform uses to remember which real Azure
and Cloudflare objects it owns. It includes sensitive rendered values (for
example the Tunnel token and SMS settings), so it must not live only on one
laptop. The included `terraform/bootstrap-state/` root creates an Azure Storage
account and private `tfstate` container with TLS 1.2, blob versioning, 30-day
soft delete, and an Azure AD `Storage Blob Data Contributor` assignment for the
current Azure CLI principal. The main root then uses Azure Blob leases for
state locking.

Bootstrap is the one unavoidable two-phase step: a backend cannot create the
storage account that stores its own first state. Run the bootstrap plan, review
it, and apply it once:

```text
./scripts/bootstrap_state.sh plan
./scripts/bootstrap_state.sh apply
./scripts/terraform.sh init
./scripts/terraform.sh plan
```

The apply writes the ignored `terraform/backend.hcl`; it contains only the
storage resource names and state key, never the Cloudflare or Twilio secrets.
After that, `scripts/terraform.sh apply` refuses to run unless the remote
backend file exists. The bootstrap root has its own local state until you move
that state to a separately managed backend; protect that small bootstrap state
file and do not commit it.

### Deployment inputs and secret handling

Provide non-secret values in chat or a local `.tfvars` file; provide secrets
only through environment variables or a secret manager:

| Area | Input used by this deployment |
|------|-------------------------------|
| Cloudflare | Zone `bahasadri.com`, hostname `hangout.bahasadri.com`, account ID, and zone ID in the ignored `.env` |
| Cloudflare | API token exported as `CLOUDFLARE_API_TOKEN`; it needs Tunnel Edit and DNS Edit permissions |
| Compute | Azure CLI login, region/VM size, repository URL/branch, and an SSH public key for the VM administrator (not direct public access) |
| Public URL | `https://hangout.bahasadri.com`, written to `PUBLIC_BASE_URL` for webhook signature validation |
| SMS | `mock` by default; Twilio SID/token/number must be injected through secret environment variables before selecting `twilio` |
| State | A locked remote Terraform backend should be configured before production apply |

Never send a Twilio auth token, Cloudflare API token, Azure client secret,
Terraform state, SSH private key, or `.env` contents in chat.

### What can be configured from this repository

`./scripts/terraform.sh init`, `validate`, `plan`, and `apply` use the values in
`.env` plus the logged-in Azure CLI. Applying the plan changes the Cloudflare
and Azure accounts; review the plan before applying. The wrapper also reads a
local public key (or `TF_VAR_ssh_public_key`) so a separate tfvars file is not
needed for the default configuration.

### PUBLIC_BASE_URL and webhook signatures

`PUBLIC_BASE_URL` (Terraform `public_base_url`, default
`https://hangout.bahasadri.com` when empty) is written into
`/etc/hangout-automator.env` and used by the app to validate Twilio webhook
signatures (see [sms-and-rsvp.md](./sms-and-rsvp.md)). Cloudflare terminates
public HTTPS and the Tunnel sends HTTP to the local Uvicorn process. Set the
Twilio webhook to the exact `https://hangout.bahasadri.com/webhooks/sms` URL.

### cloud-init

Template `cloud-init.yaml.tftpl`:

- Installs Python, git, and `cloudflared` from Cloudflare's apt repo
- Writes `/etc/hangout-automator.env` (app on `127.0.0.1:8000`, DB `sqlite:////var/lib/hangout-automator/app.db`, SMS settings from Terraform)
- systemd unit `hangout-automator.service` running Uvicorn
- systemd unit `cloudflared.service` using the Terraform-created Tunnel token
- Bootstrap: wait for and mount the persistent data disk, clone `git_repo_url`, create venv, `pip install -r requirements.txt`, and enable/restart the app service

The cloudflared service token and SMS secrets are rendered into root-readable
machine configuration and Terraform state; use a remote encrypted backend and
restrict state access. Never commit real credentials, `.env`, Terraform state,
or private keys.

### Release security gate

Cloudflare Tunnel provides transport and origin hiding, not user
authentication. This repository's FastAPI app currently has no application
authentication or rate limiting. Because the requested Cloudflare scope is
DNS plus Tunnel only (no Access policy), the hostname is public once applied.
Keep `SMS_PROVIDER=mock` until an application-level auth/rate-limit decision is
made and tested; enabling Twilio would otherwise expose contact data and an
SMS-sending action to anyone who discovers the hostname.

## Production go-live checklist (external prerequisites)

The normal Azure/Cloudflare resources are automated by Terraform. These are the
remaining external or operator-controlled steps:

1. **Twilio**: provision an SMS-capable phone number; set the messaging webhook to `https://hangout.bahasadri.com/webhooks/sms` (HTTP POST); inject the three Twilio credentials and select `SMS_PROVIDER=twilio`.
2. **State**: initialize the chosen remote backend before the first production apply.
3. **Azure**: `az login`, then review `./scripts/terraform.sh plan` and run `./scripts/terraform.sh apply`.

## Rsync updates

`./scripts/deploy_rsync.sh user@host` is a legacy public-SSH updater and is not
usable for this private-VM topology. Initial code and service setup come from
Terraform-rendered cloud-init; a future release pipeline should use Azure Run
Command or another private management path for updates.
