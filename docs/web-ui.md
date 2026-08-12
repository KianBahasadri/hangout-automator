# Web UI

HTML routes: `app/routers/web.py`. Templates: `app/templates/`. Styles/scripts: `app/static/`.

## UX principles (as implemented)

- Minimal chrome: nav is Home, Contacts, Settings, **Admin Panel** (admins only),
  plus a light/dark theme toggle. Clerk account / sign-out lives under Settings,
  not the header
- **Labels and placeholders only** — no instructional help copy, lead paragraphs, or
  explainer text under headings. Bad examples (do not reintroduce):
  - “Your personal account settings. This is not the contacts list under Contacts…”
  - “Prefills new hangouts. You can still change each hangout.”
  - “Saved for organizer SMS. Phone confirmation will land with a later update.”
  Empty states (“No tags yet”), flash alerts after an action, and field
  placeholders are fine; prose that teaches the product on the form is not.
- Optional fields use a blank first option (or unchecked pills), not an “unknown” value
- Multi-tenant: each authenticated user works in their own workspace; My Profile
  (under Settings) is personal (account-holder), while Contacts is the invitee
  directory for the active workspace

## Theme

`base.html` (inline script) + CSS tokens in `static/style.css`.

- Toggle sits at the right end of the header nav (after Admin Panel when shown)
- Preference is stored in `localStorage` key `theme` (`light` | `dark`)
- Default is dark (`data-theme="dark"` on `<html>`, also the unscoped token set)
- Head script applies the stored theme before paint; the button calls
  `window.__hangoutTheme.toggle()` (no external JS required)
- Light palette is `html[data-theme="light"]` overrides only

## Destructive-action confirmations

Delete forms (contact, tag, dietary restriction) carry the prompt text in a
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
| GET | `/me` | Legacy redirect (307) to `/settings` |
| POST | `/me` | Legacy alias for `POST /settings/profile` |
| GET | `/contacts` | Contacts page; tags and existing contacts (invitee directory for this workspace) |
| GET | `/contacts/new` | Add one or more contacts, with optional phone contact import |
| POST | `/contacts` | Validate and save the add-contacts batch |
| POST | `/contacts/{id}/delete` | Delete contact |
| GET | `/profiles`, `/profiles/new` | Legacy redirects (307) to `/contacts` / `/contacts/new` |
| POST | `/profiles`, `/profiles/{id}/delete` | Legacy aliases for the contacts POST routes |
| POST | `/tags` | Create tag → redirect `/contacts` |
| POST | `/tags/{id}/delete` | Delete tag |
| GET/POST | `/hangouts/new` | Create hangout (`action=draft` or `setup`); organizer SMS is not on this form — stamped from Settings → My Profile on create; invitee picker badges DNC phones as **won't be texted** |
| GET/POST | `/hangouts/{id}/edit` | Draft-only prefilled edit form (`action=draft` or `setup`) |
| GET | `/hangouts/{id}` | Detail / status (`?error=need_contacts`); invitee rows show DNC badge when applicable |
| POST | `/hangouts/{id}/setup` | Activate / (re)send invites |
| POST | `/hangouts/{id}/close` | End hangout |
| POST | `/hangouts/{id}/delete` | Soft-delete (closed only) — sets `deleted_at`, hides from home |
| POST | `/hangouts/{id}/restore` | Clear `deleted_at` so the hangout appears on home again |
| GET | `/settings` | My Profile (name, phone, default organizer SMS), dietary-restriction catalog, deleted hangouts, SMS simulator |
| POST | `/settings/profile` | Save My Profile fields → `/settings?notice=saved` |
| GET | `/admin` | Admin-only: cost monitoring cards + links to access, SMS opt-outs, log download, ops tools |
| GET | `/admin/access` | Admin-only: the email access list (`?notice=` feedback) |
| POST | `/admin/access` | Admin-only: add an email or change its role → `/admin/access` |
| POST | `/admin/access/{id}/delete` | Admin-only: revoke an email |
| GET | `/admin/opt-outs` | Admin-only: permanent SMS do-not-contact list |
| POST | `/admin/opt-outs` | Admin-only: add a phone to DNC |
| POST | `/admin/opt-outs/{id}/delete` | Admin-only: remove a phone from DNC |
| GET | `/admin/logs` | Admin-only: download the active JSONL audit log (`LOG_FILE`) |
| GET | `/settings/access` | Legacy redirect (307) to `/admin/access` |
| POST | `/settings/access`, `/settings/access/{id}/delete` | Legacy aliases for the admin access POST routes |
| GET | `/settings/logs` | Legacy redirect (307) to `/admin/logs` |
| GET | `/settings/deleted-hangouts` | Soft-deleted hangouts; optional `?q=` motive search |
| GET | `/settings/sms-simulator` | Live SMS simulator: create-hangout form + all message-type previews (not sent) |
| POST | `/allergies` | Create dietary restriction → `/settings` |
| POST | `/allergies/{id}/delete` | Delete dietary restriction |

Blank optional enums from forms are parsed to `None` via `_optional_enum_form`.

## Contacts page

