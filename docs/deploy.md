# Deploy

## Terraform (Azure VM)

Directory: `terraform/`. Requires Terraform ≥ 1.5, AzureRM provider ~> 4.0,
and Cloudflare provider ~> 5.22. Run it through `./scripts/terraform.sh` so
the ignored `.env` is loaded and dotenv values are mapped to Terraform inputs.

Provisions roughly: resource group, VNet `10.20.0.0/16`, private subnet
`10.20.1.0/24`, an NSG with no inbound allow rules, an Ubuntu 24.04 LTS Gen2
VM (default size `Standard_B2ats_v2`, admin user `hangout`), a remotely managed
Cloudflare Tunnel, its hostname route, and the app hostname's CNAME.
The VM has no public IP; an Azure NAT Gateway supplies outbound-only access for
first-boot package installation and `cloudflared`, and a separate managed disk
holds the SQLite database.

Notable variables (`variables.tf` / `terraform.tfvars.example`): `prefix`,
`location` (default `mexicocentral`, see [Region and VM size
capacity](#region-and-vm-size-capacity)), required `ssh_public_key`, required Cloudflare
account id, zone id, and `cloudflare_hostname`, optional `git_repo_url` /
`git_branch` (default `main`), optional pinned `git_revision`, SMS/Twilio
settings, `public_base_url`, `followup_hours`, and `organizer_interval_hours`.

`cloudflare_hostname` deliberately has **no default** and the example tfvars
carries placeholders: the deployed hostname is the only thing standing between
an unauthenticated app and the open internet, so it stays in the ignored `.env`
rather than in this public repository. `scripts/terraform.sh` supplies it from
`CLOUDFLARE_TUNNEL_HOSTNAME`.

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
app's zone and one Tunnel only. It is not the application runtime,
database, Workers/Pages host, WAF, or identity layer. Terraform should own the
complete boundary:

- Azure VM, NAT-backed private connectivity, network security group, systemd,
  and the persistent application disk
- a Cloudflare Tunnel from the VM to `http://127.0.0.1:8000`
- the Tunnel hostname route and DNS record for the configured zone and
  subdomain
- the Cloudflare Access applications and policies in front of that hostname
  (see Access control below)
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

That account sets `shared_access_key_enabled = false`, so both roots reach the
blob data plane with an Azure AD token instead: the bootstrap provider sets
`storage_use_azuread = true` and the generated backend file sets
`use_azuread_auth = true`. Without the provider setting, the account is created
and then immediately fails its own post-create probe with "Key based
authentication is not permitted on this storage account", leaving the resource
tainted.

The `Storage Blob Data Contributor` assignment is a data-plane role, and Azure
takes up to a minute or two to propagate it. A `terraform.sh init` run
immediately after bootstrap can therefore fail with
`AuthorizationPermissionMismatch`; that is propagation delay, not
misconfiguration, and re-running `init` a minute later succeeds. Note that
subscription Owner does not imply blob data access — it covers the service
properties the provider probes, but not the container the backend writes to.

A `plan` holds the same blob-lease lock as an `apply`, so a plan that is
killed mid-run (closed terminal, laptop sleep) leaves the state locked and
every later command fails with "state blob is already locked". Confirm no
terraform process is actually still running, then clear it once:
`./scripts/terraform.sh force-unlock -force <lock ID from the error>`.

Resuming an interrupted apply can hit ARM propagation lag as two
benign-looking failures. A freshly created VNet can answer 404
(`ResourceNotFound`) for a few moments after Azure reports it created, so
`azurerm_subnet.main` fails while "waiting for provisioning state" —
re-running the `apply` a minute later just continues. The same 404 wave
during an apply's refresh can silently drop a real, still-existing resource
(seen with the NSG) from state; the next apply then dies with "A resource
with the ID ... already exists — to be managed via Terraform this resource
needs to be imported". That one does not heal by retrying: re-import it
(`./scripts/terraform.sh import <addr> <id from the error>`), then apply
again. A failed apply also makes a saved plan file stale, so re-plan rather
than reusing an old `tfplan`.

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

### Region and VM size capacity

Neither of the two constraints below is visible to Terraform. `location` and
`vm_size` are plain strings that no provider validates during plan, so both
failures land mid-apply — as a 409 `SkuNotAvailable` or a 403
`RequestDisallowedByAzure` raised while creating resources, after the network
and every Cloudflare resource already exist. Check both before changing either
variable.

**1. The subscription is fenced to an allowlist of regions.** An Azure Policy
named "Allowed resource deployment regions" rejects everything else. Read the
current list rather than assuming it:

```bash
az policy assignment list --disable-scope-strict-match \
  --query "[?displayName=='Allowed resource deployment regions'].parameters" -o json
```

For this subscription that is `centralus`, `eastus`, `canadacentral`,
`eastus2`, `mexicocentral`. A region outside it fails at *every* resource, not
just the VM — the resource group is created, then the NAT gateway, NSG, and
disk all 403 together.

**2. Within the allowlist, capacity still varies by size.** The entire
B-series is `NotAvailableForSubscription` in four of those five regions.
Only `mexicocentral` will schedule a burstable size, which is why `location`
defaults to it despite being the furthest away — the alternative is roughly
six times the price for a non-burstable size:

| Region | Size | vCPU / RAM | ~USD/month |
|--------|------|-----------|------------|
| `mexicocentral` | `Standard_B2ats_v2` | 2 / 1 GB | 7.52 |
| `eastus` / `eastus2` / `centralus` | `Standard_F1als_v7` | 1 / 2 GB | 44.16 |
| `eastus` | `Standard_D2s_v7` | 2 / 8 GB | 96.36 |

Inspect restrictions by **type**, not merely by presence — the distinction
decides whether a size is usable:

```bash
az vm list-skus -l <region> --resource-type virtualMachines --all \
  --query "[?name=='<size>'].{name:name, restrictions:restrictions}" -o json
```

A `Location` restriction blocks the size outright. A `Zone` restriction only
blocks the listed zones, and this VM is deliberately non-zonal, so regional
allocation still succeeds — `Standard_B2ats_v2` is restricted in
`mexicocentral` zone 3 and deploys fine. Also confirm
`CpuArchitectureType` is `x64`: the image reference pins the non-ARM `server`
SKU, so an ARM size (`*p*_v2`, e.g. `Standard_B2pts_v2`) would need
`server-arm64` instead.

A size can also be listed with no price in a region even when it is
unrestricted there — `Standard_B2ts_v2` returns no `mexicocentral` retail
price while its AMD sibling `Standard_B2ats_v2` does. Treat a missing price as
a sign the size is not really sold there and pick one that quotes.

Current prices come from the public retail API, which needs no credentials:

```bash
curl -s -G https://prices.azure.com/api/retail/prices \
  --data-urlencode "\$filter=serviceName eq 'Virtual Machines' and armRegionName eq 'mexicocentral' and armSkuName eq 'Standard_B2ats_v2' and priceType eq 'Consumption'"
```

Changing `location` after an apply replaces every Azure resource, and two
things make that more than a normal replace:

- `azurerm_subnet.main` plans as a **no-op**. It has no `location` of its own
  and its name and parent names do not change, so Terraform sees no diff — but
  Azure deletes it along with the resource group, and the NIC then fails to
  find it. Force it: `./scripts/terraform.sh plan -replace=azurerm_subnet.main`.
- `azurerm_managed_disk.data` carries `prevent_destroy = true`, so the plan is
  refused outright. If the disk holds real data, back it up per
  [Backups](#backups) first. Only when it is genuinely empty, drop it from
  state: `./scripts/terraform.sh state rm azurerm_managed_disk.data`.

  Dropping it from state is not enough on its own. The disk still exists in
  Azure, and the AzureRM provider defaults
  `prevent_deletion_if_contains_resources` to true, so destroying the resource
  group fails with "the Resource Group still contains Resources" — after
  spending about ten minutes on the attempt, since the guard is checked at the
  end. Delete the orphan first (`az disk delete -g <rg> -n <disk> --yes`), then
  re-plan. That guard is worth keeping: it is what stops a `state rm` from
  quietly taking a real database down with the resource group.

The Cloudflare resources are unaffected by a region change — the tunnel keeps
its ID and token, so the DNS record and Access policies stay in place.

### Deployment inputs and secret handling

Provide non-secret values in chat or a local `.tfvars` file; provide secrets
only through environment variables or a secret manager:

| Area | Input used by this deployment |
|------|-------------------------------|
| Cloudflare | Zone, app hostname, account ID, and zone ID — all in the ignored `.env`, never in this repo |
| Cloudflare | API token exported as `CLOUDFLARE_API_TOKEN`; it needs Tunnel Edit, DNS Edit, and Access: Apps and Policies Edit permissions |
| Cloudflare | `CLOUDFLARE_ACCESS_EMAILS` in the ignored `.env` — comma-separated addresses Access admits (see Access control below) |
| Compute | Azure CLI login, region/VM size, repository URL/branch, and an SSH public key for the VM administrator (not direct public access) |
| Public URL | `https://<app hostname>`, written to `PUBLIC_BASE_URL` for webhook signature validation |
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
`https://<cloudflare_hostname>` when empty) is written into
`/etc/hangout-automator.env` and used by the app to validate Twilio webhook
signatures (see [sms-and-rsvp.md](./sms-and-rsvp.md)). Cloudflare terminates
public HTTPS and the Tunnel sends HTTP to the local Uvicorn process. The Twilio
webhook must be the exact `https://<app hostname>/webhooks/sms` URL — a
mismatched base URL produces a valid-looking request whose signature never
verifies.

### Registering the Twilio webhook

`./scripts/set_twilio_webhook.py` sets it over the Twilio REST API instead of
the console, using the `twilio` SDK the app already depends on. It resolves the
number's SID from `TWILIO_FROM_NUMBER`, sets `SmsUrl`, and re-reads the number
to confirm the value Twilio actually stored. Re-running it once the URL is
correct is a no-op, and it re-execs into `.venv` if invoked with an interpreter
that lacks the SDK.

It builds the URL from `Settings.public_base_url` and the webhooks router rather
than from its own string handling, so the registered value is exactly what
`_canonical_webhook_url` reconstructs during signature verification. It refuses
to run while `PUBLIC_BASE_URL` still points at localhost, since the default
would otherwise register a URL Twilio cannot reach.

This is not managed in Terraform. The only provider that covers it is a
community 0.x one whose `twilio_phone_number` delete path calls
`IncomingPhoneNumber.Delete()`, which *releases* the number, and which would
need Twilio credentials that can spend money — too much blast radius for a
single URL field.

Twilio permits exactly one `SmsUrl` per number, so pointing a number at this app
takes it away from anything else using it. The script refuses to overwrite a
non-empty webhook unless `--force` is passed, and `--dry-run` reports what it
would do. It also fails fast when the number carries an `SmsApplicationSid`,
because that binding silently overrides `SmsUrl`.

### cloud-init

Template `cloud-init.yaml.tftpl`:

- Installs Python, git, and `cloudflared` from Cloudflare's apt repo
- Writes `/etc/hangout-automator.env` (app on `127.0.0.1:8000`, DB `sqlite:////var/lib/hangout-automator/app.db`, `ENABLE_API_DOCS=false`, SMS settings from Terraform, and `LOG_*` settings for `/var/lib/hangout-automator/logs/server.log`)
- systemd unit `hangout-automator.service` running Uvicorn, with
  `RequiresMountsFor=/var/lib/hangout-automator` so the app refuses to start
  without the data disk instead of silently creating an empty SQLite file on
  the OS disk (the fstab entry is `nofail`, so the VM itself still boots)
- systemd unit `cloudflared.service` using the Terraform-created Tunnel token
- `hangout-backup.service` + `.timer` (see Backups below)
- Bootstrap: wait for and mount the persistent data disk, clone `git_repo_url`, create venv, `pip install -r requirements.txt`, enable/restart the app service, and enable the backup timer

The cloudflared service token and SMS secrets are rendered into root-readable
machine configuration and Terraform state; use a remote encrypted backend and
restrict state access. Never commit real credentials, `.env`, Terraform state,
or private keys.

### Access control

The FastAPI app has **no authentication of its own**, so Cloudflare Access is
the only gate in front of it. Every route — the profile list, the
invite-sending actions — is reachable by anyone Access lets through. Because
hangout `motive` and `notes` become the body of the outgoing SMS, someone who
got past Access could send text of their own choosing from the Twilio number.

`terraform/access.tf` owns this, so it is applied and reviewed like the rest of
the boundary:

- `cloudflare_zero_trust_access_policy.owner` — `allow` for each address in
  `cloudflare_access_allowed_emails` (comma-separated `CLOUDFLARE_ACCESS_EMAILS`
  in the ignored `.env`; no default, so no personal address is published here).
  `allowed_idps` is left unset, so any login method on the account satisfies it;
  one-time PIN works out of the box, with no external identity provider.
- `cloudflare_zero_trust_access_application.app` — the hostname, 24h session
- `cloudflare_zero_trust_access_application.webhook` +
  `cloudflare_zero_trust_access_policy.webhook_bypass` — `bypass` for
  `/webhooks/sms`. Access matches the most specific path first, so this covers
  the webhook and the application above covers everything else.

The webhook exception is forced: Twilio cannot attach
`CF-Access-Client-Id`/`CF-Access-Client-Secret` headers to a webhook, so a
service token cannot work there. That makes the `X-Twilio-Signature` check in
`app/routers/webhooks.py` the **only** layer on that path — see
[sms-and-rsvp.md](./sms-and-rsvp.md) for what it verifies.

Defense in depth beyond Access:

- `ENABLE_API_DOCS=false` on the VM, so `/docs`, `/redoc`, and `/openapi.json`
  do not publish a map of the state-changing endpoints
- `cloudflare_hostname` has no default, so the deployed hostname is not
  committed here — though earlier commits in this public repository still
  contain it, so treat it as known

There is still no rate limiting, and Access does not add any.

### Backups

`hangout-backup.timer` runs `/usr/local/bin/hangout-backup.sh` daily
(`Persistent=true`, so a missed run fires after boot). It writes a gzipped
snapshot to `/var/lib/hangout-automator/backups/app-<UTC timestamp>.db.gz` and
keeps the newest 7.

It uses `sqlite3 .backup` rather than copying the file: the database runs in WAL
mode, so committed rows can still live in the `-wal` sidecar and a plain `cp`
can capture a database that is missing them.

The backup contains the database only. The rotating audit log remains on the
same data disk at `/var/lib/hangout-automator/logs/` and is not included in
these snapshots; copy it separately when preserving the trace history.

Scope: this protects against application-level corruption, a bad migration, or
deleting rows by accident. It is **not** off-site — the snapshots sit on the
same managed disk as `app.db`, so restoring after losing that disk needs a
separate copy (Azure disk snapshot, or pulling the `.gz` off the VM).

Restore: stop `hangout-automator`, `gunzip -c backups/app-<stamp>.db.gz >
/var/lib/hangout-automator/app.db`, remove any stale `app.db-wal` / `app.db-shm`,
then start the service.

### Tearing down

`terraform destroy` fails while the data disk carries `prevent_destroy = true`.
That is deliberate — it is the only copy of the database. To intentionally
destroy the environment, take a backup first, then drop the disk from state
(`terraform state rm azurerm_managed_disk.data`) and destroy; the disk survives
in Azure and must be deleted by hand once you are sure.

## Production go-live checklist (external prerequisites)

The normal Azure/Cloudflare resources are automated by Terraform. These are the
remaining external or operator-controlled steps:

1. **Twilio**: provision an SMS-capable phone number; inject the three Twilio credentials and select `SMS_PROVIDER=twilio`; then run `./scripts/set_twilio_webhook.py` to register the messaging webhook. Use a number not already serving another app — see [Registering the Twilio webhook](#registering-the-twilio-webhook).
2. **State**: initialize the chosen remote backend before the first production apply.
3. **Cloudflare Zero Trust**: done for the current deployment account — Zero Trust is enabled (the free tier covers 50 users) and the API token carries Access: Apps and Policies. On a fresh account both are dashboard steps, because editing an API token over the API needs a token carrying User: API Tokens Edit, which a deployment token normally does not have. When adding the permission, put it under an *Account* resource row rather than a zone row; it is an account-level permission group, so a zone-scoped policy silently fails to grant it. To confirm, `GET /client/v4/accounts/<account id>/access/apps` should return `success: true` rather than error `10000`.
4. **Cloudflare API token write scopes**: Cloudflare grants Read and Edit as
   separate permission groups, and a plan cannot tell them apart — every
   resource in it is a create, so nothing is exercised until apply. A token
   holding only Tunnel Read lists tunnels happily and then fails
   `POST /cfd_tunnel` with 403 `10000` *during* apply, after the Access
   applications and most of the Azure network already exist. Probe the writes
   first; an empty body creates nothing, and 400 means the permission is
   present and only the body was rejected, while 403 means it is missing:

   ```bash
   for p in "accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel" \
            "accounts/$CLOUDFLARE_ACCOUNT_ID/access/apps" \
            "zones/$CLOUDFLARE_ZONE_ID/dns_records"; do
     printf '%s -> ' "$p"
     curl -s -o /dev/null -w '%{http_code}\n' -X POST \
       -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
       -H "Content-Type: application/json" -d '{}' \
       "https://api.cloudflare.com/client/v4/$p"
   done
   ```

   Note that `GET /client/v4/user/tokens/verify` returns `Invalid API Token`
   for an account-owned token even when the token is fine, so it is not a
   usable health check here. Fixing a missing scope is a dashboard step for the
   same reason as item 3.

   The tunnel permission is not listed under the name the product now uses.
   In the token editor it is **Cloudflare One / Zero Trust → `Argo Tunnel
   (Legacy)`**, which is the original name for what Cloudflare rebranded as
   Cloudflare Tunnel; it still gates the `cfd_tunnel` endpoints. Its
   description reads "Grants access to view Cloudflare Tunnels" because that
   describes the Read box — tick **Edit**, which implies Read. The nearby
   `Connectivity Directory` entry also mentions tunnels but is Magic WAN and
   grants nothing here.
5. **Azure**: `az login`, then review `./scripts/terraform.sh plan` and run `./scripts/terraform.sh apply`.

### Verifying a fresh deploy

The VM has no inbound access, so smoke-test from two directions. On the VM,
via Run Command — cloud-init should report `status: done` with no errors, all
three units active, and the app answering locally:

```bash
az vm run-command invoke -g <rg> -n <vm> --command-id RunShellScript \
  --scripts "cloud-init status --long; systemctl is-active hangout-automator cloudflared hangout-backup.timer; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/" \
  --query "value[0].message" -o tsv
```

Externally, `GET https://<hostname>/webhooks/sms` should return **405** from
the app itself — that one response proves DNS, the tunnel, the Access bypass
exception, and Uvicorn all at once. `GET /` should redirect (302) to the
Cloudflare Access login and never serve the app.

## Rsync updates

`./scripts/deploy_rsync.sh user@host` is a legacy public-SSH updater and is not
usable for this private-VM topology. Initial code and service setup come from
Terraform-rendered cloud-init; a future release pipeline should use Azure Run
Command or another private management path for updates.

It excludes `.env`, `terraform.tfvars`, `backend.hcl`, state files, and all
`*.db*` files, so running it cannot copy local secrets or a development
database onto a VM.
