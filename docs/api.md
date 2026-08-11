# API

JSON routes in `app/routers/api.py`, prefix `/api`. Schemas in `app/schemas.py`. When `CLERK_ENABLED=true`, every API route except `/api/health` requires a verified Clerk session and returns `401` for an unauthenticated request. OpenAPI UI at `/docs` — served only when `ENABLE_API_DOCS` is true, so `/docs`, `/redoc`, and `/openapi.json` all 404 on deployments (see [local-development.md](./local-development.md)).

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health` | `{"status":"ok"}` |
| GET | `/api/places/autocomplete` | Google Places (New) predictions for a location query; returns a disabled response when the integration is not configured |
| GET | `/api/places/details` | Resolves a selected Google place ID to its formatted address and coordinates; requires `place_id` |
| POST | `/api/sms/preview-invite` | Craft invite SMS body from hangout fields (preview only; does not send) |
| POST | `/api/sms/preview` | Full SMS simulator catalog (`messages[]` with key/title/description/body) from hangout fields + optional `contact_ids`; does not create or send |
| GET | `/api/tags` | List tags |
| POST | `/api/tags` | 201; normalized name; case-insensitive unique |
| DELETE | `/api/tags/{id}` | 204 |
| GET | `/api/allergies` | List dietary restrictions (fresh catalogs seed meat, pork once) |
| POST | `/api/allergies` | 201; same uniqueness rules as tags |
| DELETE | `/api/allergies/{id}` | 204 |
| GET | `/api/contacts` | Includes `tags` and `allergies` |
| POST | `/api/contacts` | 201; phone unique after normalize; 400 when the normalized phone is not 8-15 digits |
| PATCH | `/api/contacts/{id}` | Partial update; `tag_ids` / `allergy_ids` replace when present; same phone validation as create |
| DELETE | `/api/contacts/{id}` | 204 |
| * | `/api/profiles`, `/api/profiles/{id}` | Compatibility aliases for the contacts routes (same handlers) |
| GET | `/api/hangouts` | Nested invites + contacts |
| POST | `/api/hangouts` | Creates **draft** + invite rows; does not send SMS |
| GET | `/api/hangouts/{id}` | |
| PATCH | `/api/hangouts/{id}` | Clamps interval/goal/cooldown to allowed option sets; explicit `null` for a column the table requires (the `notify_*` settings) returns `400` |
| POST | `/api/hangouts/{id}/setup` | Omit the body to reuse existing invitees; an explicit `{ "profile_ids": [...] }` or `{ "contact_ids": [...] }` selection returns `400` when empty or invalid, and **removes** invite rows left out of it that were never messaged (see [invites-and-followups.md](./invites-and-followups.md)) |
| POST | `/api/hangouts/{id}/close` | Sets `closed` |

## Contact payloads

Create/update accept optional `drinks`, `smokes`, `drive`, `tag_ids`, `allergy_ids`. Output includes nested `tags` / `allergies` (`id`, `name`) and `created_at`. Schema class names still say `Profile*` while the table is `profiles` (ORM rename deferred).

## Hangout payloads

Create defaults mirror the model/UI notify defaults (interval hours 6, skip-if-unchanged true, confirm/allergy/ride alerts on, decline off, goal 0, cooldown 0). Schema ranges are wider; service layer clamps to the option tuples in [data-model.md](./data-model.md). Invitee id lists accept `profile_ids` or `contact_ids`.

Enabling `notify_enabled` without a resolvable organizer phone (selected contact or My Profile) returns **400** on API create/update.

## Row ids

Ids in a path or payload are bounded to `1 .. 2^63-1` (`app/ids.py`). An id no
row could ever have should miss, not crash, so out-of-range ids return
**422**. Form fields that carry an id as free text (the organizer combobox)
read as blank instead.

## Phone handling

All contact phones go through `normalize_phone` in `app/sms.py` (see [sms-and-rsvp.md](./sms-and-rsvp.md)).
