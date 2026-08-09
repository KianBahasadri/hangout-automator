# Logging and traceability

## Decision

The server uses verbose, application-side audit logging as a deliberate
traceability feature. Phone numbers and SMS message bodies are retained in the
audit stream. Every HTTP request that reaches FastAPI, every response, SMS
operation, inbound webhook decision, background-job run, RSVP transition, and
database transaction is recorded with timestamps and correlation IDs.

This is an operational audit trail, not a tamper-proof compliance archive.
The VM's application user can still alter or delete local files. If evidence-
grade retention is required later, ship the JSONL file to an append-only remote
log/SIEM service.

## Log files

The application writes one JSON object per line (JSONL) to `LOG_FILE`:

- Local default: `logs/server.log` relative to the working directory.
- Terraform deployment: `/var/lib/hangout-automator/logs/server.log` on the
  persistent data disk.
- Rotation: 50,000,000 bytes per file and 10 rotated files by default, for a
  maximum of roughly 550 MB including the active file and 10 backups before the
  oldest file is removed.
- Files are created owner-readable/writable (`0600`); the deployed service
  also uses a restrictive `UMask`.
- The same events continue to the terminal locally and the systemd journal in
  deployment. The file is the detailed searchable audit stream.

The settings are `LOG_FILE`, `LOG_LEVEL`, `LOG_MAX_BYTES`,
`LOG_BACKUP_COUNT`, and `LOG_BODY_MAX_BYTES`. Bodies are hashed in full and
text bodies are included up to `LOG_BODY_MAX_BYTES` (256 KiB by default), with
a truncation marker when the limit is exceeded.

## Event contents

Each JSONL record includes an `event_id`, `request_id`, UTC `timestamp`, level,
logger, process/thread, event name, message, and event-specific `data`.

HTTP events include method, path, query string, route, status, duration,
request/response headers, readable request/response bodies, body byte counts
and SHA-256 hashes, user agent, referer, and host. Source fields include the
direct peer, `X-Forwarded-For`, `X-Real-IP`, and Cloudflare's
`CF-Connecting-IP` and country/Ray values. A response
also carries `X-Request-ID` so an operator can match a browser/API response to
the file.

No identity is read from a request header. The log used to record an
`access_identity` from `CF-Access-Authenticated-User-Email`; that field was
dropped when Cloudflare Access was removed, because with no edge authenticator
stamping it the header is just client-supplied text and recording it would
attribute requests to a forged address.

Known credential-bearing headers (`Authorization`, cookies, API keys, the
Twilio signature, the legacy Cloudflare Access JWT/client credentials, and
`Set-Cookie`) are not written; their names are recorded as redacted. This keeps access
tracing useful without turning the audit file into a credential dump. Request
and response bodies are intentionally not redacted.

Domain events additionally cover:

- server startup/shutdown, scheduler start, each background-job run and error;
- access-list bootstrap at startup (`access.bootstrap_admins_granted` with the
  seeded emails, `access.bootstrap_complete` with the admin count, or
  `access.bootstrap_unavailable` when the database could not be read at all —
  the app still starts) and a warning for every signed-in user refused for
  holding no grant;
- ORM-created/updated/deleted records and commit/rollback outcomes;
- outbound SMS provider, destination, full body, result, and error;
- inbound webhook payload, signature presence/validity, rejection reason,
  processing result, parsed intent, and auto-reply;
- RSVP status changes, unmatched/unrecognized replies, hangout setup, and
  follow-up/organizer scans.

## Download from Settings

`GET /settings/logs` (linked from the Settings page) returns the active
`LOG_FILE` as a downloadable attachment (`Content-Disposition: attachment`).
Handlers are flushed before the file is served so recent events are included.
Rotated backups (`server.log.1`, …) are not included. A missing file yields 404.

## Finding events

Local development:

```bash
tail -f logs/server.log
jq -c 'select(.event == "http.request.completed")' logs/server.log
```

Deployment:

```bash
sudo tail -f /var/lib/hangout-automator/logs/server.log
sudo journalctl -u hangout-automator.service -f
```

Use `request_id` to follow one browser/API request through its database and
SMS side effects. Use `event_id` to identify one exact event. The app only
records requests that reach FastAPI; Cloudflare edge events and tunnel
transport events remain in Cloudflare or `cloudflared` logs, respectively.
