# Plan: make Hangout Automator survive growth

Status: **Phase 1 complete** (2026-08-08) — CI, ruff, verify_plan.sh, testing docs. Phase 2 in progress.
Owner: unattended agent loop
Baseline commit: `5891d75` (2026-08-08)

This plan removes the four structural limits that stop this app from serving more
than one organizer on more than one process. It is **not** a framework rewrite —
FastAPI, Jinja, SQLAlchemy, and the existing `/api` surface all stay.

The four limits, in dependency order:

1. Nothing gates a push (`docs/testing.md` — no CI).
2. `app/database.py` is a hand-rolled migration engine on single-writer SQLite.
3. There is no tenancy: zero `workspace_id` / `owner_id` / `user_id` columns exist,
   so every authenticated user reads and writes the same rows.
4. `BackgroundScheduler()` (`app/main.py:23`) runs in the web process, so a second
   process double-sends every invite and every digest.

Phases must be done **in order**. Phase 3 (tenancy) needs Alembic from Phase 2 to
express a backfill migration; Phase 4 (worker) needs Postgres from Phase 2 for
advisory locks.

---

## How to work this plan

**Read this section on every iteration before doing anything else.**

1. Find the first unchecked `- [ ]` task in the lowest-numbered incomplete phase.
   That is the only task you are working on.
2. Do it. Run `uv run --group dev pytest`. It must be green before you continue.
3. Tick the box in this file (`- [ ]` → `- [x]`) and commit with the task ID in
   the message, e.g. `git commit -m "P2.4: move schema evolution to Alembic"`.
4. Update the `Status:` line at the top of this file when a phase completes.
5. If a task is blocked, mark it `- [!]`, append a one-line reason under it, and
   move to the next task in the same phase. Never skip forward a phase.
6. Stop when `./scripts/verify_plan.sh` exits 0 and every box below is `[x]`
   or `[!]`. Report any `[!]` items in your final message.

### Hard rules — violating these is worse than not finishing

- **Never run `terraform apply`, `terraform destroy`, or `az` write commands.**
  Terraform files are yours to *edit*; applying them is a human step. See
  "Operator runbook" at the bottom.
- **Never touch the production database.** Anything under
  `/var/lib/hangout-automator/` on the VM is off-limits.
- **Never commit `.env`, `*.db`, `terraform.tfvars`, `backend.hcl`, or state
  files.** Check `git status` before every commit.
- **Never delete `hangout.db` locally without first copying it** to
  `hangout.db.bak-<date>`. (`hangout_test.db` in the repo root is scratch and
  may be deleted freely.)
- **Never force-push, never rebase `main`, never amend a pushed commit.**
- **Do not add a JS framework, do not convert templates to React, do not
  introduce Node.** That was considered and explicitly rejected; the `/api`
  router already provides the seam if it is ever wanted.
- When a task changes behavior, update **exactly one** topic file under `docs/`
  per `CLAUDE.md`. Do not duplicate the same fact into `README.md` or `AGENTS.md`.
- Dependencies are declared **twice** — `pyproject.toml` `[project].dependencies`
  and `requirements.txt`, both exact-pinned. Every dependency change must edit
  both or the VM (which installs `requirements.txt`) drifts from local.
- `pyproject.toml:38` sets `filterwarnings = ["error"]` — **every warning is a
  test failure.** Adding `psycopg` and `alembic` will surface deprecation
  warnings that look like unrelated breakage. Add a targeted
  `"ignore::SomeWarning:module"` entry with a comment saying which dependency
  forced it. Never relax the list to `["default"]`.

File/line references below point at the baseline commit and will drift as you
work. Trust the symbol names over the line numbers.

---

## Phase 1 — A gate that actually gates

Nothing here changes app behavior. It exists so every later phase has a
trustworthy green/red signal.

`docs/testing.md` explains that a CI workflow used to exist and silently never
ran a single test (it installed `requirements.txt`, which has no pytest, then
called `pytest`). Do not repeat that. The self-check in P1.2 is mandatory.