- Tag catalog manager (pill list + × remove + add form)
- “Add new contact” opens the dedicated add-contacts page
- Existing contacts: 3-column card grid (responsive 2/1 columns)
- **Autosave** (`contacts_autosave.js`): `PATCH /api/contacts/{id}`; text fields debounce 450ms; selects/tags/allergies save immediately
- **Filters** (`contacts_filter.js`): search name/phone/tag; tag chips (OR); field chips AND (drinks/smokes/drive/allergies); clear filters; visible count

## Add contacts

- Each contact card has name/phone fields, drinks/smokes/drive selects, and allergy/tag **pill checkboxes** (`.tag-checkboxes`)
- “Add another contact” adds another editable card; “Save contacts” validates and saves the entire batch together
- **Phone import** (`contacts_new.js`) uses the browser Contact Picker when available, requests name and phone, and fills one card per selected contact. The selected values remain editable and are not saved until the batch form is submitted; unsupported browsers show the manual-entry state.
- Server-side validation rejects unusable or duplicate phone numbers and does not partially save a batch; rejected cards retain their entered values for correction. A phone that normalizes to fewer than 8 digits is rejected rather than silently stored — see [sms-and-rsvp.md](./sms-and-rsvp.md) for normalization

## Invitee picker

Partial `_invitee_picker.html` + `invitee_picker.js` (new/edit hangout form and hangout detail setup).

- Search filter; Select all / Invert (matched rows) / Clear (all)
- Tag chips toggle-select everyone with that tag; field chips toggle groups
- **Paginated 3×3 grid** (`PAGE_SIZE = 9`); Prev/Next when more than 9 matches
- Checkboxes named `contact_ids` (legacy `profile_ids` still accepted by form handlers)

## New hangout

- All hangout detail fields optional (day, time, duration, location, motive, alcohol, weed, notes)
- Header includes an **All hangouts** back button to return to the hangout list without submitting
- **Location** uses Google Places (New) suggestions when configured; selecting a suggestion stores display text plus hidden `location_place_id` / lat / lng. Manual typing clears the structured fields (text-only location). Without a key, the field is free-text only and shows an inline status when suggestions fail
- Submitting setup without invitees keeps the hangout as a draft and returns the create/edit form with a contact-selection error
- Organizer SMS is **not** on this form — stamped from Settings → My Profile at create (matching contact by phone when one exists)
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

**Account** (only when `CLERK_ENABLED`): mounts Clerk’s user button
(`#clerk-user-button` via `clerk_auth.js`) for profile management and sign-out.
Not shown in the header nav.

**My Profile**: account-holder name, phone, and default organizer SMS prefs
(`POST /settings/profile`). Personal (`users` table), not workspace-scoped.
Legacy `GET/POST /me` redirect/alias to these routes. Flash notices use
`?notice=` (`saved`, `invalid-phone`).

Dietary Restrictions catalog (same pill list pattern as tags; defaults
`meat` and `pork` are seeded once into an empty catalog — deletions persist).

**Deleted hangouts** lists soft-deleted rows with motive search (`?q=`).
**SMS simulator** card links to `GET /settings/sms-simulator`. That page reuses
the same hangout detail + invitee form as **New hangout** (shared partial
`_hangout_form_fields.html`) beside live phone-style bubbles for every
outbound/auto-reply type. Editing fields or invitee selection debounces
(~400ms) and refreshes via `POST /api/sms/preview` (same `craft_*` builders as
production). The form never creates a hangout or sends SMS. See
[sms-and-rsvp.md](./sms-and-rsvp.md).

## Admin Panel

Admin-only (`require_admin`). Nav link sits to the right of Settings and is
hidden from members. Local dev with Clerk off treats everyone as admin.

**Tools** include SMS simulator (still under `/settings/…` for now) and
**Download logs** → `GET /admin/logs` (length-stable snapshot of active
`LOG_FILE` as attachment; 404 if missing; legacy `/settings/logs` redirects).
See [logging.md](./logging.md).

**Costs** (KIAN-535 Phase A): cards for Twilio, Azure, Cloudflare.

- **Twilio:** MTD / 7d / 30d counts from `message_logs` (outbound success,
  outbound fail, inbound). Optional `TWILIO_SMS_PRICE_ESTIMATE` multiplies
  billable (outbound OK + inbound) for a rough `$` labeled **estimate**.
  Live Twilio Usage API is not required.
- **Azure:** label from `AZURE_RESOURCE_GROUP` (+ optional
  `AZURE_SUBSCRIPTION_ID` for a portal deep link). No live Cost Management
  API yet.
- **Cloudflare:** unavailable note + dashboard billing link.

**Access** lives at `GET /admin/access` (legacy `/settings/access` redirects).
Lists allowed emails with role, signed-in status, and who added them; form to
add/change role. Outcomes use `?notice=` (`added`, `removed`, `role-updated`,
`already-listed`, `invalid-email`, `last-admin`, `not-found`). Removing or
demoting the last admin is refused. Meaning of the list:
[tenancy.md](./tenancy.md).

A signed-in user with no grant never reaches these pages: the auth middleware
answers 403 with standalone `no_access.html` (no app nav).
