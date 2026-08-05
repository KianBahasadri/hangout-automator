# Data model

Defined in `app/models.py`. SQLite bootstrap and lightweight schema evolution live in `app/database.py`.

## Enums

| Enum | Values | Notes |
|------|--------|-------|
| `YesNo` | `yes`, `no` | Blank / unset stored as `NULL` |
| `Drive` | `yes`, `no`, `maybe` | Legacy strings `can_drive`/`can` → `yes`, `cannot` → `no` |
| `HangoutStatus` | `draft`, `active`, `closed` | |
| `InviteStatus` | `pending`, `confirmed`, `remind`, `declined`, `no_response`, `failed_send` | |
| `MessageDirection` | `outbound`, `inbound` | |

Optional enums use a string `TypeDecorator`: empty string and `"unknown"` bind/result as `NULL`.

## Tables

### `tags` / `allergies`

Catalog rows: `id`, unique `name` (64), `created_at`. Allergies are managed in Settings; tags on the Profiles page.

### Association tables

- `profile_tags` — `(profile_id, tag_id)` PK, CASCADE deletes
- `profile_allergies` — `(profile_id, allergy_id)` PK, CASCADE deletes

### `profiles`

- Required: `name` (120), `phone` (32, unique, normalized E.164-ish)
- Optional: `drinks`, `smokes` (`YesNo|None`), `drive` (`Drive|None`)
- Legacy column `food_allergies` (`Text`) kept for migration; prefer M2M `allergies`
- Properties: `food_allergies_label` (joined allergy names, else legacy text), `has_allergies`

### `hangouts`

Optional details: `day_date`, `time`, `duration`, `motive`, `alcohol_involved`, `weed_involved`, `notes`.

`status` defaults to `draft`.

Organizer / notify fields:

- `organizer_profile_id` (FK profiles, `ON DELETE SET NULL`), legacy `organizer_phone`
- `notify_enabled` (default false), `notify_interval`, `notify_threshold`
- Interval: `notify_interval_hours` (default 6), `notify_interval_only_if_changed` (default true), `last_digest_fingerprint`
- Threshold: `notify_on_new_confirm` (true), `notify_on_decline` (false), `notify_on_allergy` (true), `notify_on_ride_needed` (true), `notify_confirm_goal` (0 = off), `notify_confirm_goal_sent`, `notify_threshold_cooldown_minutes` (0), `last_organizer_notify_at`
- `activated_at`, `created_at`

### `hangout_invites`

`hangout_id`, `profile_id`, `status` (default `pending`), `followups_sent` (0), `last_outbound_at`, `responded_at`, `created_at`.

### `message_logs`

`invite_id` / `hangout_id` (nullable), `direction`, `phone`, `body`, `success`, `error`, `created_at`.

### `app_settings`

Legacy singleton table (`id=1`) may still exist in older databases. The app no longer exposes or uses a global default organizer phone; organizer SMS requires selecting an organizer profile on the hangout.

## Clamped UI/API option sets (`services.py`)

- Interval hours: `1, 2, 3, 4, 6, 8, 12, 24`
- Threshold cooldown minutes: `0, 5, 15, 30, 60`
- Confirm goal: `0, 2, 3, 4, 5, 6, 8, 10`

Values outside these sets are clamped to the defaults used at create time.

## SQLite ensure / migrate

`init_db()` → `create_all`, then:

1. **`_ensure_sqlite_columns`** — `ALTER TABLE` add missing hangout notify/`weed_involved` columns; add `profiles.drive`; copy `car_access` → `drive`; set `'unknown'` enum strings to `NULL` where possible
2. **`_rebuild_profiles_if_needed`** — recreate `profiles` if `drinks`/`smokes` were `NOT NULL` or `car_access` still exists (SQLite cannot drop nullability in place)
3. **`_migrate_legacy_food_allergies`** — split comma/semicolon free-text into `Allergy` rows + M2M links, clear legacy text

Connection PRAGMAs (SQLite): `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.