- [x] **P1.1** Add `.github/workflows/ci.yml`: on push and pull_request, matrix
      on Python 3.12 and 3.13 (`pyproject.toml` pins `>=3.12,<3.14`). Install with
      `uv sync --group dev`, then run `uv run --group dev pytest`. Do **not**
      install from `requirements.txt` in the test job.
- [x] **P1.2** Prove the workflow can fail. Add a test file containing a
      deliberately failing assertion, push to a scratch branch, confirm the run
      goes red, then delete the file and confirm green. Record both run URLs in
      the commit message for P1.2. If you cannot reach GitHub, mark `[!]` and
      say so — do not claim the workflow works unverified.
- [x] **P1.3** Add `ruff` to the `dev` dependency group with a `[tool.ruff]`
      section in `pyproject.toml` (line-length 100, target py312). Fix what it
      flags. Add `ruff check` and `ruff format --check` as a CI job.
- [x] **P1.4** Create `scripts/verify_plan.sh` — the loop's terminal condition.
      It must exit non-zero unless **all** of these hold, each as a separate
      labelled check with its own message:
      1. `uv run --group dev pytest` exits 0
      2. `ruff check` and `ruff format --check` exit 0
      3. `alembic upgrade head` then `alembic check` reports no pending
         autogenerate diff (Phase 2)
      4. `grep -rn "BackgroundScheduler" app/main.py` finds nothing (Phase 4)
      5. `grep -rniE "sqlite|PRAGMA" app/` finds nothing (Phase 2). Grep the
         whole dialect, not a list of function names — a name list passes while
         half the module survives.
      6. `tests/test_tenant_isolation.py` and `tests/test_worker_concurrency.py`
         both exist and pass (Phases 3, 4)
      Until the later phases land, checks 3–6 fail. That is correct and expected.
      **Check 3 writes to a database.** The script must refuse to run unless
      `DATABASE_URL` points at localhost or `TEST_DATABASE_URL` is set — exit 2
      with a loud message otherwise. Without that guard this script is a way to
      run migrations against production by accident, which the hard rules forbid.
      It also needs the Phase 2 compose Postgres up; say so in the failure text.
- [x] **P1.5** Update `docs/testing.md`: CI now exists, what it runs, and that
      `scripts/verify_plan.sh` is the aggregate gate. Delete the "Nothing runs
      this for you" section's claim that there is no CI, but **keep** the
      paragraph explaining why the old workflow was removed — it is the reason
      P1.2 exists.

**Exit criteria:** a red test produces a red CI run; `scripts/verify_plan.sh`
exists and fails only on checks 3–6.

---

## Phase 2 — Postgres + Alembic

### Decisions (do not re-litigate)

- **Postgres only. SQLite support is removed, including for local dev and tests.**
  Dual-dialect support is exactly what grew `app/database.py` to 591 lines of
  `PRAGMA`-juggling table rebuilds. Local dev gets Postgres via Docker Compose.
- **Alembic owns all schema evolution.** Every SQLite-era schema helper in
  `app/database.py` is deleted, not ported. They exist to drag a SQLite file
  forward through schema history; the one-time export in P2.7 runs them for the
  last time. The full list is **nine** functions, not the four that `init_db()`
  names directly — `_rebuild_profiles_if_needed` and `_rebuild_hangouts_if_needed`
  are called from inside `_ensure_sqlite_columns` (`app/database.py:330-331`),
  and three more are private plumbing:
  `_ensure_sqlite_columns`, `_rebuild_profiles_if_needed`,
  `_rebuild_hangouts_if_needed`, `_migrate_legacy_food_allergies`,
  `_ensure_schema_flags_table`, `_ensure_default_dietary_restrictions`,
  `_table_cols` (PRAGMA-based), `_schema_flag_get`, `_schema_flag_set`.
  That is `app/database.py:143-591` in the baseline — the file should end up
  around 140 lines.
- **`schema_flags` is dropped.** Its only live use is the
  `dietary_defaults_seeded` marker. In Alembic that is just a data migration
  that runs once by construction.
