# Invites and follow-ups

Core logic: `setup_hangout` and `process_followups` in `app/services.py`. The
sweep runs on the worker's 5-minute interval — see
[background-jobs.md](./background-jobs.md) for scheduling and send locking.

## Setup / activate

Triggered by web “Set up hangout”, web create with `action=setup`, or `POST /api/hangouts/{id}/setup`.

Rules:

- Reject if hangout is `closed`
- Require at least one existing contact id (body/form or existing invites); reject selections that contain no valid contacts
- Create missing `hangout_invites` rows
- Drop existing invite rows left **out** of an explicit selection that have never been messaged (`last_outbound_at IS NULL`) — they were never really invited, and left in place they would sit in `pending` forever because the follow-up job has no clock to measure them against. Rows that already received a text are never removed, so re-running setup for one person cannot discard anyone else's answer
- Do not re-send to invites already `confirmed` / `remind` / `declined` when hangout is already active
- Do not reset the follow-up clock for `pending` invites that already have `last_outbound_at`
- Send invite SMS; success → `pending`, failure → `failed_send`; reset `followups_sent` to 0 and set `last_outbound_at` on send attempt path used for successful invites
- Set hangout `status=active` and `activated_at` if unset

## Follow-up job

Selects invites on **active** hangouts with status `pending` or `remind`.

Due when `now >= last_outbound_at + delays[followups_sent]` hours, where `delays` comes from `FOLLOWUP_HOURS` (see [local-development.md](./local-development.md)).

Caps:

- At most `budget` = `min(max_followups, len(delays))` follow-up sends (2 by default)
- Once the budget is spent, the invite becomes `no_response` only after the **final delay has elapsed since the last send**, so nobody is marked no-response in the same pass that texted them

## Send failures

- A failed follow-up does **not** increment `followups_sent` — an outage must not burn the budget or mark invitees `no_response`
- It **does** advance `last_outbound_at`, so a number that permanently rejects SMS (an opt-out, say) is retried once per delay window instead of on every 5-minute tick
- After `FOLLOWUP_FAILURE_LIMIT` (3) **consecutive** failed sends, the status becomes `failed_send`: follow-ups stop and the hangout page shows the invite needs attention. `setup_hangout` can re-send to it.
- The streak is counted since the invite's last *successful* outbound send, not over its lifetime, so a transient outage months ago cannot combine with one fresh failure to retire a number that works.

## Related

RSVP keyword handling and inbound SMS: [sms-and-rsvp.md](./sms-and-rsvp.md). Organizer alerts after replies: [organizer-notifications.md](./organizer-notifications.md).
