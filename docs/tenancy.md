# Tenancy

Every row of tenant data — `profiles`, `hangouts`, `tags`, `allergies`,
`hangout_invites`, and `message_logs` — carries a `workspace_id` foreign key
into `workspaces`. Two organizers on the same instance see and edit disjoint
datasets. The key structures are `app/tenancy.py`, the `Workspace` /
`WorkspaceMember` models, and the Alembic migrations that backfilled the
`default` workspace.

## The workspace model

- **`workspaces`** — `id`, `name`, unique `slug`, `created_at`.
- **`workspace_members`** — maps `clerk_user_id` → workspace + `role`
  (`owner` / `member`), with `UNIQUE (workspace_id, clerk_user_id)`, plus the
  `email` the membership was provisioned for. Clerk is purely an identity
  provider; membership is app-owned, so no Clerk plan feature gates
  multi-tenancy and `CLERK_ENABLED=false` works locally.

## How a request resolves to a workspace

`current_workspace(request)` (`app/tenancy.py`) is a FastAPI dependency on
every route that touches tenant data:

1. **`CLERK_ENABLED=false`** — everything resolves to the seeded `default`
   workspace. Local dev and the test suite behave exactly as before.
2. **Clerk enabled** — `request.state.clerk_user_id` (set by the auth
   middleware) is looked up in `workspace_members`. A member resolves to their
   workspace.
3. **No membership** — not an error: the first authenticated request from a
   user provisions a workspace plus an `owner` membership (deterministic slug
   `user-<clerk_user_id>`, whose unique constraint makes concurrent first
   requests collapse to one workspace via re-read on conflict), recording the
   email the middleware resolved. Reaching this step already means the access
   list admitted them, so provisioning is not a decision this function makes.

A request that carries no usable `sub` is refused as an authentication failure
by the middleware, and again with a 401 by `current_workspace` if it somehow
gets that far. The anomaly is logged. It should be unreachable — the
middleware only populates `request.state` after `is_authenticated` — but the
alternative to refusing is resolving to the shared `default` workspace, which
would hand an unattributable request another tenant's data. Failing closed
here is what the *no identity, so no workspace* rule means;
`test_authenticated_request_without_a_subject_is_refused` pins it.

## The access list: who this deployment admits

Clerk says *who* someone is; it never says whether this deployment wants them.
Step 3 above would otherwise provision a workspace for any stranger who can
create a Clerk account — the gap Cloudflare Access used to cover until it was
removed on 2026-08-09. `access_grants` closes it in the app.

- **One row per allowed email**, with a role of `admin` or `member`. Anyone
  else is refused after authenticating, whether or not they have a Clerk
  account.
- **`member`** may use the app. **`admin`** may additionally edit the list, at
  Settings → Access. Nothing else distinguishes them.
- Matching is on the **verified primary** email Clerk holds for the user. An
  unverified address never matches, or sign-up would let anyone claim an
  allowed address and inherit its grant.
- The list is **instance-wide, not workspace-scoped** — it is the gate in front
  of workspace provisioning, so it has to be answerable before a request has a
  workspace.

### Why it is not in Clerk

Clerk's own allowlist and blocklist are paid-plan features on this instance
(the API answers `unsupported_subscription_plan_features`), its restrictions
endpoint is write-only (`GET /v1/instance/restrictions` → 405, so a setting
could not be audited), and Clerk has no notion of an *app user* permitted to
edit sign-up restrictions — only Dashboard seats, which carry control of the
whole instance. Keeping the list here makes it enforceable, testable, and
editable by exactly the three people who should edit it.

### Where it is enforced

In `ClerkAuthMiddleware` (`app/auth.py`), not in `current_workspace`. Some
protected routes — `/settings/logs`, `/settings/sms-simulator` — never resolve
a workspace, so a check living in the dependency would hand a signed-in
stranger the audit log. The middleware resolves the Clerk user id to an email
(Backend API, cached in-process for 5 minutes) and looks the grant up in the
database on **every** request, so removing a grant takes effect on the next
one. A refusal is a **403** with a standalone page, not a redirect to
`/sign-in`, which would loop.