- **Enums stay `VARCHAR`, no native Postgres `CREATE TYPE`.** On SQLite the three
  real `Enum` columns (`app/models.py:200,244,271`) were stored as VARCHAR;
  autogenerate will silently promote them to native Postgres enum types, after
  which adding one status value costs an `ALTER TYPE` migration and a table
  rewrite on old versions. Pass `native_enum=False` on all three and keep
  `values_callable` so the stored strings stay the lowercase `.value` forms.
  This also makes them consistent with `_optional_enum_column`, which is already
  `String(16)`.
- **Managed Postgres, not a container on the app VM.** The VM is a
  `Standard_B2ats_v2` with 1 GB RAM (`docs/deploy.md`); it cannot host Postgres
  and the app. Target Azure Database for PostgreSQL Flexible Server, `B1ms`,
  private endpoint into the existing subnet.

### Tasks

- [x] **P2.1** Add `alembic`, `psycopg[binary]` to both dependency files. Remove
      nothing yet. Run `alembic init migrations`; point `env.py` at
      `app.database.Base.metadata` and read the URL from
      `app.config.get_settings().database_url` rather than `alembic.ini`.
      Set `compare_type=True` and `compare_server_default=True`.
- [ ] **P2.2** Add `docker-compose.yml` with a single `postgres:17` service,
      a named volume, and port 5432. Add `scripts/db_up.sh` to start it and wait
      for readiness. Change the default `database_url` in `app/config.py` to the
      local compose URL. Update `.env.example`.
- [ ] **P2.3** Generate the baseline migration. Against an **empty** Postgres
      database, `alembic revision --autogenerate -m "baseline schema"`. Review it
      by hand — autogenerate will get the `_optional_enum_column` `TypeDecorator`
      (`app/models.py:37`) wrong if left unchecked; it must land as `VARCHAR(16)`,
      not a native Postgres `ENUM`. The three real `Enum` columns
      (`Hangout.status`, `HangoutInvite.status`, `MessageLog.direction`) should
      keep `values_callable` semantics — assert the stored values are the lowercase
      `.value` strings, not the Python member names.
> **Ordering matters here — P2.4 must come before P2.5.** Three test files call
> `init_db()` (`tests/conftest.py:21`, `tests/test_migrations.py:92,99,110,199,217`,
> `tests/test_api.py:203`). Deleting the helpers first leaves the suite red with
> no way to make it green, which deadlocks the loop protocol's "pytest must be
> green before you continue" rule. Move the tests off SQLite first, then delete.

- [ ] **P2.4** Move the test suite onto Postgres + Alembic, before anything is
      deleted from `app/database.py`.
      - `tests/conftest.py:4` sets `DATABASE_URL` to a temp SQLite path before
        importing the app; replace with a `TEST_DATABASE_URL` env var defaulting
        to the compose instance with a `hangout_test` database name.
      - Replace the session-scoped `init_db()` fixture (`tests/conftest.py:19-22`)
        with one that runs `alembic upgrade head` once.
      - Keep the per-test table-wipe fixture but drop its
        `DELETE FROM schema_flags` line (`tests/conftest.py:46`).
      - `tests/test_migrations.py` tests the SQLite rebuild paths end to end
        (legacy `PRAGMA`/`CREATE TABLE` fixtures, then `database.init_db()`).
        All of it is dead — delete the file wholesale; P2.6 replaces it.
      - `tests/test_api.py:193-203` calls `init_db()` to assert a deleted dietary
        default does not reappear. Keep the assertion, drop the `init_db()` call —
        under Alembic the seed is a one-shot data migration, so the behavior it
        guards is now "re-running migrations does not re-seed".
      - Add the `postgres` service to the CI workflow.
      Suite must be green on Postgres at the end of this task.
- [ ] **P2.5** Now delete the schema machinery. Remove all nine helpers listed in
      the decisions above and reduce `init_db()` to nothing (or delete it and its
      call at `app/main.py:96`, plus the import at `app/main.py:14`). Migrations
      run as a deploy step, not at import. Delete the `_sqlite_pragma` event
      listener and the `check_same_thread` branch at `app/database.py:11-23`.
      Change the `database_url` default in `app/config.py:15` off SQLite if P2.2
      did not already. Keep every `Session` audit-event listener
      (`_audit_transaction_*`, `app/database.py:31-118`) untouched — that is
      unrelated machinery and the audit stream depends on it.
