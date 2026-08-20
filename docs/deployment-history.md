# Deployment history

An append-only log of what was actually deployed, when, and what went wrong.
[deploy.md](./deploy.md) owns *how* to deploy; this file owns *what happened*.

Add an entry for every apply that changes real infrastructure, including the
ones that fail — a failed apply and its cause is the most valuable thing here.
Newest first. Never record a secret: name the variable, not its value.

Dates are UTC.

---

## 2026-08-20 — Postgres moved from a private endpoint to VNet integration

Terraform apply: `5 added, 0 changed, 5 destroyed`. Replaced
`azurerm_postgresql_flexible_server.main` and its database, replaced the private
DNS zone (`privatelink.` -> `hangout.private.`), deleted
`azurerm_private_endpoint.postgres`, and added `azurerm_subnet.postgres`
(`10.20.2.0/24`, delegated to `Microsoft.DBforPostgreSQL/flexibleServers`). The
VM, tunnel, DNS records, and disks were untouched.

Why: the private endpoint billed about CA$7/month for reachability that
delegated-subnet VNet integration provides for free. It had been chosen because
integration needs a subnet dedicated to the database and the app VM occupies
`10.20.1.0/24`; giving the database its own subnet removes that constraint.
`prevent_destroy` was removed from the server deliberately — the mode is fixed
at creation, so this could only be done by replacing the server.

**Data.** Dumped first with `pg_dump -Fc` (postgresql-client-17 installed from
PGDG; Ubuntu 24.04 ships 16, which refuses to dump a 17 server). Verified the
dump off the box: uploaded to the `dbbackup` container in
`hangoutstate37d24562` via a short-lived user-delegation SAS, downloaded to
`~/hangout-backups/`, sha256 matched, `pg_restore -l` listed 30 objects. The
database was nearly empty — 3 access grants, 1 user, 2 workspaces, 2 allergies,
every other table 0 rows, 62 KB dump. Row counts after restore matched the
pre-migration manifest exactly. **Deleting a Flexible Server deletes its
backups**, so the 35-day retention window restarted at zero on the new server.

Two things that were expected to break and did not:

- **`DATABASE_URL` needed no change.** Azure registers the server in the
  private zone under a generated label (`c339107ca9ef`), not the server name,
  and CNAMEs the ordinary public FQDN at it, so
  `hangout-postgres.postgres.database.azure.com` still resolves inside the
  VNet — now to `10.20.2.4`. An earlier edit that constructed a
  `<server>.<zone>` host was wrong and was reverted; that name does not exist.
- **`/etc/hangout-automator.env` needed no edit**, so the usual cloud-init
  staleness trap did not apply. Only a `systemctl restart` of
  `hangout-automator` and `hangout-worker` was needed.

Follow-up drift: Azure adds a `Microsoft.Storage` service endpoint to its own
delegated subnet at creation, which Terraform then plans to strip. Pinned in
config, same as the server's `zone` attribute, so the plan is clean.

Combined with the NAT Gateway removal earlier the same day, `hangout-rg`
networking went from about CA$59/month to about CA$0.50/month.

---

## 2026-08-20 — Removed the NAT Gateway; egress moved to default outbound access

Terraform apply: `0 added, 1 changed, 4 destroyed`. Destroyed
`azurerm_nat_gateway.main`, `azurerm_public_ip.nat`, and both associations;
changed `azurerm_subnet.main` in place to
`default_outbound_access_enabled = true`. No VM, database, or DNS record was
touched.

Why: the NAT Gateway was 58.7% of the month's Azure spend — CA$20.11 of
CA$34.24 — and its meter is per hour of existence, not per byte. Metrics showed
it had carried 1.8 GB in 13 days, worth CA$0.11 at the data-processing rate.
The subscription had spent 25.58% of the student credit, nearly all of it after
`hangout-rg` was created on 2026-08-06: daily spend went from CA$0.24 to
CA$2.57 that day. Run rate is now roughly CA$25/month, down from roughly
CA$76/month, moving credit runway from about 42 days to about 4 months.

