# Deployment history

An append-only log of what was actually deployed, when, and what went wrong.
[deploy.md](./deploy.md) owns *how* to deploy; this file owns *what happened*.

Add an entry for every apply that changes real infrastructure, including the
ones that fail — a failed apply and its cause is the most valuable thing here.
Newest first. Never record a secret: name the variable, not its value.

Dates are UTC.

---

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

## Environment

| | |
|---|---|
| Subscription | Azure for Students |
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