- [ ] **P2.6** Write the new `tests/test_migrations.py`: `alembic upgrade head`
      from empty succeeds; `downgrade base` then `upgrade head` succeeds;
      `alembic check` reports no diff between models and head. That last one is
      what keeps a model edit from shipping without a migration.
- [ ] **P2.7** Write `scripts/migrate_sqlite_to_postgres.py` — a one-shot,
      **read-only-on-the-source** export/import. It must: open the SQLite file
      read-only, run the legacy `init_db()` helpers one final time on a *copy*
      (recover them from git history at `5891d75`), then insert every row into
      Postgres in FK order (`tags`, `allergies`, `profiles`, `profile_tags`,
      `profile_allergies`, `hangouts`, `hangout_invites`, `message_logs`),
      preserving primary keys, then reset every sequence with `setval`. It must
      print a per-table source/destination row count table and exit non-zero on
      any mismatch. Dry-run flag required. Test it against a copy of the
      repo-local `hangout.db`.
      Those eight are the complete set. A real SQLite file also contains
      `schema_flags` and `app_settings`; **both are deliberately not migrated** —
      `schema_flags` is dropped by decision above, and `app_settings` is a legacy
      singleton no longer declared in `app/models.py` that nothing reads
      (`docs/data-model.md:70-72`). Have the script assert it has accounted for
      every source table, with those two named in an explicit skip list, so a
      table added later cannot be silently dropped on the floor.
- [ ] **P2.8** Terraform: add `azurerm_postgresql_flexible_server` (B1ms,
      private DNS zone + VNet integration into the existing `10.20.1.0/24`
      subnet, `public_network_access_enabled = false`, `prevent_destroy = true`,
      35-day backup retention). Add the connection string to
      `cloud-init.yaml.tftpl`'s `/etc/hangout-automator.env` as `DATABASE_URL`.
      Password comes from a Terraform variable sourced from `.env`, never a
      default. Add `alembic upgrade head` to the cloud-init bootstrap sequence
      before the service starts. **Write the config; do not apply it.**
- [ ] **P2.9** Update `docs/data-model.md` — delete the entire "SQLite ensure /
      migrate" section and the connection-PRAGMA line, replace with how Alembic
      is used and where migrations live. Update `docs/local-development.md`
      (compose, Postgres, new env vars — the `DATABASE_URL` row of its settings
      table and the "Startup side effects" section both name `init_db()`).
      Add the Postgres server to `docs/deploy.md`'s Terraform resource list and
      the go-live checklist. Fix `docs/README.md`'s index line for
      `data-model.md`, which advertises "SQLite ensure/migrate helpers".

**Exit criteria:** `alembic check` clean; full suite green against Postgres; no
`PRAGMA`, no `sqlite` string anywhere in `app/`; `verify_plan.sh` checks 3 and 5
pass.

---

## Phase 3 — Tenancy

This is the highest-value phase. Everything else is recoverable later; this one
gets exponentially more expensive with every table and every route added.

### Decisions (do not re-litigate)

- **Workspace, not Clerk Organizations.** A `workspaces` table plus a
  `workspace_members` table mapping `clerk_user_id` → workspace + role. Clerk
  stays purely an identity provider. `app/auth.py:110` already puts
  `clerk_user_id` on `request.state`; membership lookup is app-owned. This avoids
  coupling workspace lifecycle to a Clerk plan feature, and keeps
  `CLERK_ENABLED=false` working locally.
- **`workspace_id` is denormalized onto `hangout_invites` and `message_logs`**
  even though it is derivable through `hangout_id`. Reason: `message_logs` rows
  for an *unmatched* inbound SMS have `invite_id` and `hangout_id` both NULL
  (`app/services.py:_handle_inbound_sms` logs before it resolves), so there is no
  join path to a workspace for exactly the rows most worth auditing. Make it
  nullable on `message_logs`, NOT NULL on `hangout_invites`.
