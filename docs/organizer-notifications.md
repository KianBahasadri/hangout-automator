# Organizer notifications

Configured per hangout (web new-hangout form or API). Phone resolution and send logic: `app/services.py`.

## Who gets SMS

`resolve_organizer_phone` order:

1. Selected organizer **contact** profile’s phone (`organizer_profile_id`)
2. Hangout `organizer_phone` (stamped at create/edit from that contact, or
   from the creator’s **My Profile** phone when no contact is selected)

If none resolve, notifications cannot send (web create leaves notify off; API
returns 400 when enabling notify without a resolvable phone).

Account-holder defaults live on **My Profile** (`/me`, `users` table): phone
and default notify toggles. On hangout **create**, those defaults are stamped
onto the hangout (matching contact profile by phone when one exists). The new
hangout form does not expose per-hangout organizer SMS controls. Defaults are
personal, not workspace-scoped — two users never share a My Profile row.

## Interval digests

Job every **10 minutes**: active hangouts with `notify_enabled` and `notify_interval`.

- Cadence hours: hangout `notify_interval_hours`, else settings `ORGANIZER_INTERVAL_HOURS`, else 6
- Time anchor: `last_organizer_notify_at`, else `activated_at`, else now
- Fingerprint: per invite `profile_id:status:allergyFlag:drive`
- If `notify_interval_only_if_changed` and fingerprint unchanged: advance `last_organizer_notify_at` **without** sending
- Otherwise send organizer digest SMS and store fingerprint + timestamp

Digest body format: [sms-and-rsvp.md](./sms-and-rsvp.md).

## Threshold alerts

Evaluated after an invitee reply (`evaluate_organizer_threshold_for_reply`) when `notify_enabled` and `notify_threshold`.

On **new confirm** (status was not already confirmed):

- Optional “new confirmation” (`notify_on_new_confirm`)
- Optional “dietary restriction” if confirmer has restrictions (`notify_on_allergy`) — high priority
- Optional “ride needed” if confirmer `drive == no` (`notify_on_ride_needed`) — high priority
- Optional one-shot confirmed-count milestone (`notify_confirm_goal` > 0 and not yet `notify_confirm_goal_sent`) — high priority

On **new decline**:

- Optional “decline” (`notify_on_decline`)

Cooldown (`notify_threshold_cooldown_minutes`): if > 0 and last notify was recent, routine confirm/decline alerts are skipped. Allergy, ride, and milestone reasons set `high_priority` and **bypass** cooldown.

Successful threshold send appends `(Event: …)` to the digest body, updates `last_organizer_notify_at` + fingerprint, and sets `notify_confirm_goal_sent` when a goal fired.

## Defaults (create)

| Field | Default |
|-------|---------|
| `notify_enabled` | false |
| Interval hours | 6 |
| Skip if unchanged | true |
| On new confirm | true |
| On decline | false |
| On allergy | true |
| On ride needed | true |
| Confirm goal | 0 (off) |
| Cooldown minutes | 0 |

Allowed option values: [data-model.md](./data-model.md).
