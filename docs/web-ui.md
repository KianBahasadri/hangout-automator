# Web UI

HTML routes: `app/routers/web.py`. Templates: `app/templates/`. Styles/scripts: `app/static/`.

## UX principles (as implemented)

- Minimal chrome: nav is Home, Profiles, **My Profile**, Settings, plus a light/dark theme toggle
- **Labels and placeholders only** — no instructional help copy, lead paragraphs, or
  explainer text under headings. Bad examples (do not reintroduce):
  - “Your personal account settings. This is not the contacts list under Profiles…”
  - “Prefills new hangouts. You can still change each hangout.”
  - “Saved for organizer SMS. Phone confirmation will land with a later update.”
  Empty states (“No tags yet”), flash alerts after an action, and field
  placeholders are fine; prose that teaches the product on the form is not.
- Optional fields use a blank first option (or unchecked pills), not an “unknown” value
- Multi-tenant: each authenticated user works in their own workspace; My Profile is personal (account-holder), while Profiles is the invitee directory for the active workspace

## Theme

`base.html` (inline script) + CSS tokens in `static/style.css`.

- Toggle sits immediately right of **Settings** in the header nav
- Preference is stored in `localStorage` key `theme` (`light` | `dark`)
- Default is dark (`data-theme="dark"` on `<html>`, also the unscoped token set)
- Head script applies the stored theme before paint; the button calls
  `window.__hangoutTheme.toggle()` (no external JS required)
- Light palette is `html[data-theme="light"]` overrides only

## Destructive-action confirmations

Delete forms (profile, tag, dietary restriction) carry the prompt text in a
`data-confirm` attribute; `static/confirm.js` binds one delegated `submit`
listener on `document` and cancels the event when the user declines.

The message must never be interpolated into an inline
`onsubmit="return confirm('…')"`. Jinja escapes for HTML, not for JavaScript,
so a name containing an apostrophe (`O'Brien` → `O&#39;Brien`) was decoded by
the HTML parser back into a bare quote that closed the string literal early:
the handler failed to parse, never ran, and the form submitted **with no
prompt at all**. A `data-` attribute read back through `dataset` stays a
string whatever characters it holds.

## Routes

| Method | Path | Page / action |
|--------|------|----------------|
| GET | `/` | Hangout list without database ID numbers; “New hangout” sits immediately beside the page heading |
| GET/POST | `/me` | My Profile: account-holder name, phone, default organizer SMS prefs |
| GET | `/profiles` | Profiles page; tags and existing profiles (invitee contacts for this workspace) |
| GET | `/profiles/new` | Add one or more profiles, with optional phone contact import |
| POST | `/profiles` | Validate and save the add-profiles batch |
| POST | `/profiles/{id}/delete` | Delete profile |
| POST | `/tags` | Create tag → redirect `/profiles` |
| POST | `/tags/{id}/delete` | Delete tag |
| GET/POST | `/hangouts/new` | Create hangout (`action=draft` or `setup`); organizer SMS is not on this form — stamped from My Profile on create |
| GET/POST | `/hangouts/{id}/edit` | Draft-only prefilled edit form (`action=draft` or `setup`) |
| GET | `/hangouts/{id}` | Detail / status (`?error=need_profiles`) |
| POST | `/hangouts/{id}/setup` | Activate / (re)send invites |
| POST | `/hangouts/{id}/close` | End hangout |
| POST | `/hangouts/{id}/delete` | Soft-delete (closed only) — sets `deleted_at`, hides from home |
| POST | `/hangouts/{id}/restore` | Clear `deleted_at` so the hangout appears on home again |
| GET | `/settings` | Dietary-restriction catalog, access, deleted hangouts, SMS simulator, log download |
| GET | `/settings/access` | Admin-only: the email access list (`?notice=` feedback) |
| POST | `/settings/access` | Admin-only: add an email or change its role → `/settings/access` |
| POST | `/settings/access/{id}/delete` | Admin-only: revoke an email |
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