- **Inbound SMS routing: most-recent-active-invite wins, globally.** An inbound
  SMS carries only a phone number and the shared Twilio number, so it cannot
  name a workspace. The existing query
  (`app/services.py:477-504`) already orders by `last_outbound_at desc` filtered
  to `status == active`; that generalizes correctly. The residual ambiguity — one
  person invited to active hangouts in two workspaces in the same window —
  resolves to whichever texted them last, which is nearly always what the sender
  meant. **Add an audit event when the chosen invite's workspace is not the only
  candidate workspace**, so the ambiguity is observable. Per-workspace Twilio
  numbers are the real fix; record that in `docs/tenancy.md` as the upgrade path,
  do not build it.
- **Unique constraints become composite**: `profiles.phone` →
  `UNIQUE (workspace_id, phone)`; `tags.name` → `UNIQUE (workspace_id, name)`;
  `allergies.name` → `UNIQUE (workspace_id, name)`. The current global uniqueness
  (`app/models.py:115,129,142`) would otherwise stop two workspaces from having
  the same person or the same tag.

### Tasks

- [ ] **P3.1** Add `Workspace` and `WorkspaceMember` models. `Workspace`: `id`,
      `name`, `slug` (unique), `created_at`. `WorkspaceMember`: `id`,
      `workspace_id` FK CASCADE, `clerk_user_id` (String(255), indexed),
      `role` (enum `owner`/`member`), `created_at`, `UNIQUE (workspace_id,
      clerk_user_id)`.
- [ ] **P3.2** Add nullable `workspace_id` to `profiles`, `hangouts`, `tags`,
      `allergies`, `hangout_invites`, `message_logs` in one Alembic migration.
      Nullable first — the backfill needs it.
- [ ] **P3.3** Backfill migration: create workspace `default` (slug `default`),
      set `workspace_id` on every existing row in all six tables, then `ALTER`
      to NOT NULL on all except `message_logs`. Swap the three unique constraints
      to composite in the same migration. Include a working `downgrade()`.
- [ ] **P3.4** Add `app/tenancy.py`:
      - `current_workspace(request) -> Workspace` FastAPI dependency. Resolves
        `request.state.clerk_user_id` → `WorkspaceMember` → `Workspace`. A user
        with **no** membership is not an error — provision one (see P3.10);
        `current_workspace` always returns a workspace for an authenticated user.
        The 404 rule below is about *resources*, never about membership.
      - When `settings.clerk_enabled` is false, resolve to the `default`
        workspace so local dev and the existing test suite keep working.
      - `scoped(db, model, workspace)` helper returning a filtered query, and
        `get_scoped(db, model, id, workspace)` returning `None` (→ 404) rather
        than a foreign row.
- [ ] **P3.5** Convert every read in `app/routers/web.py` (17 call sites) and
      `app/routers/api.py` (17 call sites). Every bare `db.get(Model, id)` becomes
      `get_scoped(...)`; every `db.query(Model)` gains a workspace filter. Every
      write sets `workspace_id` from the resolved workspace. Do this file by file,
      running the suite after each.
- [ ] **P3.6** Convert `app/services.py` (12 call sites). `setup_hangout`,
      `load_hangout`, and the organizer-resolution helpers all take an explicit
      workspace argument. `process_followups` and `process_organizer_intervals`
      stay workspace-agnostic — they are the worker's cross-workspace sweep — but
      must carry `workspace_id` onto anything they create.
- [ ] **P3.7** Update `_handle_inbound_sms`: set `workspace_id` on the inbound
      `message_logs` row once an invite is matched, leave NULL when unmatched, and
      emit the multi-workspace-candidate audit event described above.
- [ ] **P3.8** **Write `tests/test_tenant_isolation.py` — the most important test
      in this plan.** Follow the generated-matrix style already in
      `tests/support/routes.py`. Build two workspaces, each with a full
      `sample_data` set. Then, for **every route in the live router inventory**,
      authenticated as workspace A, substitute workspace B's ids into the path
      parameters and body, and assert the response is 404 (or 403) and never 200,
      and that no workspace-B string (`"Sam Rivera"`, B's phone, B's motive)
      appears in any response body. Add a guard test asserting the route inventory
      is non-empty, matching the existing `test_route_inventory_reflects_the_real_app`
      pattern — an empty matrix must fail, not silently pass.
