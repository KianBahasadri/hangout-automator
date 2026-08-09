# Deployment history

An append-only log of what was actually deployed, when, and what went wrong.
[deploy.md](./deploy.md) owns *how* to deploy; this file owns *what happened*.

Add an entry for every apply that changes real infrastructure, including the
ones that fail — a failed apply and its cause is the most valuable thing here.
Newest first. Never record a secret: name the variable, not its value.

Dates are UTC.

---

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