Partial `_invitee_picker.html` + `invitee_picker.js` (new/edit hangout form and hangout detail setup).

- Search filter; Select all / Invert (matched rows) / Clear (all)
- Tag chips toggle-select everyone with that tag; field chips toggle groups
- **Paginated 3×3 grid** (`PAGE_SIZE = 9`); Prev/Next when more than 9 matches
- Checkboxes named `profile_ids`

## New hangout

- All hangout detail fields optional (day, time, duration, location, motive, alcohol, weed, notes)
- Header includes an **All hangouts** back button to return to the hangout list without submitting
- Location uses Google Places (New) suggestions when configured; if the service is unavailable, the field remains free-text and shows an inline status message
- Submitting setup without invitees keeps the hangout as a draft and returns the create/edit form with a profile-selection error
- Organizer **combobox** (`combobox.js`): typeahead by name/phone → hidden `organizer_profile_id`
- Notify panel (`notify_panel.js`): progressive disclosure (master → interval/threshold → nested options)
- Form defaults: interval every 6h + skip-if-unchanged; threshold confirm/allergy/ride on, decline off; goal off; no cooldown
- If notifications enabled but no organizer phone can be resolved (profile or app settings), notify flags are left off when saving
- **Preview invite SMS** button (left of “Set up hangout”) opens a dialog; body from `POST /api/sms/preview-invite` using the current form fields and the first selected invitee’s name (or “Alex”)

## Draft editing

- Opening a visible draft through its home-list link or its detail URL sends the visitor to `/hangouts/{id}/edit`
- The edit form preloads saved detail, organizer-notification, and invitee selections; it has no manual save button and saves field changes in the background, showing successful saves in a toast and save failures inline
- **Set up hangout** waits for the latest draft save, then activates it and sends the selected invites
- Non-draft or hidden hangouts cannot be edited through the draft edit routes and return to their detail page

## Hangout detail

Status badge (`Happening Now` for active, `Hangout Over` for closed), logistics,
organizer notify summary, RSVP counts, invitee table (status,
drive/allergies/drinks/smokes, follow-up count), setup/re-invite form, end
action. **Closed** hangouts show **Delete hangout** (soft-delete). Soft-deleted
hangouts show a restore control instead of setup/end.

Home list, hangout detail, and deleted-hangouts list humanize `day_date` /
`time` / `duration` with the same helpers as SMS (`August 8, 2026` · `7:00 PM` ·
`3 hours`). Storage remains ISO / raw numeric duration.

Home list and `GET /api/hangouts` omit rows with `deleted_at` set.

## Settings

Dietary Restrictions catalog (same pill list pattern as tags; defaults
`meat` and `pork` are seeded once into an empty catalog — deletions persist).
**Access** card links to `GET /settings/access`, which lists every allowed
email with its role, whether that person has signed in yet, and who added
them, plus a form to add one. Non-admins get a 403 on all three routes; the
card is still shown to them. Outcomes come back as `?notice=` codes
(`added`, `removed`, `role-updated`, `already-listed`, `invalid-email`,
`last-admin`, `not-found`) rendered as an `.alert`. Removing or demoting the
last admin is refused. What the list means lives in
[tenancy.md](./tenancy.md).

A signed-in user with no grant never reaches any of these pages: the auth
middleware answers 403 with the standalone `no_access.html`, which extends
nothing so it renders without the app nav or Clerk widgets.

**Deleted hangouts** lists soft-deleted rows with motive search (`?q=`).
**SMS simulator** card links to `GET /settings/sms-simulator`, which renders
sample invite / follow-up / RSVP / INFO / organizer copy in phone-style
bubbles (nothing is sent).
Logs card links to `GET /settings/logs`, which returns the active
`LOG_FILE` as an attachment (404 if the file does not exist yet). See
[logging.md](./logging.md).