- [ ] **P3.9** Update `tests/conftest.py`'s `sample_data` to create and return a
      workspace, and add a second `other_workspace` fixture for P3.8.
- [ ] **P3.10** Workspace bootstrap — the other half of P3.4's resolution rule.
      On the first authenticated request from a `clerk_user_id` with no
      membership, create a workspace and an `owner` membership, inside
      `current_workspace` itself. Put this in `app/tenancy.py`, not the
      middleware. Two concurrent first requests from the same user must not
      create two workspaces: rely on the `UNIQUE (workspace_id, clerk_user_id)`
      constraint from P3.1 and re-read on conflict. Add a test that a brand-new
      user gets an empty workspace and cannot see the `default` one's data.
- [ ] **P3.11** Write `docs/tenancy.md`: the workspace model, how a request
      resolves to a workspace, the `CLERK_ENABLED=false` default-workspace
      behavior, the inbound-SMS routing rule and its known ambiguity, and the
      per-workspace-Twilio-number upgrade path. Add it to `docs/README.md`'s
      index. Correct `docs/overview.md:3-7`, which currently states there is no
      multi-tenancy.

**Exit criteria:** `tests/test_tenant_isolation.py` passes with a non-empty
matrix; no route in `app/routers/` reaches a tenant-scoped model without a
workspace filter.

---

## Phase 4 — Worker process + send locking

### Decisions (do not re-litigate)

- **Separate process, not a thread.** `app/worker.py` with its own entry point,
  its own systemd unit. The web process starts no scheduler.
- **Two layers of locking, both required.** A Postgres advisory lock
  (`pg_try_advisory_lock`) around each job tick so N workers do not run the same
  sweep; and `SELECT ... FOR UPDATE SKIP LOCKED` on the invite rows inside the
  sweep so a lock lost mid-tick still cannot double-send. The advisory lock alone
  is not enough — it is not held across a crash-and-restart within the same tick.
- **Keep APScheduler** for the interval trigger inside the worker. It is fine at
  that job; the bug was only ever that it lived in the web process.

### Tasks

- [ ] **P4.1** Create `app/worker.py`: builds a `BlockingScheduler`, registers the
      two jobs currently at `app/main.py:101-110` (followups every 5 min,
      organizer every 10 min), reuses `_job_followups` / `_job_organizer` bodies
      including their `request_context()` / `audit_event` instrumentation
      verbatim — that logging is load-bearing. Add a `worker` console script to
      `pyproject.toml` `[project.scripts]` alongside the existing `dev`.
- [ ] **P4.2** Delete `scheduler`, `_job_followups`, `_job_organizer`, and both
      `add_job`/`start`/`shutdown` calls from `app/main.py`. The lifespan keeps
      only the audit events. Emit `server.started` with a field recording that
      scheduling is external, so a misconfigured deploy with no worker is
      visible in the audit stream.
- [ ] **P4.3** Add `app/locks.py`: `advisory_lock(session, key) -> bool` context
      manager over `pg_try_advisory_lock` / `pg_advisory_unlock`, with a stable
      integer key per job name. Wrap each job body; skip the tick and emit
      `background_job.skipped_locked` when the lock is not acquired.
- [ ] **P4.4** Rewrite the invite selection in `process_followups`
      (`app/services.py:610`) to claim rows with
      `.with_for_update(skip_locked=True)` and commit the `last_outbound_at`
      advance **before** dispatching to Twilio. Today `last_outbound_at` is set at
      `app/services.py:670` in the same transaction as the send; a crash between
      send and commit re-sends on the next tick.
      Same treatment for `process_organizer_intervals` (`app/services.py:694`),
      but note the lock target is **different**: that sweep claims `hangouts`
      rows, not `hangout_invites`, and the clock it advances is
      `Hangout.last_organizer_notify_at` (`app/models.py:225`, written at
      `app/services.py:729` and `:735`). Lock the hangout row, commit the
      timestamp, then send.
- [ ] **P4.5** Write `tests/test_worker_concurrency.py`: seed an active hangout
      with N due invites, run two sweeps concurrently against the same Postgres
      database (threads with separate sessions), and assert exactly N sends were
      recorded in `message_logs` — not 2N. Add a second case that asserts a
      simulated failure after send but before commit does not produce a duplicate
      on the next tick.
