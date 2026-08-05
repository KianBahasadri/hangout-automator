# API

JSON routes in `app/routers/api.py`, prefix `/api`. Schemas in `app/schemas.py`. OpenAPI UI at `/docs` — served only when `ENABLE_API_DOCS` is true, so `/docs`, `/redoc`, and `/openapi.json` all 404 on deployments (see [local-development.md](./local-development.md)).

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/health` | `{"status":"ok"}` |
| GET | `/api/tags` | List tags |
| POST | `/api/tags` | 201; normalized name; case-insensitive unique |
| DELETE | `/api/tags/{id}` | 204 |
| GET | `/api/allergies` | List allergies |
| POST | `/api/allergies` | 201; same uniqueness rules as tags |
| DELETE | `/api/allergies/{id}` | 204 |
| GET | `/api/profiles` | Includes `tags` and `allergies` |
| POST | `/api/profiles` | 201; phone unique after normalize; 400 when the normalized phone is not 8-15 digits |
| PATCH | `/api/profiles/{id}` | Partial update; `tag_ids` / `allergy_ids` replace when present; same phone validation as create |
| DELETE | `/api/profiles/{id}` | 204 |
| GET | `/api/hangouts` | Nested invites + profiles |
| POST | `/api/hangouts` | Creates **draft** + invite rows; does not send SMS |
| GET | `/api/hangouts/{id}` | |
| PATCH | `/api/hangouts/{id}` | Clamps interval/goal/cooldown to allowed option sets |
| POST | `/api/hangouts/{id}/setup` | Optional body `{ "profile_ids": [...] }`; an explicit list also **removes** invite rows left out of it that were never messaged (see [invites-and-followups.md](./invites-and-followups.md)) |
| POST | `/api/hangouts/{id}/close` | Sets `closed` |

## Profile payloads

Create/update accept optional `drinks`, `smokes`, `drive`, `tag_ids`, `allergy_ids`. Output includes nested `tags` / `allergies` (`id`, `name`) and `created_at`.

## Hangout payloads

Create defaults mirror the model/UI notify defaults (interval hours 6, skip-if-unchanged true, confirm/allergy/ride alerts on, decline off, goal 0, cooldown 0). Schema ranges are wider; service layer clamps to the option tuples in [data-model.md](./data-model.md).

Enabling `notify_enabled` without a resolvable organizer phone (selected profile) returns **400** on API create/update.

## Phone handling

All profile phones go through `normalize_phone` in `app/sms.py` (see [sms-and-rsvp.md](./sms-and-rsvp.md)).
