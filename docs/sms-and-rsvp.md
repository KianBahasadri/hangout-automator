# SMS and RSVP

Providers and phone normalization: `app/sms.py`. Message bodies and reply parsing: `app/messages.py`. Inbound HTTP: `app/routers/webhooks.py`. Orchestration: `process_inbound_sms` in `app/services.py`.

## Providers

| `SMS_PROVIDER` | Behavior |
|----------------|----------|
| `mock` (default) | Logs and prints to stdout; always succeeds |
| `twilio` | Twilio REST `messages.create`; requires account SID, auth token, from number |

App startup fails with a clear error when `SMS_PROVIDER=twilio` is set without all three Twilio credentials (config validated in `app/config.py`); `mock` requires none. Terraform also fails at plan time for the same misconfiguration (see [deploy.md](./deploy.md)).

`send_sms` always writes a `message_logs` row (success or error, including a
provider exception). Before provider lookup/send it:

1. Rejects destinations that are not a leading `+` with 8–15 digits
   (`reason=invalid_destination_phone`)
2. Rejects numbers on the permanent do-not-contact list (`sms_opt_outs`;
   `reason=do_not_contact`, error text *Number is on the do-not-contact list*)

Both rejections skip the provider, log an unsuccessful `message_logs` row, and
emit `sms.outbound.rejected`.

## Phone normalization

`normalize_phone`: strips to digits and a leading `+`. Bare 10-digit numbers become `+1…`. Stored values stay E.164-ish for SMS.

## Phone display

`format_phone` (Jinja filter `|phone`, and `phone_format.js` on `input[type="tel"]`) shows the standard NANP form `+1 (XXX) XXX-XXXX`. Other country codes display as `+` plus digit groups. Storage and Twilio sends remain normalized; only UI input/display is pretty-printed.

## Previewing copy

- **Settings → SMS simulator** (`GET /settings/sms-simulator`) — same create-hangout form fields and invitee picker; previews for every outbound/auto-reply type **live-update** as fields change via `POST /api/sms/preview` (debounced in `sms_simulator.js`). Optional selected contacts personalize names and sample guest lists (INFO / MORE INFO / organizer digests use synthetic RSVP statuses for preview only). Empty fields use the same fallbacks as real invites (`"a hangout"`, omitted optional lines). Nothing is saved or sent.
- **New hangout → Preview invite SMS** — dialog still uses `POST /api/sms/preview-invite` with the form’s current fields (invite body only)

## Outbound copy

Messages use short multi-line layout (labels + blank lines) so they read clearly on phones.

- **Hangout summary** — motive; `When:` long-form date (e.g. `August 8, 2026`) / 12-hour time (e.g. `7:00 PM`) / duration with hours unit (e.g. `(3 hours)`); `Where:` hangout location **display string** only (`location_display` / `hangouts.location` — never place ids or coordinates); `Alcohol:` / `Weed:` only if set to yes/no; `Notes:`; fallback phrase `"a hangout"`. Storage stays ISO (`YYYY-MM-DD`, `HH:MM`) and raw duration; the same formatters humanize both SMS and the hangout list/detail UI.
- **Invite / follow-up** — greeting + summary + reply menu (keywords only, no explanations):
  - **CONFIRM**
  - **NO**
  - **INFO**
  - **MORE INFO**
- **RSVP acks** — confirm / decline acknowledgements (confirm also points at INFO / MORE INFO)
- **INFO** — headcounts only (coming / pending / declined / invited) + the requester’s RSVP status; points at MORE INFO
- **MORE INFO** — named lists for coming / pending / declined, plus confirmed logistics (restrictions, rides, drivers) + requester’s RSVP
- **Organizer digest** — same named coming / pending / declined layout as MORE INFO; dietary restrictions for confirmed; Needs ride / Can drive for confirmed with `drive` no/yes

### Website footer

When `PUBLIC_BASE_URL` is a usable absolute `http(s)` URL, invite, follow-up,
confirm/decline acks, INFO / MORE INFO, and organizer digests append a short
footer:

```text
Web: https://hangout.example.com
```

Source: `public_site_url()` / `web_link_footer()` in `app/messages.py` (trailing
slash stripped). Empty, relative, or malformed base URLs omit the line entirely
(and log a warning) — no broken `http://` stubs. SMS simulator and
`preview-invite` use the same craft helpers, so previews match real sends.

