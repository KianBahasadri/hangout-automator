# AGENTS.md

Hangout Automator is a FastAPI web app that plans hangouts, invites people from shared profiles via SMS, and tracks RSVPs with optional organizer SMS updates.

## Documentation (read/write)

- **Read first:** For how this repo works, start at `docs/README.md`, then
  open only the topic files you need. Do not re-derive behavior from
  filenames alone when a doc covers it.
- **Before non-trivial changes:** Check the relevant doc so you match
  existing patterns.
- **After behavior changes:** Update the **one** topic file under `docs/`
  that owns that fact. Do not copy the same detail into AGENTS.md,
  README.md, or multiple docs.
- **Regenerate vs patch:** Prefer the `generate_docs` skill when docs are
  broadly stale or many topics shifted; for small, targeted edits, patch
  the single topic file (and `docs/README.md` if you add/remove a topic).
- `CLAUDE.md` is a symlink to this file.

Do not put implementation detail in this file — it lives under `docs/`.
