# Deployment history

An append-only log of what was actually deployed, when, and what went wrong.
[deploy.md](./deploy.md) owns *how* to deploy; this file owns *what happened*.

Add an entry for every apply that changes real infrastructure, including the
ones that fail — a failed apply and its cause is the most valuable thing here.
Newest first. Never record a secret: name the variable, not its value.

Dates are UTC.

---

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
