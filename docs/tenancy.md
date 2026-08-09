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
  (`owner` / `member`), with `UNIQUE (workspace_id, clerk_user_id)`. Clerk is
  purely an identity provider; membership is app-owned, so no Clerk plan
  feature gates multi-tenancy and `CLERK_ENABLED=false` works locally.

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
   requests collapse to one workspace via re-read on conflict).

A request the middleware admitted but that carries no usable `sub` is refused
with a 401, and the anomaly is logged. It should be unreachable — the
middleware only populates `request.state` after `is_authenticated` — but the
alternative to refusing is resolving to the shared `default` workspace, which
would hand an unattributable request another tenant's data. Failing closed
here is what the *no identity, so no workspace* rule means;
`test_authenticated_request_without_a_subject_is_refused` pins it.

### Known gap: any signer gets a workspace

Nothing in the app refuses an *unknown* Clerk user — step 3 simply provisions
a workspace for them. Whoever can sign up can get an account. The deployment
is kept single-user by the Cloudflare Access email allowlist in front of it,
not by anything here, which is why Access cannot be removed until sign-up is
restricted; the
[deploy doc](./deploy.md#planned-retiring-cloudflare-access) records that plan
and its exit criteria.

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
