# Invites and follow-ups

Core logic: `setup_hangout` and `process_followups` in `app/services.py`. Scheduler: every **5 minutes** from `app/main.py`.

## Setup / activate

Triggered by web “Set up hangout”, web create with `action=setup`, or `POST /api/hangouts/{id}/setup`.

Rules:

- Reject if hangout is `closed`
- Require at least one profile id (body/form or existing invites)
- Create missing `hangout_invites` rows
- Do not re-send to invites already `confirmed` / `remind` / `declined` when hangout is already active
- Do not reset the follow-up clock for `pending` invites that already have `last_outbound_at`
- Send invite SMS; success → `pending`, failure → `failed_send`; reset `followups_sent` to 0 and set `last_outbound_at` on send attempt path used for successful invites
- Set hangout `status=active` and `activated_at` if unset

## Follow-up job

Selects invites on **active** hangouts with status `pending` or `remind`.

Due when `now >= last_outbound_at + delays[followups_sent]` hours, where `delays` comes from `FOLLOWUP_HOURS` (see [local-development.md](./local-development.md)).

Caps:

- At most `max_followups` (2) follow-up sends
- After the budget is exhausted, status becomes `no_response`

Failed follow-up SMS does **not** increment `followups_sent` (does not burn the budget). A cleanup pass also marks invites that somehow exceed the max as `no_response`.

## Related

RSVP keyword handling and inbound SMS: [sms-and-rsvp.md](./sms-and-rsvp.md). Organizer alerts after replies: [organizer-notifications.md](./organizer-notifications.md).
