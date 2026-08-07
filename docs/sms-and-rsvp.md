# SMS and RSVP

Providers and phone normalization: `app/sms.py`. Message bodies and reply parsing: `app/messages.py`. Inbound HTTP: `app/routers/webhooks.py`. Orchestration: `process_inbound_sms` in `app/services.py`.

## Providers

| `SMS_PROVIDER` | Behavior |
|----------------|----------|
| `mock` (default) | Logs and prints to stdout; always succeeds |
| `twilio` | Twilio REST `messages.create`; requires account SID, auth token, from number |

App startup fails with a clear error when `SMS_PROVIDER=twilio` is set without all three Twilio credentials (config validated in `app/config.py`); `mock` requires none. Terraform also fails at plan time for the same misconfiguration (see [deploy.md](./deploy.md)).

`send_sms` always writes a `message_logs` row (success or error, including a
provider exception). It also performs a final destination check immediately
before provider lookup/send: the normalized number must have a leading `+` and
8–15 digits. Invalid destinations are rejected without invoking the SMS
provider, recorded as an unsuccessful message, and emitted as an
`sms.outbound.rejected` audit event.

## Phone normalization

`normalize_phone`: strips to digits and a leading `+`. Bare 10-digit numbers become `+1…`. Stored values stay E.164-ish for SMS.

## Phone display

`format_phone` (Jinja filter `|phone`, and `phone_format.js` on `input[type="tel"]`) shows the standard NANP form `+1 (XXX) XXX-XXXX`. Other country codes display as `+` plus digit groups. Storage and Twilio sends remain normalized; only UI input/display is pretty-printed.

## Outbound copy

- **Hangout summary** — motive; `on` date; `at` time; `(duration)`; `@` location; alcohol/weed only if set to yes/no; notes; fallback phrase `"a hangout"`
- **Invite** — summary + reply with **CONFIRM / REMIND / NO**
- **Follow-up** — `reminder ({attempt}): … Reply CONFIRM, REMIND, or NO.`
- **Immediate remind** (on REMIND reply) — `Reminder for {name}: … Reply CONFIRM if you're still in!`
- **Organizer digest** — Coming / Pending (`pending`+`remind`+`no_response`) / Declined; allergies for confirmed; Needs ride / Can drive for confirmed with `drive` no/yes

## Inbound webhook

`POST /webhooks/sms`

- Accepts Twilio form fields or JSON (`From`/`from`, `Body`/`body`)
- When `SMS_PROVIDER=twilio`, validates `X-Twilio-Signature` for **both** form and JSON requests (403 on failure). The signature is verified against the canonical public URL `PUBLIC_BASE_URL + /webhooks/sms` (reverse-proxy aware), so `PUBLIC_BASE_URL` must match the URL configured in the Twilio console. JSON payloads are verified via the `bodySHA256` query parameter Twilio appends to the webhook URL; a signed JSON request **without** `bodySHA256` is rejected with 403 rather than passed to the validator, which cannot verify a raw body without it.
- With `SMS_PROVIDER=mock` (local testing), signatures are skipped and JSON is accepted as-is
- Missing From → 400
- Response is always TwiML `<Response><Message>…</Message></Response>` with the auto-reply text

## Reply parsing

`parse_reply_intent` uses the first whitespace token (lowercased):

| Intent | Keywords |
|--------|----------|
| confirm | `confirm`, `yes`, `y`, `in`, `attending`, `coming` |
| remind | `remind`, `reminder`, `later` |
| decline | `no`, `n`, `decline`, `can't`, `cant`, `out`, `nope`, plus the carrier opt-out words `stop`, `stopall`, `unsubscribe`, `cancel`, `end`, `quit` |
| none | anything else → help reply listing CONFIRM / REMIND / NO |

Opt-out keywords count as a decline because Twilio blocks the number at the
provider; recording it as an answer stops the app retrying a number that can no
longer receive messages.

They also get **no auto-reply**: `process_inbound_sms` returns `""` for any
opt-out body (`is_opt_out` in `app/messages.py`), including from numbers with no
matching invite, and the webhook answers with a `<Response></Response>` carrying
no `<Message>`. The carrier and Twilio send their own opt-out confirmation, and
anything the app tried to send back would be rejected as Twilio error 21610.

## Inbound processing

1. Log inbound message
2. Find the invite on an **active** hangout for the normalized phone, ranked by most recent `last_outbound_at` (never-messaged invites last, then newest invite id) so a reply lands on the hangout that actually texted them (fallback: last-10-digit match)
3. No invite → unmatched thanks message
4. confirm / decline / remind update `status` + `responded_at`; remind also sends the immediate reminder SMS
   (an opt-out body still records the decline, then suppresses the reply)
5. Run organizer threshold evaluation (see [organizer-notifications.md](./organizer-notifications.md))
