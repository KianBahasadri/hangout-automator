# Background jobs

Background jobs run in a **separate process** (`hangout-worker`), never in the
web process. The web process starts no scheduler, so running N web processes
cannot double-send; the worker is the only place the two sweeps run.

## The two jobs

Both are APScheduler interval triggers inside `app/worker.py`:

| Job | Interval | What it does |
|-----|----------|--------------|
| `followups` | 5 minutes | `process_followups`: sends due follow-up SMS to pending/remind invitees, marks final-window non-responders `no_response` |
| `organizer` | 10 minutes | `process_organizer_intervals`: sends organizer digest SMS to active hangouts whose interval elapsed, skipping unchanged digests |

Each tick wraps its work in `request_context()` and emits
`background_job.started` / `completed` / `failed` audit events with a `job_id`
correlation field — that logging is load-bearing for operations (see
[logging.md](./logging.md)).

## Why two layers of locking

With more than one worker process (or a crash-restart race), a sweep must not
send the same SMS twice. Two layers exist because either one alone is
insufficient:

1. **Postgres advisory lock** (`app/locks.py`) — `pg_try_advisory_lock` around
   each tick, with a stable integer key per job (`followups` = 9001,
   `organizer` = 9002). The loser skips the tick and emits
   `background_job.skipped_locked`. This stops two workers from running the
   same sweep at the same moment — but it is **not** held across a
   crash-and-restart within the same tick, so alone it can still double-send.

   The lock is acquired and released on a **dedicated connection**
   (`engine.connect()` inside `advisory_lock`) held open for the whole tick —
   never on the sweep session's own connection. The sweeps commit mid-tick;
   a lock taken on the sweep session's connection would be stranded when the
   first commit returns that connection to the pool: another session checks
   it out, the unlock runs on a different connection, silently fails, and
   the lock survives for the life of the pooled connection. That is liveness,
   not safety — the row claiming below still prevents double-sends — but a
   stranded lock starves every later tick. `pg_try_advisory_xact_lock` was
   rejected because it releases at the first COMMIT inside the sweep, which
   is exactly what must not happen.
2. **`FOR UPDATE SKIP LOCKED` row claiming** (`app/services.py`) — inside the
   sweep, each candidate row is claimed in its own short transaction: a worker
   that loses the claim walks away. The row's clock (`last_outbound_at` for
   invites, `last_organizer_notify_at` for hangouts) is **advanced and
   committed before dispatching to Twilio**, so a crash between send and
   commit cannot re-send on the next tick. The due-ness re-read uses
   `populate_existing()` — the lock can be acquired mid-scan, after the other
   worker's commit, and the decision must never use a stale clock.

The lock targets differ per sweep: `process_followups` claims
`hangout_invites` rows; `process_organizer_intervals` claims `hangouts` rows
(the clock it advances lives on the hangout). Both stay workspace-agnostic —
they are the worker's cross-workspace sweep — but every row they create or
touch carries `workspace_id` (see [tenancy.md](./tenancy.md)).

## The worker unit

`hangout-worker.service` (cloud-init) runs
`/opt/hangout-automator/.venv/bin/python -m app.worker`, shares
`/etc/hangout-automator.env`, starts after `hangout-automator.service`, and
`Restart=always`. Locally: `uv run worker`.

## Verifying a worker is running

- On the VM: `systemctl is-active hangout-worker` (the deploy.md smoke snippet
  checks it alongside the app).
- In the audit stream: `worker.started` on boot, then `background_job.started`
  / `completed` every 5–10 minutes with increasing `job_id`s. A deploy without
  a worker is visible because `server.started` records
  `scheduling=external_worker` — and the background_job events simply never
  appear.