This is the **canonical app root**, not a hangout-specific guest link. If the
deployment sits behind Cloudflare Access (or similar), invitees who open the
URL still need Access approval or a future public guest RSVP path; the footer
remains useful for organizers and Access-approved users.

## Inbound webhook

`POST /webhooks/sms`

- Accepts Twilio form fields or JSON (`From`/`from`, `Body`/`body`)
- When `SMS_PROVIDER=twilio`, validates `X-Twilio-Signature` for **both** form and JSON requests (403 on failure). The signature is verified against the canonical public URL `PUBLIC_BASE_URL + /webhooks/sms` (reverse-proxy aware), so `PUBLIC_BASE_URL` must match the URL configured in the Twilio console. JSON payloads are verified via the `bodySHA256` query parameter Twilio appends to the webhook URL; a signed JSON request **without** `bodySHA256` is rejected with 403 rather than passed to the validator, which cannot verify a raw body without it.
- With `SMS_PROVIDER=mock` (local testing), signatures are skipped and JSON is accepted as-is
- When Clerk is enabled, mock-provider webhook requests still require a Clerk session; the Twilio webhook is the public integration exception because Twilio cannot authenticate with a browser session.
- Missing From → 400
- Response is always TwiML `<Response><Message>…</Message></Response>` with the auto-reply text

## Reply parsing

`parse_reply_intent` lowercases the body and looks at the first token (and the second for MORE INFO):

| Intent | Keywords |
|--------|----------|
| info2 | `more info`, `moreinfo`, legacy `info 2` / `info2` / `info two`, or `info` + `2`/`two`/`full`/`list`/`details` |
| info | `info` (alone) |
| confirm | `confirm`, `yes`, `y`, `in`, `attending`, `coming` |
| decline | `no`, `n`, `decline`, `can't`, `cant`, `out`, `nope`, plus permanent opt-out keywords (below) |
| opt_in | `start`, `unstop`, `yesstart`, `opt in` / `optin`, `start forever` |
| none | anything else → help reply listing CONFIRM / NO / INFO / MORE INFO |

INFO and MORE INFO are **read-only**: they never change invite status.

Hangout-only decline (`NO`, etc.) affects that invite only. **Permanent opt-out**
is separate (global DNC).

### Permanent opt-out (do-not-contact)

Keywords / phrases (case-insensitive): carrier STOP set
`stop`, `stopall`, `unsubscribe`, `cancel`, `end`, `quit`, plus explicit
`stop forever` / `stopforever` / `stop all`.

On any of these:

1. Upsert a global `sms_opt_outs` row for the normalized phone (`source=keyword`)
2. Decline the matched active invite when one exists (same as today)
3. **No auto-reply** — `process_inbound_sms` returns `""`; webhook is empty
   `<Response></Response>`. Carrier/Twilio already confirm STOP; an app reply
   would bounce as Twilio 21610

The DNC list is **global** (not workspace-scoped): one STOP protects the person
on every tenant of this deployment. Survives restarts and new hangouts.

`send_sms` refuses every outbound to a DNC number (invites, follow-ups,
organizer digests/alerts). Setup / follow-up mark those invites `declined` so
they are not retried. UI: invitee picker and hangout status show
**won't be texted** for DNC phones. Platform admins manage the list at
`/admin/opt-outs` (manual add/remove, `source=admin`).

### Re-opt-in

`START` / `UNSTOP` (and aliases above) clear the DNC row and get a short
confirmation reply. Hangout-only declines are **not** cleared by START — only
the permanent list is.

## Inbound processing

1. Log inbound message
2. Permanent opt-out → record DNC; opt-in → clear DNC and return confirmation
3. Find the invite on an **active** hangout for the normalized phone, ranked by most recent `last_outbound_at` (never-messaged invites last, then newest invite id) so a reply lands on the hangout that actually texted them (fallback: last-10-digit match)
4. No invite → unmatched thanks message (opt-out still suppresses the reply)
5. `info` / `info2` return headcount or guest-list text without changing status
6. confirm / decline update `status` + `responded_at`
   (an opt-out body still records the decline, then suppresses the reply)
7. Run organizer threshold evaluation for RSVP intents (see [organizer-notifications.md](./organizer-notifications.md))
