# Web UI

HTML routes: `app/routers/web.py`. Templates: `app/templates/`. Styles/scripts: `app/static/`.

## UX principles (as implemented)

- Minimal chrome: nav is Home, Profiles, Settings only
- Labels and placeholders only — no instructional hint/lead paragraphs on forms
- Optional fields use a blank first option (or unchecked pills), not an “unknown” value
- Shared data: every visitor sees the same profiles/hangouts

## Routes

| Method | Path | Page / action |
|--------|------|----------------|
| GET | `/` | Hangout list + “New hangout” CTA |
| GET/POST | `/profiles` | Profiles page; POST creates profile |
| POST | `/profiles/{id}/delete` | Delete profile |
| POST | `/tags` | Create tag → redirect `/profiles` |
| POST | `/tags/{id}/delete` | Delete tag |
| GET/POST | `/hangouts/new` | Create hangout (`action=draft` or `setup`) |
| GET | `/hangouts/{id}` | Detail / status (`?error=need_profiles`) |
| POST | `/hangouts/{id}/setup` | Activate / (re)send invites |
| POST | `/hangouts/{id}/close` | Close hangout |
| GET | `/settings` | Allergy catalog + SMS webhook path |
| POST | `/allergies` | Create allergy → `/settings` |
| POST | `/allergies/{id}/delete` | Delete allergy |

Blank optional enums from forms are parsed to `None` via `_optional_enum_form`.

## Profiles page

- Tag catalog manager (pill list + × remove + add form)
- Add-profile form: name/phone required; drinks/smokes/drive selects; allergy and tag **pill checkboxes** (`.tag-checkboxes`)
- Existing profiles: 3-column card grid (responsive 2/1 columns)
- **Autosave** (`profiles_autosave.js`): `PATCH /api/profiles/{id}`; text fields debounce 450ms; selects/tags/allergies save immediately
- **Filters** (`profiles_filter.js`): search name/phone/tag; tag chips (OR); field chips AND (drinks/smokes/drive/allergies); clear filters; visible count

## Invitee picker

Partial `_invitee_picker.html` + `invitee_picker.js` (new hangout and hangout detail setup).

- Search filter; Select all / Invert (matched rows) / Clear (all)
- Tag chips toggle-select everyone with that tag; field chips toggle groups
- **Paginated 3×3 grid** (`PAGE_SIZE = 9`); Prev/Next when more than 9 matches
- Checkboxes named `profile_ids`

## New hangout

- All hangout detail fields optional
- Organizer **combobox** (`combobox.js`): typeahead by name/phone → hidden `organizer_profile_id`
- Notify panel (`notify_panel.js`): progressive disclosure (master → interval/threshold → nested options)
- Form defaults: interval every 6h + skip-if-unchanged; threshold confirm/allergy/ride on, decline off; goal off; no cooldown
- If notifications enabled but no organizer phone can be resolved (profile or app settings), notify flags are left off on create

## Hangout detail

Status badge, logistics, organizer notify summary, RSVP counts, invitee table (status, drive/allergies/drinks/smokes, follow-up count), setup/re-invite form, close action.

## Settings

Food-allergy catalog (same pill list pattern as tags), webhook path shown as `POST /webhooks/sms`.
