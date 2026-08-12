# Data model

Defined in `app/models.py`. Schema evolution lives in Alembic migrations under
`migrations/versions/`; `app/database.py` only builds the engine and the
session/audit machinery.

## Enums

| Enum | Values | Notes |
|------|--------|-------|
| `YesNo` | `yes`, `no` | Blank / unset stored as `NULL` |
| `Drive` | `yes`, `no`, `maybe` | Legacy strings `can_drive`/`can` → `yes`, `cannot` → `no` |
| `HangoutStatus` | `draft`, `active`, `closed` | |
| `InviteStatus` | `pending`, `confirmed`, `remind`, `declined`, `no_response`, `failed_send` | |
| `MessageDirection` | `outbound`, `inbound` | |
| `WorkspaceRole` | `owner`, `member` | Membership in one workspace |
| `AccessRole` | `admin`, `member` | Instance-wide; `admin` may edit `access_grants` |

Optional enums use a string `TypeDecorator`: empty string and `"unknown"` bind/result as `NULL`.

## Tables

### `tags` / `allergies`

Catalog rows: `id`, unique `name` (64), `created_at`. Dietary restrictions (table/API name: allergies) are managed in Settings; tags on the Contacts page.

The default catalog (`meat`, `pork`) is seeded by the baseline Alembic
migration — a one-shot data migration that runs once by construction, so
deleting restrictions (including the defaults) persists across restarts and
re-runs of `alembic upgrade head`.

### Association tables

- `profile_tags` — `(profile_id, tag_id)` PK, CASCADE deletes
- `profile_allergies` — `(profile_id, allergy_id)` PK, CASCADE deletes

### `profiles` (Contacts)

Workspace invitee directory. Product language is **Contacts**; table/ORM name remains `profiles` / `Profile` for now.

- Required: `name` (120), `phone` (32, unique per workspace, normalized E.164-ish)
- Optional: `drinks`, `smokes` (`YesNo|None`), `drive` (`Drive|None`)
- Legacy column `food_allergies` (`Text`) kept for migration; prefer M2M `allergies`
- Properties: `food_allergies_label` (joined allergy names, else legacy text), `has_allergies`

### `hangouts`

Optional details: `day_date`, `time`, `duration`, `motive`, `alcohol_involved`, `weed_involved`, `notes`.

**Location** (product: a hangout location, not only a label — see [location-and-carpool.md](./location-and-carpool.md)):

- `location` — human display string (255); used for list/detail headers and SMS `Where:`
- `location_place_id` — optional Google Places id (512) when chosen via autocomplete
- `location_latitude` / `location_longitude` — optional coords from Place Details (pair; incomplete pairs stored as null)

Text-only locations (no Places key or free-typed nickname) leave the structured columns null. Empty `location` clears structure too.

`status` defaults to `draft`.

Soft delete: `deleted_at` (`NULL` = visible on the home list; set = hidden). Rows are kept; invites and message logs remain. UI only allows soft-delete for **closed** hangouts.

Organizer / notify fields:

- `organizer_profile_id` (FK profiles, `ON DELETE SET NULL`), legacy `organizer_phone`
- `notify_enabled` (default false), `notify_interval`, `notify_threshold`
- Interval: `notify_interval_hours` (default 6), `notify_interval_only_if_changed` (default true), `last_digest_fingerprint`
- Threshold: `notify_on_new_confirm` (true), `notify_on_decline` (false), `notify_on_allergy` (true), `notify_on_ride_needed` (true), `notify_confirm_goal` (0 = off), `notify_confirm_goal_sent`, `notify_threshold_cooldown_minutes` (0), `last_organizer_notify_at`
- `activated_at`, `deleted_at`, `created_at`

### `hangout_invites`

`hangout_id`, `profile_id`, `status` (default `pending`), `followups_sent` (0), `last_outbound_at`, `responded_at`, `created_at`.

Both FKs are `ON DELETE CASCADE`, and `Profile.invites` is mapped with
`passive_deletes=True` so the database does the cascading. Deleting a contact
therefore drops their invite rows (and NULLs the `message_logs.invite_id`
pointing at them, keeping the SMS history) instead of failing the `NOT NULL`
constraint on `profile_id`.

### `message_logs`

`invite_id` / `hangout_id` (nullable), `direction`, `phone`, `body`, `success`, `error`, `created_at`.

### `sms_opt_outs`

Global permanent SMS do-not-contact list (not workspace-scoped).

- Unique `phone` (normalized E.164-ish)
- `opted_out_at`, `source` (`keyword` | `admin`), optional `reason`
- Written on carrier STOP / `STOP FOREVER` (and aliases) or by platform admin;
  cleared by `START` / `UNSTOP` or admin remove — see [sms-and-rsvp.md](./sms-and-rsvp.md)

### `access_grants`

`id`, unique `email` (255, stored normalized: stripped and lowercased), `role`,
`created_by` (the admin who added it; `NULL` for rows seeded from
`ACCESS_BOOTSTRAP_ADMINS`), `created_at`.

Instance-wide, deliberately without a `workspace_id`: it is the gate in front
of workspace provisioning, so it must be answerable before a request has a
workspace. Tenant tables are the ones that carry `workspace_id` — see
[tenancy.md](./tenancy.md), which owns what the list means and where it is
enforced.

### `users` (My Profile)

Account-holder settings for the signed-in organizer — **not** the
workspace-scoped invitee directory (Contacts / `profiles` table).

- Unique `clerk_user_id` (Clerk `sub`, or `local-dev` when Clerk is off)
- Optional `display_name`, `phone`, `phone_verified_at` (OTP verification is
  not wired yet; changing phone clears `phone_verified_at`)
- Optional `email` (mirrored from Clerk when known)
- Default organizer-SMS prefs: `default_notify_*` mirrors hangout notify
  columns and prefills **new** hangouts (overridable per hangout)

Helpers: `app/users.py`. UI: Settings → My Profile (`GET /settings`,
`POST /settings/profile`; legacy `/me` redirect/alias).

### `app_settings`

Legacy singleton table (`id=1`) may still exist in older databases; it is no longer declared in `models.py` and nothing reads or writes it.

## Clamped UI/API option sets (`services.py`)

- Interval hours: `1, 2, 3, 4, 6, 8, 12, 24`
- Threshold cooldown minutes: `0, 5, 15, 30, 60`
- Confirm goal: `0, 2, 3, 4, 5, 6, 8, 10`

Values outside these sets are clamped to the defaults used at create time.

## Schema evolution (Alembic)

All schema changes are Alembic migrations in `migrations/versions/`, applied as
a deploy step (`alembic upgrade head` — see [deploy.md](./deploy.md)); the app
never creates or mutates its own schema at startup. Migrations target
PostgreSQL only; there is no SQLite support.

- `migrations/env.py` reads the URL from `app.config` settings (env-driven),
  registers the models on `Base.metadata`, and enables `compare_type` /
  `compare_server_default` so `alembic check` catches model edits that shipped
  without a migration. Regression coverage: `tests/test_migrations.py`
- Enum columns are stored as `VARCHAR` with CHECK constraints
  (`native_enum=False`), storing the lowercase `.value` strings — adding a
  status value never costs an `ALTER TYPE` migration or a table rewrite.
- The default dietary-restriction seed lives in the baseline migration (see
  above). `schema_flags` no longer exists.
