# Deploy

What has actually been deployed, and what broke doing it, is logged in
[deployment-history.md](./deployment-history.md). Record an entry there after
any apply that changes real infrastructure.

## Terraform (Azure VM)

Directory: `terraform/`. Requires Terraform ≥ 1.5, AzureRM provider ~> 4.0,
and Cloudflare provider ~> 5.22. Run it through `./scripts/deploy/terraform.sh` so
the ignored `.env.production` is loaded and dotenv values are mapped to Terraform
inputs. The local `.env.development` file is never a deployment input; set
`HANGOUT_DEPLOY_ENV_FILE` only to explicitly select another production secrets file.

Provisions roughly: resource group, VNet `10.20.0.0/16`, private subnet
`10.20.1.0/24`, an NSG with no inbound allow rules, an Ubuntu 24.04 LTS Gen2
VM (default size `Standard_B2ats_v2`, admin user `hangout`), a remotely managed
Cloudflare Tunnel, its hostname route, and the app hostname's CNAME.
The VM has no public IP; the subnet's `default_outbound_access_enabled = true`
supplies outbound-only access for first-boot package installation and
`cloudflared` (see [Outbound internet access](#outbound-internet-access-egress)).
A separate managed disk holds the audit log. The database is an Azure Database for PostgreSQL Flexible
Server (`B_Standard_B1ms`, 35-day backup retention) with `prevent_destroy =
true`, reachable only through a private endpoint in the `10.20.1.0/24` subnet
plus a private DNS zone; the server has no public network access. The admin
password comes from `POSTGRES_ADMIN_PASSWORD` in the ignored `.env.production` and has no
Terraform default (`terraform/postgres.tf`).

Notable variables (`variables.tf` / `terraform.tfvars.example`): `prefix`,
`location` (default `mexicocentral`, see [Region and VM size
capacity](#region-and-vm-size-capacity)), required `ssh_public_key`, required Cloudflare
account id, zone id, and `cloudflare_hostname`, optional `git_repo_url` /
`git_branch` (default `main`), optional pinned `git_revision`, SMS/Twilio
settings, optional `GOOGLE_MAPS_API_KEY`, Clerk settings, `public_base_url`,
`app_port`, `followup_hours`, `organizer_interval_hours`, and the Postgres
admin user/password (`scripts/deploy/terraform.sh` requires `POSTGRES_ADMIN_PASSWORD`
in the ignored `.env.production` — no default).
`scripts/deploy/terraform.sh` maps these values from the ignored `.env.production` to Terraform.

`cloudflare_hostname` deliberately has **no default** and the example tfvars
carries placeholders: the deployed hostname is a sensitive deployment boundary,
so it stays in the ignored `.env.production` rather than in this public repository.
`scripts/deploy/terraform.sh` supplies it from `CLOUDFLARE_TUNNEL_HOSTNAME`.

Validation (fails at plan/apply, before anything is created):

- `sms_provider` must be `mock` or `twilio`
- `sms_provider=twilio` requires `twilio_account_sid`, `twilio_auth_token`, and `twilio_from_number`
- `clerk_enabled=true` requires the Clerk publishable key, frontend API URL, and secret or JWT key

Outputs: resource group, `app_url`, `sms_webhook_url`, Cloudflare Tunnel ID,
and the public hostname. There is deliberately no public-IP or SSH-command
output.

## Cloudflare and infrastructure as code

“Hosted on Cloudflare” can mean either Cloudflare at the edge or a full
Cloudflare Workers rewrite. The current application is a long-running FastAPI
process with a PostgreSQL database and a separate worker process for scheduled
jobs, so the low-risk deployment keeps the Azure VM as the compute layer and
puts Cloudflare in front of it.

### Target path: Cloudflare Tunnel + Azure compute (no VM public IP)

The selected scope is deliberately narrow: Cloudflare manages the
app's zone and one Tunnel only. It is not the application runtime,
database, Workers/Pages host, WAF, or identity layer. Terraform should own the
complete boundary:

- Azure VM, egress-only private connectivity, network security group, systemd,
  and the persistent application disk
- a Cloudflare Tunnel from the VM to `http://127.0.0.1:${APP_PORT}`
- the Tunnel hostname route and DNS record for the configured zone and
  subdomain
- the canonical `PUBLIC_BASE_URL` passed to the app so Twilio signatures match
  the public URL

Cloudflare documents both a [Terraform provider](https://developers.cloudflare.com/api/terraform/)
and a [Terraform-managed Tunnel deployment](https://developers.cloudflare.com/tunnel/deployment-guides/terraform/).
The Cloudflare provider should read `CLOUDFLARE_API_TOKEN` from the shell or a
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
on a Python process, PostgreSQL persistence, and a worker process for scheduled
jobs. A Workers deployment would therefore require a deliberate rewrite to a
Worker entrypoint, D1/KV or Durable Objects for state, and Cron
Triggers/Queues/Workflows for scheduled jobs. See Cloudflare’s [Python Workers
overview](https://developers.cloudflare.com/workers/languages/python/)
and [Python runtime limitations](https://developers.cloudflare.com/workers/languages/python/stdlib/).

Do not select this path unless a platform rewrite is intended.

### IaC ownership and workflow

The desired ownership is:

1. Terraform plans and applies Azure plus Cloudflare resources.
2. Terraform renders the VM environment and cloud-init from variables; secrets
   come from environment variables or a secret manager, never committed files.
3. An operator runs `terraform fmt -check` and `terraform validate` (there is no
   CI in this repository — see [testing.md](./testing.md)), then reviews a
   credentialed `terraform plan` before each production apply.
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
should be committed so every operator resolves the same provider builds.

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
`./scripts/deploy/terraform.sh force-unlock -force <lock ID from the error>`.

Resuming an interrupted apply can hit ARM propagation lag as two
benign-looking failures. A freshly created VNet can answer 404
(`ResourceNotFound`) for a few moments after Azure reports it created, so
`azurerm_subnet.main` fails while "waiting for provisioning state" —
re-running the `apply` a minute later just continues. The same 404 wave
during an apply's refresh can silently drop a real, still-existing resource
(seen with the NSG) from state; the next apply then dies with "A resource
with the ID ... already exists — to be managed via Terraform this resource
needs to be imported". That one does not heal by retrying: re-import it
(`./scripts/deploy/terraform.sh import <addr> <id from the error>`), then apply
again. A failed apply also makes a saved plan file stale, so re-plan rather
than reusing an old `tfplan`.

Bootstrap is the one unavoidable two-phase step: a backend cannot create the
storage account that stores its own first state. Run the bootstrap plan, review
it, and apply it once:

```text
./scripts/deploy/bootstrap_state.sh plan
./scripts/deploy/bootstrap_state.sh apply
./scripts/deploy/terraform.sh init
./scripts/deploy/terraform.sh plan
```

The apply writes the ignored `terraform/backend.hcl`; it contains only the
storage resource names and state key, never the Cloudflare or Twilio secrets.
After that, `scripts/deploy/terraform.sh apply` refuses to run unless the remote
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
just the VM — the resource group is created, then the VNet, NSG, and
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
an ambiguity, not proof that the size cannot allocate: Azure's capacity error
is authoritative. On 2026-08-06, `Standard_B2ats_v2` returned
`AllocationFailed` in `mexicocentral` and Azure explicitly offered
`Standard_B2ts_v2`, which then deployed successfully.

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
  find it. Force it: `./scripts/deploy/terraform.sh plan -replace=azurerm_subnet.main`.
- `azurerm_managed_disk.data` carries `prevent_destroy = true`, so the plan is
  refused outright. If the disk holds real data, back it up per
  [Backups](#backups) first. Only when it is genuinely empty, drop it from
  state: `./scripts/deploy/terraform.sh state rm azurerm_managed_disk.data`.

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

### Outbound internet access (egress)

The Tunnel removes the need for *inbound* connectivity, not outbound.
`cloudflared` works by dialing out to the Cloudflare edge, and cloud-init needs
`apt`, `git`, and `pip`, so the subnet must have an egress path. It gets one
from Azure's implicit outbound access:

```hcl
default_outbound_access_enabled = true
```

This replaced an Azure NAT Gateway on 2026-08-20. The gateway billed roughly
CA$46/month in idle gateway hours — about 60% of all subscription spend — to
carry under 2 GB of traffic, because the meter is per hour of existence rather
than per byte. A single VM never needed the SNAT-port pooling a NAT Gateway
exists to provide. Removing it and its static public IP cut the subscription
run rate from about CA$76/month to about CA$25/month.

The comment that had justified the NAT Gateway claimed new Azure subnets
receive no implicit outbound access. That is not true for this subscription: a
VM created into a subnet with the flag set reaches the internet, Cloudflare's
edge on 7844, and the Ubuntu archives.

Two constraints follow from depending on implicit outbound access:

- **The egress IP is not stable.** It comes from a shared Azure pool and can
  change without notice, so nothing may depend on a fixed source address. In
  particular, keep Postgres on its private endpoint rather than moving it to
  public access with IP firewall rules.
- **Microsoft deprecates this path** and advises against it for production. It
  works today. If it stops, the replacement is a Standard public IP on the NIC
  (about CA$5/month), which leaves the NSG as the only inbound gate.

#### Changing the egress method requires a deallocate, not a reboot

Azure programs a VM's outbound SNAT when the VM is placed on a host. Changing
how the subnet gets egress therefore does **not** reach a VM that is already
running. Terraform reports success and the subnet reports the new setting, but
the VM has no egress at all: `cloudflared` fails with `failed to dial to edge
with quic: timeout` and the public hostname goes dark behind a completely green
apply. A guest reboot does not fix it. The VM has to be deallocated and started
so the platform re-provisions its networking:

```bash
az vm deallocate -g hangout-rg -n hangout-vm
az vm start -g hangout-rg -n hangout-vm
```

`scripts/deploy/verify_egress.sh` runs automatically after
`scripts/deploy/terraform.sh apply` and fails the deploy with exactly those
commands when the VM cannot reach the internet. It is safe to run by hand at
any time; `HANGOUT_SKIP_EGRESS_CHECK=1` bypasses it.

A VM *created* into a subnet that already has the flag set needs none of this.
Only flipping it under a live VM does.

### VM `custom_data` and accidental replacement

Azure treats any change to the VM's `custom_data` (the rendered cloud-init
user-data) as **ForceNew**: Terraform destroys the VM and creates a new one.
Cloud-init only runs on first boot, so day-to-day app releases and most env
tweaks do not need a replace.

The VM resource therefore sets `lifecycle.ignore_changes = [custom_data]`. Plan
and apply will not replace the VM when the template, env vars, or unit file in
`cloud-init.yaml.tftpl` drift from what was last written into state. New VMs
still get the current template on create.

To intentionally rebuild the VM (pick up a new cloud-init, rotate secrets that
only live in user-data, etc.):

```bash
./scripts/deploy/terraform.sh apply -replace=azurerm_linux_virtual_machine.main
```

Expect downtime while Azure recreates the VM. The managed database disk has
`prevent_destroy` and is re-attached; bootstrap waits for the data disk before
starting the app.

#### Pre-flight: prove the new VM's dependencies before destroying the old one

A replace destroys the only working instance *before* creating its
replacement, and cloud-init runs `alembic upgrade head` during boot. Anything
the new VM needs at boot must be proven reachable first — if it is not, the
bootstrap fails and there is no old VM left to fall back to.

A green `apply` does not prove this. Terraform reports success when the
resources exist, not when they work together, and `postgres_host`
(`main.tf`) is a hardcoded string rather than a resource reference, so no
dependency edge forces the check. Verify by observation, not by plan output:

```bash
# The private endpoint's zone must actually hold the A record...
az network private-dns record-set a list -g hangout-rg \
  -z privatelink.postgres.database.azure.com \
  --query '[].{name:name, ips:aRecords[].ipv4Address}' -o json

# ...and the hardcoded host must resolve to that private IP from the VM,
# not to the public address the FQDN carries outside the VNet.
az vm run-command invoke -g hangout-rg -n hangout-vm --command-id RunShellScript \
  --scripts "getent hosts hangout-postgres.postgres.database.azure.com" \
  --query "value[0].message" -o tsv
```

An empty record list, or a resolved address that is not the endpoint's private
IP, means the new VM will fail to bootstrap. Fix that before replacing.

### Recovering a failed VM replacement

An intentional VM replace (or a plan that still force-replaces for other
reasons) causes application downtime while Azure destroys the old VM and
creates the new one. The managed database disk has `prevent_destroy`, but its
attachment is destroyed and re-created as part of the replacement.

An Azure `AllocationFailed` can leave a failed VM resource behind even though
Terraform did not add it to state. The next apply then reports that the VM
already exists and suggests importing it. Do not import until checking whether
the VM actually succeeded:

```bash
az vm show -g <rg> -n <vm> --show-details \
  --query '{powerState:powerState,provisioningState:provisioningState,size:hardwareProfile.vmSize}' -o json
az disk show -g <rg> -n <data-disk> \
  --query '{state:diskState,managedBy:managedBy}' -o json
```

If the VM reports `provisioningState: Failed` and the database disk is
`Unattached`, delete only that failed VM (its disposable OS disk), not the data
disk:

```bash
az vm delete -g <rg> -n <vm> --yes
```

Persist Azure's offered alternative as an explicit ignored deployment input,
for example `TF_VAR_vm_size=Standard_B2ts_v2` in `.env.production`, then re-run
`./scripts/deploy/terraform.sh plan` and `apply`. The recovery plan should create the
VM and data-disk attachment only. After the apply, wait for cloud-init and run
a final Terraform plan; it should report no changes. During the outage,
Cloudflare correctly reports the tunnel as down because its origin is absent.

### Deployment inputs and secret handling

Provide non-secret values in chat or a local `.tfvars` file; provide secrets
only through environment variables or a secret manager:

| Area | Input used by this deployment |
|------|-------------------------------|
| Cloudflare | Zone, app hostname, account ID, and zone ID — all in the ignored `.env.production`, never in this repo |
| Cloudflare | API token exported as `CLOUDFLARE_API_TOKEN`; it needs Tunnel Edit and DNS Edit. Before concluding this token is broken, read item 4 of the [go-live checklist](#production-go-live-checklist-external-prerequisites) — the obvious health-check endpoint reports a healthy token as invalid |
| Compute | Azure CLI login, region/VM size, repository URL/branch, and an SSH public key for the VM administrator (not direct public access) |
| Public URL | `https://<app hostname>`, written to `PUBLIC_BASE_URL` for webhook signature validation |
| SMS | `mock` by default; Twilio SID/token/number must be injected through secret environment variables before selecting `twilio` |
| Google Places | Optional `GOOGLE_MAPS_API_KEY`, injected through the ignored `.env.production` when location autocomplete is enabled |
| Access | `ACCESS_BOOTSTRAP_ADMINS` — comma-separated emails seeded as access-list admins at startup. Not a secret, but it names real people, so it lives in the ignored `.env.production` like the rest |
| State | A locked remote Terraform backend should be configured before production apply |

Never send a Twilio auth token, Cloudflare API token, Azure client secret,
Terraform state, SSH private key, or `.env.production` contents in chat.

### What can be configured from this repository

`./scripts/deploy/terraform.sh init`, `validate`, `plan`, and `apply` use the values in
`.env.production` plus the logged-in Azure CLI. Applying the plan changes the Cloudflare
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

`./scripts/deploy/set_twilio_webhook.py` sets it over the Twilio REST API instead of
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
- Writes `/etc/hangout-automator.env` (with `HANGOUT_ENV=production`, app on
  `127.0.0.1:${APP_PORT}`, and `APP_PORT` supplied from the ignored `.env.production`; DB
  `postgresql+psycopg://<admin>:<password>@<server>.postgres.database.azure.com/hangout`
  from the Postgres variables, `ENABLE_API_DOCS=false`, SMS settings and
  optional Google Places key from Terraform, and `LOG_*` settings for
  `/var/lib/hangout-automator/logs/server.log`)
- systemd unit `hangout-automator.service` running Uvicorn, with
  `RequiresMountsFor=/var/lib/hangout-automator` so the app refuses to start
  without the data disk holding its audit log (the fstab entry is `nofail`, so
  the VM itself still boots)
- systemd unit `cloudflared.service` using the Terraform-created Tunnel token
- systemd unit `hangout-worker.service` running the background jobs (see
  [background-jobs.md](./background-jobs.md)); backups are Azure-managed (see
  Backups below)
- Bootstrap: wait for and mount the persistent data disk, clone `git_repo_url`, create venv, `pip install -r requirements.txt`, run `alembic upgrade head` against the Postgres server (schema migrations are a deploy step, never an app-startup step), then enable/restart the app and worker services

The cloudflared service token, Clerk backend credentials, and SMS secrets are
rendered into root-readable machine configuration and Terraform state; use a
remote encrypted backend and restrict state access. Never commit real
credentials, `.env.production`, Terraform state, or private keys.

### Access control

**Clerk plus the app's own access list is the authentication boundary.**
Cloudflare Access was removed on 2026-08-09 (see
[the removal record](./deployment-history.md)); the edge now passes every
request straight to the app. Clerk verifies *who* a visitor is, the
`access_grants` table decides whether this deployment admits them, and
per-workspace scoping keeps admitted users apart. Both halves are owned by
[tenancy.md](./tenancy.md).

Because `CLERK_ENABLED` is now load-bearing rather than an extra layer,
`scripts/deploy/terraform.sh` refuses `apply` when it is anything but `true`
(override with `HANGOUT_ALLOW_UNAUTHENTICATED_DEPLOY=1` for a deliberately
public instance). It also refuses a `pk_test_` key, a publishable key whose
encoded host disagrees with `CLERK_FRONTEND_API_URL`, and an empty
`ACCESS_BOOTSTRAP_ADMINS` while Clerk is on (override with
`HANGOUT_ALLOW_NO_BOOTSTRAP_ADMINS=1` when the access list is already
populated in the database) — that last one would otherwise ship a deployment
nobody, including the operator, can sign in to.

Two paths are deliberately outside the Clerk session check:

- `/webhooks/sms` — Twilio cannot present a browser session, so the
  `X-Twilio-Signature` check in `app/routers/webhooks.py` is the **sole** gate
  on this path. With `SMS_PROVIDER=mock` there is no external caller and Clerk
  protects it normally. See [sms-and-rsvp.md](./sms-and-rsvp.md).
- `/sign-in` and Clerk's own handshake routes, which must be reachable
  unauthenticated for anyone to sign in at all.

Clerk sign-*up* is open and cannot be narrowed on this plan (Clerk's allowlist
and blocklist are paid features), so strangers can still create accounts
against the instance. The app refuses them: without an `access_grants` row for
their verified email they get a 403 on every route and no workspace. Adding
someone is Admin → Access, by an admin. `ACCESS_BOOTSTRAP_ADMINS` seeds the
first admins at startup and is the recovery path if the last one is lost.

Defense in depth beyond Clerk:

- `ENABLE_API_DOCS=false` on the VM, so `/docs`, `/redoc`, and `/openapi.json`
  do not publish a map of the state-changing endpoints
- `cloudflare_hostname` has no default, so the deployed hostname is not
  committed here — though earlier commits in this public repository still
  contain it, so treat it as known

The SMS webhook is rate-limited per source phone and globally (Postgres
counters, 429 past the ceilings); the Twilio signature check still runs first.

The Zero Trust layer that used to front this hostname is gone; its removal, the
exit criteria it had to clear first, and what is now unprotected are recorded in
[deployment-history.md](./deployment-history.md).

### Backups

Backups are **off-site by the database server itself**. The Flexible Server is
provisioned with `backup_retention_days = 35`, so Azure keeps rolling daily
backups plus transaction-log point-in-time restore (PITR) for the last 35
days, stored in Azure's own backup storage — not on the VM's data disk. The
old `hangout-backup.sh` / `.service` / `.timer` no longer exist.

- **Restore a point in time** (deleted rows, bad migration, corruption):
  create a new Flexible Server from a restore point in the portal/CLI
  (`az postgres flexible-server restore --source-server … --restore-time
  …`), point `DATABASE_URL` at it, run `alembic upgrade head`, and cut over.
  The original server's `prevent_destroy` keeps Terraform from replacing it.
- **Off-site scope**: backups live in Azure storage, so losing the whole VM
  does not lose the database. The rotating audit log still sits on the VM's
  data disk (`/var/lib/hangout-automator/logs/`); copy it off the VM when
  preserving trace history matters.
- **Before an irreversible migration or a production cutover**: take an extra
  pre-change restore point (`az postgres flexible-server restore` to a scratch
  server, or a manual PITR restore) rather than trusting only the rolling
  windows.

### Tearing down

`terraform destroy` fails while the data disk and the Flexible Server both
carry `prevent_destroy = true`. The disk holds the audit log; the database
lives on the Azure-managed server (with its own 35-day backups). To
intentionally destroy the environment, export the audit log first, then drop
both from state (`terraform state rm azurerm_managed_disk.data
azurerm_postgresql_flexible_server.main`) and destroy; the disk and the server
survive in Azure and must be deleted by hand once you are sure.

## Production go-live checklist (external prerequisites)

The normal Azure/Cloudflare resources are automated by Terraform. These are the
remaining external or operator-controlled steps:

1. **Twilio**: provision an SMS-capable phone number; inject the three Twilio credentials and select `SMS_PROVIDER=twilio`; then run `./scripts/deploy/set_twilio_webhook.py` to register the messaging webhook. Use a number not already serving another app — see [Registering the Twilio webhook](#registering-the-twilio-webhook).
2. **State**: initialize the chosen remote backend before the first production apply.
3. **Clerk**: a **production** instance (`pk_live_`/`sk_live_`) with its five CNAMEs verified and a certificate issued for `clerk.<hostname>`, since Clerk is the only authentication boundary. Decide sign-up policy in the Clerk Dashboard under **Configure → Restrictions**: the default is open registration, and this repo provisions a workspace for whoever signs up. Cloudflare Zero Trust is no longer a prerequisite — Access was removed on 2026-08-09.
4. **Cloudflare API token write scopes**: Cloudflare grants Read and Edit as
   separate permission groups, and a plan cannot tell them apart — every
   resource in it is a create, so nothing is exercised until apply. A token
   holding only Tunnel Read lists tunnels happily and then fails
   `POST /cfd_tunnel` with 403 `10000` *during* apply, after most of the Azure
   network already exists. Probe the writes
   first; an empty body creates nothing, and 400 means the permission is
   present and only the body was rejected, while 403 means it is missing:

   ```bash
   for p in "accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel" \
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
   usable health check here. Fixing a missing scope is a dashboard step,
   because editing an API token over the API needs a token carrying User: API
   Tokens Edit, which a deployment token normally does not have.

   The tunnel permission is not listed under the name the product now uses.
   In the token editor it is **Cloudflare One / Zero Trust → `Argo Tunnel
   (Legacy)`**, which is the original name for what Cloudflare rebranded as
   Cloudflare Tunnel; it still gates the `cfd_tunnel` endpoints. Its
   description reads "Grants access to view Cloudflare Tunnels" because that
   describes the Read box — tick **Edit**, which implies Read. The nearby
   `Connectivity Directory` entry also mentions tunnels but is Magic WAN and
   grants nothing here.
5. **Azure**: `az login`, then review `./scripts/deploy/terraform.sh plan` and run `./scripts/deploy/terraform.sh apply`.
6. **Postgres**: after the server exists (the flexible server, private DNS
   zone, and private endpoint are Terraform resources), `POSTGRES_ADMIN_PASSWORD`
   must be in the ignored `.env.production` before any plan/apply. cloud-init runs
   `alembic upgrade head` during bootstrap, so the schema is already applied by
   the time the app starts.
7. **Clerk production instance**: the deployment runs Clerk *development* keys
   (`pk_test_` / `sk_test_`, on a `*.clerk.accounts.dev` frontend API). Those
   are user-capped and not intended for production traffic, so a production
   instance is required before real tenants exist.

   It is registered as a **secondary application** on `hangout.bahasadri.com`,
   not as the primary application for `bahasadri.com`. The apex zone serves
   other things, and a primary registration would have Clerk claim
   `clerk.bahasadri.com` and send verification mail as `@bahasadri.com` — a
   zone-wide commitment this one app should not make. As a secondary
   application Clerk's API is hosted at `clerk.hangout.bahasadri.com` and
   verification mail comes from `@hangout.bahasadri.com`, keeping the whole
   footprint under the subdomain the app already owns.

   Clerk then issues five CNAME records (frontend API, account portal, and the
   mail/DKIM entries) for the `bahasadri.com` zone. `cloudflare_dns_record.clerk`
   in `terraform/cloudflare.tf` creates them, keyed off `clerk_dns_id`.

   **Do not use Clerk's "configure automatically" Cloudflare integration on a
   zone Terraform manages.** It does not adopt existing records — it deletes
   them and creates its own with new record ids, which strands Terraform's
   state on five ids that no longer exist. The failure is delayed and ugly: a
   plan reports `5 to add` with `Warning: Resource not found`, and the apply
   then fails partway trying to create CNAMEs that already exist (Cloudflare
   error 81053). Recovery is `terraform state rm` plus `terraform import
   '<zone id>/<record id>'` for each of the five, reading the live ids from
   `GET /zones/<zone id>/dns_records`. The records' `ttl` is `3600` to match
   what that integration writes, so a re-sync does not show up as drift.

   Terraform manages individual `cloudflare_dns_record` resources rather than
   the zone as a whole, so *hand-adding* a record is safe; it is specifically
   the delete-and-recreate integration that breaks state.

   **Wait for the certificate before swapping keys — "verified" is not it.**
   Clerk go-live has two stages and only the first is visible: the dashboard
   turns green once it has *read* the CNAMEs, then Clerk provisions the TLS
   certificate for `clerk.<host>` some time later. `GET /v1/domains` carries no
   status field and its `updated_at` does not move when the certificate lands,
   so the dashboard cannot tell you the difference. Before the certificate
   exists the host answers TLS with `alert 40` / `no peer certificate
   available`, and deploying live keys into that window is a **total outage**:
   `app/auth.py` fails closed, so an unreachable JWKS makes every protected
   route a 503.

   The only honest readiness check is the endpoint itself:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' \
     https://clerk.<host>/v1/environment   # 200 = ready; 000 = certificate pending
   ```

   Do not run that check through a resolver that cached `NXDOMAIN` before the
   records existed. The zone's SOA sets a 1800s negative TTL, and a stale
   local resolver makes curl fail with `Could not resolve host`, which is
   indistinguishable from "not ready" if you only look at the status code. Pin
   the lookup when in doubt:
   `--resolve clerk.<host>:443:$(dig +short clerk.<host> @1.1.1.1 | grep -E '^[0-9]' | head -1)`.

   Afterwards set `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`,
   `CLERK_FRONTEND_API_URL` (which becomes `https://clerk.hangout.bahasadri.com`),
   and `ACCESS_BOOTSTRAP_ADMINS` in the ignored `.env.production`. The wrapper refuses to
   apply if `CLERK_ENABLED=true` with a `pk_test_` key, if the publishable
   key's encoded host disagrees with `CLERK_FRONTEND_API_URL`, or if
   `ACCESS_BOOTSTRAP_ADMINS` is empty. **These reach the VM only through
   cloud-init**, whose `custom_data` is under `ignore_changes` — a plain
   `apply` will not deliver new keys. Either replace the VM or edit
   `/etc/hangout-automator.env` on the host and restart the units; a replace is
   the one that keeps the VM matching what Terraform would build.

### Updating a running VM

The VM has no inbound SSH, so a code update goes through Run Command and
mirrors what cloud-init does at first boot — pull, install, **migrate, then
restart**:

```bash
az vm run-command invoke -g <rg> -n <vm> --command-id RunShellScript --scripts "
set -e
cd /opt/hangout-automator
git -c safe.directory=/opt/hangout-automator fetch --all --prune
git -c safe.directory=/opt/hangout-automator pull --ff-only origin main
.venv/bin/pip install -q -r requirements.txt
chown -R <admin_username>:<admin_username> /opt/hangout-automator
set -a; . /etc/hangout-automator.env; set +a
.venv/bin/alembic upgrade head
systemctl restart hangout-automator hangout-worker
" --query "value[0].message" -o tsv
```

That order is not stylistic. Running code tolerates a schema that is ahead of
it; new code meeting an old schema does not — the access-list bootstrap reads
`access_grants` before the first request, so restarting ahead of its migration
leaves a process that starts but refuses every sign-in (see
[tenancy.md](./tenancy.md)). Migrating first keeps that window at zero.

New *environment variables* are a separate problem with a separate answer:
they live in `/etc/hangout-automator.env`, and Terraform cannot deliver them to
a running VM (see the `custom_data` note above). Before upgrading an existing
VM to code that selects environment-specific files, add
`HANGOUT_ENV=production` to that file and restart both app units; otherwise the
runtime defaults to development and deliberately rejects the live Clerk keys.

### Verifying a fresh deploy

The VM has no inbound access, so smoke-test from two directions. On the VM,
via Run Command — cloud-init should report `status: done` with no errors, all
three units active, and the app answering locally:

```bash
az vm run-command invoke -g <rg> -n <vm> --command-id RunShellScript \
  --scripts "set -a; . /etc/hangout-automator.env; set +a; cloud-init status --long; systemctl is-active hangout-automator hangout-worker cloudflared; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:\${APP_PORT}/" \
  --query "value[0].message" -o tsv
```

Externally, `GET https://<hostname>/webhooks/sms` should return **405** from
the app itself — that one response proves DNS, the tunnel, and Uvicorn all at
once. `GET /` should redirect (302) to the app's own `/sign-in`, and
`GET /api/hangouts` with no session should return **401**, not a workspace.
That 401 is the check worth keeping: it proves the app refuses an
unattributable request rather than resolving it to the shared `default`
workspace, which is the behavior Cloudflare Access used to make moot.

To verify the exact release from Azure Run Command, Git can reject the app
directory as being owned by a different service user. Use a per-command safe
directory override instead of changing global Git configuration:

```bash
az vm run-command invoke -g <rg> -n <vm> --command-id RunShellScript \
  --scripts "git -c safe.directory=/opt/hangout-automator -C /opt/hangout-automator rev-parse HEAD" \
  --query "value[0].message" -o tsv
```

## Rsync updates

`./scripts/deploy/rsync.sh user@host` is a legacy public-SSH updater and is not
usable for this private-VM topology. Initial code and service setup come from
Terraform-rendered cloud-init; a future release pipeline should use Azure Run
Command or another private management path for updates.

It excludes `.env` and `.env.*`, `terraform.tfvars`, `backend.hcl`, state files, and all
`*.db*` files, so running it cannot copy local or production secrets or a development
database onto a VM. It runs `alembic upgrade head` before the restart, for the
same ordering reason as above; it had synced code and restarted without ever
migrating, which is survivable only while no release carries a migration.

It also runs `rsync --delete` against `/opt/hangout-automator`, so whatever
`ROOT` resolves to *becomes* the deployed tree and everything else there is
erased. `ROOT` is computed by walking up from `$0`, which means moving this
script between directories silently changes what it deploys — when the deploy
scripts were moved from `scripts/` into `scripts/deploy/`, the unchanged
one-level walk left `ROOT` pointing at `scripts/`. Nothing would have failed;
it would have synced `scripts/` over the application and deleted the rest. Any
script that derives a path from its own location needs that path re-checked
when it moves, from both inside and outside the repo.