- [ ] **P4.6** Terraform / cloud-init: add a `hangout-worker.service` systemd unit
      running the `worker` entry point, sharing `/etc/hangout-automator.env`,
      `After=hangout-automator.service`, `Restart=always`. Add it to the
      `systemctl is-active` smoke check in `docs/deploy.md`'s verification
      snippet. **Write it; do not apply it.**
- [ ] **P4.7** Write `docs/background-jobs.md`: the two jobs, their intervals, the
      two-layer locking model and why both layers exist, the worker unit, and how
      to verify a worker is running. Add to `docs/README.md`. Remove the
      background-jobs bullet from `docs/overview.md:15` and link instead.

**Exit criteria:** `grep BackgroundScheduler app/main.py` finds nothing;
`tests/test_worker_concurrency.py` passes.

---

## Phase 5 — Ops hardening

Small, independent, genuinely needed before real traffic. `docs/deploy.md:456`
already flags the first one.

- [ ] **P5.1** Rate-limit `POST /webhooks/sms`. It is publicly reachable by
      design (Cloudflare Access bypasses it for Twilio) and every hit can cost
      money. Per-source-phone and global ceilings, 429 past them, backed by a
      Postgres counter table — no new infrastructure dependency. Signature
      verification stays first in the chain, so unsigned floods die cheaper.
- [ ] **P5.2** Off-site backups. `docs/deploy.md:474` states the current gzipped
      snapshots sit on the same managed disk as the database. With Flexible Server
      from P2.8, Azure's own 35-day retention replaces `hangout-backup.sh`; delete
      the script, its `.service`, and its `.timer` from `cloud-init.yaml.tftpl`,
      and rewrite the "Backups" section of `docs/deploy.md` around
      point-in-time restore.
- [ ] **P5.3** Add `GET /api/health` deep-check variant reporting database
      reachability, pending Alembic revision vs head, and worker last-tick age
      (from the audit stream or a heartbeat row). Keep the existing shallow
      `/api/health` public and unauthenticated for the Cloudflare probe; the deep
      one requires auth.

---

## Phase 6 — Final documentation sweep

- [ ] **P6.1** Re-read `docs/README.md` and confirm every topic file listed still
      exists, every new file (`tenancy.md`, `background-jobs.md`) is indexed, and
      no fact is duplicated across two files — the repo rule in `CLAUDE.md`.
- [ ] **P6.2** Update `docs/overview.md`'s package layout block for the new
      modules (`tenancy.py`, `locks.py`, `worker.py`, `migrations/`).
- [ ] **P6.3** Update `README.md` quickstart: Docker Compose, `alembic upgrade
      head`, running web and worker.
- [ ] **P6.4** Run `./scripts/verify_plan.sh`. All six checks must pass.

---

## Operator runbook (human — not the loop)

The loop writes all of this but applies none of it. Run in order, after the loop
reports done:

1. Review the full diff, especially the Alembic migrations in `migrations/versions/`.
2. `./scripts/terraform.sh plan` — review the Postgres server, private DNS zone,
   and worker unit. Confirm the data disk and its `prevent_destroy` are untouched.
3. `./scripts/terraform.sh apply`.
4. Stop `hangout-automator` on the VM. Take a final SQLite backup per
   `docs/deploy.md`'s existing procedure and pull it off the VM.
5. Run `scripts/migrate_sqlite_to_postgres.py --dry-run` against that backup,
   review the row-count table, then run it for real.
6. `alembic upgrade head` against the new server; confirm `alembic check` is clean.
7. Start `hangout-automator` and `hangout-worker`. Verify both with the
   `systemctl is-active` snippet in `docs/deploy.md`.
8. Send one test SMS end to end. Confirm exactly one message arrives — that is the
   Phase 4 regression that matters most in production.
9. Only after that: delete the Azure managed data disk holding the old SQLite file.

## Definition of done

`./scripts/verify_plan.sh` exits 0, every checkbox above is `[x]` or `[!]`, and
`git status` is clean. Report every `[!]` with its reason.
