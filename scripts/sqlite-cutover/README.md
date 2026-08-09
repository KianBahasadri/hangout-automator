# SQLite → Postgres cutover (one-time)

Everything in this directory exists to move the pre-Postgres SQLite database
onto Postgres exactly once. **It is not part of normal development or deploy.**
Once the production cutover in
[../../docs/deploy.md](../../docs/deploy.md) has been done and verified, delete
this directory.

- `migrate.py` — the one-shot migration. Always run `--dry-run` first and read
  the row-count table before running it for real.
- `_legacy_sqlite_database.py` — the pre-Postgres `app/database.py`. Everything
  below its header comment is byte-identical to `app/database.py` at commit
  `5891d75`; only the header is ours. `migrate.py` executes it to bring a *copy*
  of an old SQLite file up to its final legacy schema before reading it. It is
  excluded from ruff in `pyproject.toml`: reformatting it would break the
  byte-identical provenance that makes it trustworthy. Do not edit the code.

```bash
uv run scripts/sqlite-cutover/migrate.py --dry-run <backup.db>
uv run scripts/sqlite-cutover/migrate.py [--database-url URL] <backup.db>
```

Point it at a *copy* of the SQLite file, never the original.
