# Web UI

HTML routes: `app/routers/web.py`. Templates: `app/templates/`. Styles/scripts: `app/static/`.

## UX principles (as implemented)

- Minimal chrome: nav is Home, Profiles, Settings, plus a light/dark theme toggle
- Labels and placeholders only — no instructional hint/lead paragraphs on forms
- Optional fields use a blank first option (or unchecked pills), not an “unknown” value
- Shared data: every visitor sees the same profiles/hangouts

## Theme

`base.html` (inline script) + CSS tokens in `static/style.css`.

- Toggle sits immediately right of **Settings** in the header nav
- Preference is stored in `localStorage` key `theme` (`light` | `dark`)
- Default is dark (`data-theme="dark"` on `<html>`, also the unscoped token set)
- Head script applies the stored theme before paint; the button calls
  `window.__hangoutTheme.toggle()` (no external JS required)
- Light palette is `html[data-theme="light"]` overrides only

## Routes

| Method | Path | Page / action |
|--------|------|----------------|
| GET | `/` | Hangout list without database ID numbers; “New hangout” sits immediately beside the page heading |
| GET | `/profiles` | Profiles page; tags and existing profiles |
| GET | `/profiles/new` | Add one or more profiles, with optional phone contact import |
| POST | `/profiles` | Validate and save the add-profiles batch |
| POST | `/profiles/{id}/delete` | Delete profile |
| POST | `/tags` | Create tag → redirect `/profiles` |
| POST | `/tags/{id}/delete` | Delete tag |
| GET/POST | `/hangouts/new` | Create hangout (`action=draft` or `setup`) |
| GET | `/hangouts/{id}` | Detail / status (`?error=need_profiles`) |
| POST | `/hangouts/{id}/setup` | Activate / (re)send invites |
| POST | `/hangouts/{id}/close` | End hangout |
| POST | `/hangouts/{id}/delete` | Soft-delete (closed only) — sets `deleted_at`, hides from home |
| POST | `/hangouts/{id}/restore` | Clear `deleted_at` so the hangout appears on home again |
| GET | `/settings` | Dietary-restriction catalog, deleted hangouts, SMS simulator, log download |
| GET | `/settings/deleted-hangouts` | Soft-deleted hangouts; optional `?q=` motive search |
| GET | `/settings/sms-simulator` | Preview sample outbound / auto-reply SMS layouts (not sent) |
| GET | `/settings/logs` | Download the active JSONL audit log (`LOG_FILE`) |
| POST | `/allergies` | Create dietary restriction → `/settings` |
| POST | `/allergies/{id}/delete` | Delete dietary restriction |

Blank optional enums from forms are parsed to `None` via `_optional_enum_form`.

## Profiles page

- Tag catalog manager (pill list + × remove + add form)
- “Add new profile” opens the dedicated add-profiles page
- Existing profiles: 3-column card grid (responsive 2/1 columns)
- **Autosave** (`profiles_autosave.js`): `PATCH /api/profiles/{id}`; text fields debounce 450ms; selects/tags/allergies save immediately
- **Filters** (`profiles_filter.js`): search name/phone/tag; tag chips (OR); field chips AND (drinks/smokes/drive/allergies); clear filters; visible count

## Add profiles

- Each profile card has name/phone fields, drinks/smokes/drive selects, and allergy/tag **pill checkboxes** (`.tag-checkboxes`)
- “Add another profile” adds another editable card; “Save profiles” validates and saves the entire batch together
- **Phone import** (`profiles_new.js`) uses the browser Contact Picker when available, requests name and phone, and fills one card per selected contact. The selected values remain editable and are not saved until the batch form is submitted; unsupported browsers show the manual-entry state.
- Server-side validation rejects unusable or duplicate phone numbers and does not partially save a batch; rejected cards retain their entered values for correction. A phone that normalizes to fewer than 8 digits is rejected rather than silently stored — see [sms-and-rsvp.md](./sms-and-rsvp.md) for normalization

## Invitee picker

Partial `_invitee_picker.html` + `invitee_picker.js` (new hangout and hangout detail setup).

- Search filter; Select all / Invert (matched rows) / Clear (all)
- Tag chips toggle-select everyone with that tag; field chips toggle groups
- **Paginated 3×3 grid** (`PAGE_SIZE = 9`); Prev/Next when more than 9 matches
- Checkboxes named `profile_ids`

## New hangout

- All hangout detail fields optional (day, time, duration, location, motive, alcohol, weed, notes)
- Submitting setup without invitees keeps the hangout as a draft and redirects back with a profile-selection error
- Organizer **combobox** (`combobox.js`): typeahead by name/phone → hidden `organizer_profile_id`
- Notify panel (`notify_panel.js`): progressive disclosure (master → interval/threshold → nested options)
- Form defaults: interval every 6h + skip-if-unchanged; threshold confirm/allergy/ride on, decline off; goal off; no cooldown
- If notifications enabled but no organizer phone can be resolved (profile or app settings), notify flags are left off on create
- **Preview invite SMS** button (left of “Set up hangout”) opens a dialog; body from `POST /api/sms/preview-invite` using the current form fields and the first selected invitee’s name (or “Alex”)

## Hangout detail

Status badge (`Happening Now` for active, `Hangout Over` for closed), logistics,
organizer notify summary, RSVP counts, invitee table (status,
drive/allergies/drinks/smokes, follow-up count), setup/re-invite form, end
action. **Closed** hangouts show **Delete hangout** (soft-delete). Soft-deleted
hangouts show a restore control instead of setup/end.

Home list and `GET /api/hangouts` omit rows with `deleted_at` set.

## Settings

Dietary Restrictions catalog (same pill list pattern as tags; defaults
`meat` and `pork` are seeded on startup). **Deleted hangouts** lists soft-deleted
rows with motive search (`?q=`). **SMS simulator** card links to
`GET /settings/sms-simulator`, which renders sample invite / follow-up /
RSVP / INFO / organizer copy in phone-style bubbles (nothing is sent).
Logs card links to `GET /settings/logs`, which returns the active
`LOG_FILE` as an attachment (404 if the file does not exist yet). See
[logging.md](./logging.md).