The NAT Gateway existed because of a wrong premise recorded in a `main.tf`
comment: that new Azure subnets receive no implicit outbound access. A test VM
created in a throwaway subnet with `default_outbound_access_enabled = true`
reached the internet, Cloudflare's edge on 7844, and the Ubuntu archives,
disproving it. The throwaway subnet and VM were deleted.

**What went wrong.** The apply succeeded and left the site down. Azure programs
a VM's outbound SNAT at placement, so flipping the subnet flag under the
already-running VM never reached it: egress stopped entirely and `cloudflared`
looped on `failed to dial to edge with quic: timeout: no recent network
activity`. Nothing in the apply output indicated this. A guest reboot would not
have helped — `az vm deallocate` then `az vm start` fixed it, after which
cloudflared re-registered all four edge connections (dfw06/07/08/11) and
`https://hangout.bahasadri.com/` returned 303 again. The egress address is now
`158.23.144.8` from Azure's shared pool, replacing the NAT's static
`68.155.154.48`; nothing may depend on it staying that value.

Guard added so this cannot recur silently: `scripts/deploy/verify_egress.sh`
runs after every `terraform.sh apply` and fails the deploy with the deallocate
commands when the VM cannot reach the internet. See
[deploy.md](./deploy.md#outbound-internet-access-egress).

Left undone: the orphaned OS disk
`hangout-vm_OsDisk_1_c13d8d4ff57b4ccaaa322333c537a3ab` (unattached, not in
Terraform state, ~CA$0.40/month). The Postgres private endpoint (~CA$7/month)
is now the largest remaining networking line; VNet integration would make it
free but forces a server replacement against `prevent_destroy = true`.

---

## 2026-08-11 — App release to main via Azure Run Command

Commit: `162631e` (Include public site URL in outbound SMS, KIAN-533)  
Previous: `33d818a` (Clerk production cutover era)

Command path: Azure Run Command on `hangout-vm` (no SSH; not a Terraform
apply). Sequence: `git pull --ff-only origin main` → `pip install` → add missing
env keys → `alembic upgrade head` → `systemctl restart hangout-automator
hangout-worker`.

Migrations applied: `db04909245fd` → `7c4be1d90a52` (access grants) →
`a8f3c2e19b04` (users / my profile) → `c3e8f1a92b04` (SMS opt-outs). Head is
`c3e8f1a92b04`.

Env gap on the long-lived VM (cloud-init only runs at first boot; Terraform
`ignore_changes` on `custom_data` does not refresh `/etc/hangout-automator.env`):

- Added `HANGOUT_ENV=production` — without it the new config code defaults to
  development and refuses live `pk_live_` Clerk keys, so `alembic` and the app
  both fail at import.
- Added `ACCESS_BOOTSTRAP_ADMINS` (value from operator `.env.production`, not
  recorded here). Startup logged `access.bootstrap_admins_granted` and
  `access.bootstrap_complete`.

First Run Command attempt failed immediately on `set -o pipefail` because the
agent executes scripts with `sh` (dash), not bash. Retried with POSIX `set -e`.

Verified after restart:

- Units: `hangout-automator`, `hangout-worker`, `cloudflared` all `active`
- Local: `/` → 303, `/webhooks/sms` → 405, `/api/hangouts` → 401
- External (`https://hangout.bahasadri.com`): same codes; `/sign-in` → 200

Notable code included in this jump (not an exhaustive list): app-owned access
list, multi-tenant workspace scoping already on main, My Profile / Settings,
Admin Panel + costs, SMS opt-out, public site URL in outbound SMS, contacts
rename, SMS simulator.

## 2026-08-09 — Cloudflare Access removed; Clerk is the only auth boundary

Command: `./scripts/deploy/terraform.sh apply`
Plan: 0 to add, 0 to change, 4 to destroy

Destroyed `cloudflare_zero_trust_access_application.app`,
`.webhook[0]`, `cloudflare_zero_trust_access_policy.owner`, and
`.webhook_bypass[0]`. No Azure resource was touched and the VM was not
replaced — Access lived entirely at the Cloudflare edge, so nothing had to be
redeployed. The account's built-in `App Launcher` app remains; it is
Cloudflare's default and gates nothing here.

Exit criteria from the retirement plan, as they stood at removal:

1. `current_workspace` fails closed — done in the previous cutover.
2. Production Clerk instance with live keys — done in the previous cutover.
3. **Sign-up restriction — not met, accepted deliberately.** Clerk is on open
   registration with `total_count: 0` users. See the exposure note below.
4. Isolation matrix green — 30 tests in `tests/test_tenant_isolation.py`,
   full suite 325 passed.
5. `default` workspace holds no real data — verified against production: one
   workspace, zero members, zero profiles/hangouts/invites/message_logs, and
   2 leftover `allergies` rows.

**What is now unprotected.** The email allowlist that came out held three
addresses, so this was never a single-user deployment. Anyone who creates a
Clerk account now gets their own workspace. Tenant data stays isolated —
criterion 4 covers that — but a stranger can drive the organizer features,
which send SMS on the deployment's shared Twilio credentials at the operator's
expense. Closing this is a Clerk Dashboard setting (**Configure →
Restrictions**), not a code change; the Backend API exposes
`/v1/instance/restrictions` as `PATCH` only, and `GET` returns 405, so the
current restriction state cannot be read back programmatically.

A third guard went into `terraform.sh` alongside the two Clerk-key ones: it
refuses `apply` unless `CLERK_ENABLED=true`. That switch used to select
"one auth layer or two"; with Access gone it selects "authenticated or open to
the internet", and the failure is silent at apply time. Override is
`HANGOUT_ALLOW_UNAUTHENTICATED_DEPLOY=1`.

The audit log's `access_identity` field was dropped from
`app/event_logging.py`. It read `CF-Access-Authenticated-User-Email`, which
without an edge authenticator stamping it is unauthenticated client-supplied
text — recording it would have attributed requests to a forged address.
`tests/test_logging.py` now asserts the field is absent rather than populated.

Verified after apply, externally: `/` → 303 to the app's own
`/sign-in?redirect_url=%2F` (previously a 302 to the Cloudflare Access login),
`/api/hangouts` with no session → 401, `/webhooks/sms` → 405 from the app,
`/sign-in` → 200. `GET /accounts/<id>/access/apps` lists only `App Launcher`.

## 2026-08-09 — Clerk production cutover: live keys delivered to the VM

Commit: `33d818a`
Command: `./scripts/deploy/terraform.sh apply -replace=azurerm_linux_virtual_machine.main`
Plan: 2 to add, 0 to change, 2 to destroy

The development Clerk instance (`pk_test_`) was replaced by a production one.
Because `main.tf:156` sets `ignore_changes = [custom_data]`, a plain apply would
have delivered nothing — the keys only reach the VM through a deliberate
replace. The fail-closed tenancy fix rode along in the same rebuild.

**Most of the elapsed time was spent waiting on a stage with no status field.**
Clerk's go-live is two stages: DNS verification, then certificate issuance for
`clerk.<host>`. The dashboard shows only the first. It went green at roughly
`05:15Z`; the certificate did not land until `05:51:33Z` — 36 minutes later,
during which `clerk.hangout.bahasadri.com` answered TLS with
`alert 40` / `no peer certificate available`. Deploying in that window would
have taken the app down, since `app/auth.py` fails closed and every protected
route 503s when JWKS is unreachable. The readiness gate that worked was polling
`GET /v1/environment` for a `200`, nothing in the dashboard or API.

**A readiness poll spent 30 cycles measuring the wrong thing.** It reported
status `000`, read as "certificate not issued". The actual error was
`curl: (6) Could not resolve host`: this machine's resolver had cached
`NXDOMAIN` from before the records existed, and the zone SOA sets a 1800s
negative TTL. The `dig` half of the poll used `@1.1.1.1` and was fine; the
`curl` half went through the stale system resolver. Pinning with
`--resolve <host>:443:<ip>` against the IP from `dig @1.1.1.1` fixed it. A
failing DNS lookup and an unissued certificate are indistinguishable by status
code alone.

**Clerk's "configure automatically" button broke Terraform state.** The
dashboard integration *deletes and recreates* the five CNAMEs rather than
updating them, so every record ID in state pointed at a deleted record.
`terraform plan` reported `5 to add` with `Warning: Resource not found`; an
apply would have tried to create records that already exist (Cloudflare 81053)
and died partway. Repaired with `state rm` then `import` for all five, reading
live IDs from `GET /zones/<id>/dns_records`. `cloudflare.tf` now pins
`ttl = 3600` to match what the integration writes, so a future re-sync is not
drift. Note the distinction: hand-adding a record alongside Terraform is safe;
it is specifically the integration's delete-recreate that breaks state.

Two guards were added to `terraform.sh` for failure modes that are silent at
apply time: it refuses `CLERK_ENABLED=true` with a `pk_test_` key, and refuses
a publishable key whose encoded host disagrees with `CLERK_FRONTEND_API_URL`.
Both passed on this apply.

Verification: cloud-init `done` with `errors: []`; `hangout-automator`,
`hangout-worker`, `cloudflared` all active; local `GET /` → 303; external
`/webhooks/sms` → 405, `/` and `/api/health` → 302; data disk mounted;
`alembic_version = db04909245fd` (local head), 12 tables. The two that matter
for this cutover: JWKS fetched **from the VM** → `200`, and `/sign-in` serves a
`pk_live_` key pointing at `clerk.hangout.bahasadri.com`. Unauthenticated
`/api/hangouts` → `401`, confirming the fail-closed tenancy fix is live.

Follow-up: `CLERK_SECRET_KEY` and `POSTGRES_ADMIN_PASSWORD` were both exposed in
an operator chat transcript during this work and need rotating.

## 2026-08-09 — Postgres cutover: Flexible Server created, VM rebuilt

App commit deployed: `f20812c`. Three applies, all succeeded.

| # | Command | Plan | Result |
|---|---------|------|--------|
| 1 | `apply` | 5 add / 0 change / 0 destroy | Flexible Server, database, private DNS zone, VNet link, private endpoint |
| 2 | `apply` (after the DNS fix below) | 2 add / 1 change / 2 destroy | zone + link replaced, endpoint repointed, `zone = "1"` pinned |
| 3 | `apply -replace=azurerm_linux_virtual_machine.main` | 2 add / 0 change / 2 destroy | VM and data-disk attachment replaced; the disk itself survived on `prevent_destroy` |

The pre-Postgres SQLite VM is gone. Its database was judged disposable and was
not migrated; persistence for the data disk is deferred to later work.

**The private DNS zone was named wrong, and it would have bricked the rebuild.**
`postgres.tf` created `private.postgres.database.azure.com`. That name belongs
to Flexible Server's *delegated-subnet* VNet integration; this deployment uses a
*private endpoint*, which does not get to choose the name. Azure CNAMEs the
server FQDN to `<server>.privatelink.postgres.database.azure.com`, so only a
zone with that exact name receives the endpoint's A record. Symptoms after
apply #1: the zone held no A records, the endpoint's zone group listed
`recordSets: []`, and the FQDN resolved via public DNS to `158.23.18.130` — a
public address on a server with `public_network_access_enabled = false`.

Caught between apply #1 and the VM replace, by checking that the hardcoded
`postgres_host` actually resolved to the endpoint's private IP. Had the replace
run first, cloud-init's `alembic upgrade head` would have failed against an
unreachable database and left a broken VM with the old one already destroyed.
The zone name now carries a comment explaining why it is not a free choice.

Verification: cloud-init `done` with `errors: []`; `hangout-automator`,
`hangout-worker`, `cloudflared` all active; local `GET /` → 303; external
`/webhooks/sms` → 405 and `/` → 302; `/var/lib/hangout-automator` mounted;
deployed commit `f20812c`. Database reached over TLS from the VM —
PostgreSQL 17.10, `alembic_version = db04909245fd` (local head), 12 public
tables.

## 2026-08-09 — false credential alarm (no infrastructure changed)

A deploy was delayed by roughly an hour on a misdiagnosis. Recorded because the
fix was already written down and went unread.

`GET /client/v4/user/tokens/verify` returned `401` / `1000 Invalid API Token`.
That was read as a dead credential, and the failure was attributed first to the
API token's IP filter and then to the token value itself. Neither was true: the
endpoint requires a User-level scope the deployment token deliberately lacks,
so it reports a perfectly healthy account-scoped token as invalid. The token
had been working the whole time, and the plan that eventually ran was clean on
the first attempt.

Cost: one needless token roll, two needless Cloudflare dashboard edits, and a
`CLOUDFLARE_API_TOKEN_ALLOWED_IP` note in `.env` that is now stale in the other
direction (two IPs are allowed; the note lists one, and nothing reads it).

`deploy.md` already documented this exact false negative under go-live
checklist item 4. It was missed because the search that should have found it
truncated its output immediately above the relevant paragraph. The lesson is
not "write it down" — it was written down. It is that a scoped credential must
be tested against the endpoints it is scoped for, and that a grep whose output
is cut short has not confirmed an absence.

## 2026-08-09 — pre-deploy audit (no infrastructure changed)

No apply was run. Recorded because it corrected two beliefs that were wrong in
the repo's own commit history.

**Deployed stack is pre-Postgres.** `terraform state list` returns 22 resources
— VM, data disk, NAT gateway, NSG, VNet/subnet, Cloudflare tunnel, DNS record,
Access apps — and **none** of the five resources in `postgres.tf` (Flexible
Server, its database, private DNS zone, VNet link, private endpoint). The
running VM is the SQLite-era application. The repo had been treated as though
nothing was deployed; that was never checked against remote state.

**The SQLite cutover tooling was deleted on a false premise.** Commit `8a3e9e5`
removed `scripts/sqlite-cutover/` reasoning that "no SQLite database exists in
any environment". That conclusion came from the local machine only. A live
SQLite database exists on the deployed VM's data disk. The data was judged
disposable, so the deletion stands and the cutover is deliberately skipped —
but if a future deploy ever needs it, recover it with `git revert 8a3e9e5`.

**`custom_data` changes do not replace the VM.** Commit `ac2eb97` states that
editing `cloud-init.yaml.tftpl` means "the next apply replaces the VM". That is
wrong. `main.tf:156` sets `ignore_changes = [custom_data]` exactly so Azure's
ForceNew behaviour cannot cause an unplanned rebuild. Cloud-init edits reach a
running VM only via a deliberate
`apply -replace=azurerm_linux_virtual_machine.main`.

State of the plan at audit time: `5 to add, 0 to change, 0 to destroy` (the
five Postgres resources), plus two no-op state moves where the webhook Access
application and policy gained a count index.

## Standing gotchas

Things that have bitten, or would have. Keep this list short and real.

- **The VM has no dependency on the Postgres resources.** `postgres_host`
  (`main.tf:146`) is a hardcoded hostname string, not a resource reference, so
  Terraform will happily build the VM and the Flexible Server in parallel.
  Cloud-init runs `alembic upgrade head` at boot, so a VM created alongside a
  not-yet-ready server fails to bootstrap. Always let the Postgres apply finish
  before replacing the VM.
- **A VM replace is downtime**, and the data-disk *attachment* is destroyed and
  recreated even though the disk itself has `prevent_destroy`.
- **`Standard_B2ats_v2` on Azure for Students**: an `AllocationFailed` can leave
  a VM behind that is not in state. See the recovery notes in
  [deploy.md](./deploy.md) before importing anything.
- **A killed plan strands the state lock.** Confirm no terraform process is
  running, then `terraform.sh force-unlock -force <lock ID>`.
- **A successful apply does not mean the pieces work together.** Terraform
  reports success when resources exist. Twice now that has hidden a broken
  deployment: a private DNS zone created, linked, and holding no records, and
  an endpoint reporting `Approved` while its FQDN resolved to a public address.
  Nothing forced a check because `postgres_host` is a hardcoded string with no
  dependency edge. Before any destructive apply, verify the new dependency by
  observation — see the pre-flight in [deploy.md](./deploy.md).
- **Verify a scoped credential against the endpoints it is scoped for.** A
  generic health-check endpoint can fail for reasons that have nothing to do
  with the credential's health, and its error message will not say so.
- **A truncated search has not proven an absence.** Both wrong turns on
  2026-08-09 came from concluding something was missing or broken on the
  strength of output that had been cut short, or of a check that could not see
  the thing being asked about.
- **Clerk "verified" is DNS detection, not a working Frontend API.** The
  dashboard turning green means Clerk read the five CNAMEs. Issuing the
  certificate for `clerk.<host>` is a separate, later stage on their side, and
  nothing in the dashboard or the API distinguishes the two —
  `GET /v1/domains` has no status field at all, and its `updated_at` does not
  move when issuance completes. Until the certificate exists the host answers
  TLS with `alert 40` / `no peer certificate available`. Deploying live keys in
  that window takes the whole app down, because `app/auth.py` fails closed and
  every protected route becomes a 503 when JWKS is unreachable. Test the
  endpoint, never the dashboard: `curl https://clerk.<host>/v1/environment`
  must return `200`.
- **Clerk's "configure automatically" button deletes and recreates the DNS
  records.** It is not idempotent and not additive, so every record ID
  Terraform holds goes stale the moment it runs. Symptom is a plan that says
  `N to add` with `Warning: Resource not found`; applying it hits Cloudflare
  `81053` (record already exists) partway through. Recover with `state rm` +
  `import` per record. Hand-adding a record alongside Terraform does *not*
  cause this — only the integration does.
- **A cached `NXDOMAIN` makes a readiness poll lie.** Curl reports status `000`
  for "could not resolve host" and for "host is up but TLS failed" alike, and
  this zone's SOA sets a 1800s negative TTL, so a resolver that cached the
  miss before the records existed stays wrong for half an hour. Any poll for a
  freshly created hostname must pin the lookup —
  `--resolve <host>:443:$(dig +short @1.1.1.1 <host> | tail -1)` — or it is
  measuring the local resolver, not the service.
- **`GET /user/tokens/verify` reports a healthy deployment token as invalid.**
  It needs a User-level scope that the deployment token deliberately does not
  carry, so it answers `401` / `1000 Invalid API Token` no matter how healthy
  the token is. Probe the endpoints the token *is* scoped for instead —
  `accounts/<id>/cfd_tunnel`, `accounts/<id>/access/apps`, `zones/<id>` should
  all return `200` / `success: true`. Note the discriminator: a genuine
  IP-filter rejection is `403` / `9109 Cannot use the access token from
  location`, never `1000`.

## Environment

| | |
|---|---|
| Subscription | Azure for Students |
| Database | `hangout-postgres`, Flexible Server 17, `B_Standard_B1ms`, zone 1, private endpoint at `10.20.1.5`, no public access, 35-day backup retention |
| Remote state | Azure Storage, bootstrapped 2026-08-08 (`bootstrap-state`, serial 8, 5 resources) |
| Secrets source | the gitignored `.env`; `POSTGRES_ADMIN_PASSWORD` was generated 2026-08-09 and exists nowhere else |
| SSH key | `~/.ssh/github-auth.pem.pub` (the wrapper's default path) |

## Entry template

```
## YYYY-MM-DD — what this apply was for

Commit: <sha>
Command: ./scripts/deploy/terraform.sh apply [-replace=...]
Plan: N to add, N to change, N to destroy

What happened, what broke, what the fix was. Verification: cloud-init status,
the three units, the local curl, and the external 405 on /webhooks/sms.
```
