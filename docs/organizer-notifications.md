# Organizer notifications

Configured per hangout (web new-hangout form or API). Phone resolution and send logic: `app/services.py`.

## Who gets SMS

`resolve_organizer_phone` order:

1. Selected organizer profile’s phone (`organizer_profile_id`)
2. Legacy hangout `organizer_phone`

If none resolve, notifications cannot send (web create leaves notify off; API returns 400 when enabling notify without an organizer profile phone).

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
- Optional “allergy note” if confirmer has allergies (`notify_on_allergy`) — high priority
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