An unreachable Clerk Backend API is a **503**, not a 403: "we cannot ask who
you are" and "you are not on the list" are different answers, and collapsing
them would tell every legitimate user they had been removed whenever Clerk
hiccuped.

`tests/test_access_control.py` pins all of this; `tests/support/access.py` is
the seam other tests use to sign somebody in without calling Clerk.

### Bootstrapping and lockout

`ACCESS_BOOTSTRAP_ADMINS` (comma-separated emails) is applied at startup and
guarantees each address an `admin` grant. It is **additive only** — it never
demotes or deletes — which makes it the way back in if the last admin is ever
lost: put the address there and restart. The flip side is that an address left
in the variable reappears after a restart even if an admin removed it in the
UI; remove it from both.

Two guards protect against locking everyone out: the UI refuses to remove or
demote the last remaining admin, and `scripts/deploy/terraform.sh` refuses an
apply with `CLERK_ENABLED=true` and an empty `ACCESS_BOOTSTRAP_ADMINS`. With no
admins at all the app still starts, logging an error, because refusing to boot
would turn a bootstrap problem into an outage.

**Nothing in the bootstrap may stop the server.** It is the first thing in
`lifespan` to touch the database, so an unreachable Postgres — or a deploy that
restarts the app before `alembic upgrade head` has created `access_grants` —
would otherwise crash startup, take `/api/health` down with it, and under
systemd restart forever. Both cases now log an error naming the two likely
causes and continue; sign-in stays broken until the database is fixed, but the
process stays up and recovers on the next restart. This is also why the error
is *not* the "no admins exist" one: "could not count" is not "counted zero",
the same distinction the middleware draws between a Clerk outage and a missing
grant. `test_startup_survives_a_database_it_cannot_read` pins it.

### What the list does and does not protect

- It stops a stranger from using the organizer features, which send SMS
  through the deployment's shared Twilio credentials at the operator's
  expense. That was the concrete cost of leaving sign-up open.
- It is not tenant isolation. Allowed users still land in **separate**
  workspaces and cannot read each other's data — see below.
- Sign-*up* at Clerk remains open; the app simply refuses the account. A
  stranger can still create a Clerk user against this instance, so Clerk's user
  count is not a count of people with access.

### Scoping

Reads go through `scoped(db, model, workspace)` / `get_scoped(db, model, id,
workspace)`; `get_scoped` returns `None` for a row that exists in a *different*
workspace, which callers turn into a 404. Writes set `workspace_id` from the
resolved workspace. The isolation matrix in `tests/test_tenant_isolation.py`
drives every route with another workspace's ids and asserts 404 (or 303 for
web redirects) with no leaked content.

## Inbound SMS routing and its known ambiguity

An inbound SMS carries only the sender's phone and the shared Twilio number —
it cannot name a workspace. `_handle_inbound_sms` therefore resolves the
invite globally: most-recent-active-invite wins (ordered by
`last_outbound_at`, then invite id), with a last-10-digits fallback. The
inbound `message_logs` row is created with `workspace_id` NULL and attributed
to the matched invite's workspace; unmatched rows stay NULL (the column is
nullable for exactly this reason).

When the sender is invited to active hangouts in **more than one workspace**,
the chosen invite's workspace may not be the only candidate. The resolver
picks the most recent outbound (nearly always what the sender meant) and emits
an `sms.inbound.ambiguous_workspace` audit event with the chosen and candidate
workspace ids, so the ambiguity is observable, not silent.

**Upgrade path:** per-workspace Twilio numbers. A workspace with its own
number can be resolved before the invite lookup, eliminating the ambiguity.
Not built — recorded here as the direction to take when a workspace needs it.
