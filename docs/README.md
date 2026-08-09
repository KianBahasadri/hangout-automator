# Docs

- [functional-specification.md](./functional-specification.md) — Product MVP requirements (hand-written; what the system should do)
- [overview.md](./overview.md) — Runtime architecture, shared-data assumptions, and package layout
- [local-development.md](./local-development.md) — Stack, env vars, and how to run locally
- [logging.md](./logging.md) — Verbose JSONL event logging, trace IDs, retention, and operations
- [data-model.md](./data-model.md) — SQLAlchemy models, enums, Alembic migrations
- [web-ui.md](./web-ui.md) — HTML routes, templates, and UI patterns
- [api.md](./api.md) — JSON REST API under `/api`
- [sms-and-rsvp.md](./sms-and-rsvp.md) — SMS providers, message copy, inbound webhook, reply parsing
- [invites-and-followups.md](./invites-and-followups.md) — Hangout activation, invite sends, follow-up scheduler
- [organizer-notifications.md](./organizer-notifications.md) — Interval digests and threshold alerts
- [testing.md](./testing.md) — Test suite layout, the generated smoke matrix, running it
- [tenancy.md](./tenancy.md) — Workspaces, how requests resolve, inbound-SMS routing ambiguity
- [background-jobs.md](./background-jobs.md) — Worker process, sweep intervals, two-layer send locking
- [deploy.md](./deploy.md) — Azure Terraform + rsync deploy scripts
- [deployment-history.md](./deployment-history.md) — Log of what was actually deployed, when, and what broke

`archive/` holds completed plans and other point-in-time records. It is kept
for history and is **not maintained** — never treat it as a description of
current behavior.

## Notes

These docs (except `functional-specification.md`) are AI-generated after the fact from the current repo. They describe how the implementation works, not a design target.

Each fact should live in exactly one topic file. If something belongs to two topics, put it in the better fit and link from the other.

`functional-specification.md` is the product requirements document and is maintained separately from the implementation topic set. Prefer updating it when product intent changes; prefer the other topic files when code behavior changes.

Experiments and dead-ends that remain in the repo should get their own doc file rather than being folded into live-feature docs.
