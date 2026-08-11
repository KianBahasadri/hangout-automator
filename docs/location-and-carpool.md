# Location and carpool (product model)

**Owns:** product decisions for epic [KIAN-498](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-498/location-data-and-ridesharecarpool-planning) (hangout **location** + rideshare/carpool planning).

This is the **requirements / cut-line** document for that epic. There is no
separate `functional-specification.md`. After children ship, *implementation*
facts live only in the usual topic files (`data-model`, `web-ui`, `api`,
`sms-and-rsvp`, `organizer-notifications`); do not duplicate those here.

**Status:** locked for v1 (KIAN-499), with product amendments for location-as-entity
and carpool auto-match (2026-08-11). Re-open only with an explicit product
change, not during schema bikeshedding.

---

## Baseline today (do not re-invent)

| Area | Current behavior |
|------|------------------|
| Hangout location | Single free-text `hangouts.location` (255). Create form may use Places autocomplete when `GOOGLE_MAPS_API_KEY` is set; selection still **only stores** the text (details API already returns `formattedAddress` + lat/lng but they are discarded). |
| SMS / UI | `Where:` and list/detail lines use that string. |
| Transport | Contact (`profiles`) `drive` = `yes` / `no` / `maybe` / unset. Confirmed + `drive=no` → optional organizer ride-needed threshold. MORE INFO / digests list “needs ride” / “can drive” from the **profile** flag. |
| Per-invite transport | None. No seats, origins, or carpool assignments. |

---

## Product outcomes (epic)

1. **Know where** — a hangout has a real **location** (map-friendly when possible), not only a nickname string.
2. **Plan how people get there** — per-hangout ride need/offer (and seats), carpool board with **manual + auto** matching, logistics in organizer SMS.
3. **Optional maps handoff** — deep links when address and/or coordinates exist; no paid rideshare dispatch APIs.

---

## Locked decisions

Priorities use **Must** / **Should** / **Could** for v1 unless marked **Later**.

### 1. Hangout location model

**Decision: the hangout field is a Location, not “just a free-text label.”**

Product language and UI should say **Location**. Internally it is a small
structured value (logical `HangoutLocation`) whose primary job is “where is
this hangout?” — suitable for maps, carpool distance, and SMS `Where:`.

| Priority | Requirement |
|----------|-------------|
| **Must** | Model hangout place as a **location**: at minimum a human **label/display string** plus optional structured fields (`place_id`, `latitude`, `longitude`). Empty location remains allowed (draft / TBD). |
| **Must** | Prefer entering location via **Places autocomplete** when `GOOGLE_MAPS_API_KEY` is set so the hangout gets a real place (formatted address + coords), not only a typed nickname. |
| **Must** | When the organizer picks a Places suggestion, **persist** label/address text, Google `place_id`, and `lat`/`lng` from Place Details when details succeed. |
| **Must** | Without a key (or details failure), allow a **text-only location** (label with no coords / place id) so create hangout never hard-blocks. That is a degraded location, not a separate product mode. |
| **Should** | UI copy and API field names lean **location** (not “place name only”); list/detail headers and SMS `Where:` use the location’s display string. |
| **Should** | Maps deep links (see §7) are first-class uses of location coords/address, not afterthoughts. |
| **Could** | Separate short **nickname** vs full **address** on the same location (e.g. “Kian’s place” + street). |
| **Later** | Full address component columns; reverse-geocode pure text into a full location on save. |

**SMS:** `Where:` uses the location **display string only**. Never put lat/lng or place ids in SMS.

**Migration:** Existing `hangouts.location` text becomes the location display string; structured fields null until re-picked or geocoded later.

### 2. Geocoding

**Decision: server-side Google Places (already in app) is the primary way to resolve a full location for v1.**

| Priority | Requirement |
|----------|-------------|
| **Must** | Keep `GET /api/places/autocomplete` + `GET /api/places/details` as the path to a **full location**; API key stays server-side only. |
| **Must** | Without a key (or on details failure), location entry degrades to text-only — not an error. |
| **Must not** | Require manual lat/lng paste for v1. |
| **Must not** | Geocode in the browser with a client Maps key. |
| **Later** | Reverse-geocode free text on save; non-Google providers; bulk backfill of old hangouts. |

### 3. Privacy (guest origins / home)

**Decision: no full home addresses on contacts for v1; optional per-invite “coming from” free text only.**

| Priority | Requirement |
|----------|-------------|
| **Must not** | Store guest home street addresses on the contact (`profiles`) record in v1. |
| **Must** | If origin is captured for matching, it is **per invite** (this hangout), free-text, optional (neighborhood / area / “Uptown” is fine). |
| **Must** | Organizer-facing UI may show invite origins on the carpool board. |
| **Must not** | Put other guests’ origins in **MORE INFO** (or any group SMS that goes to invitees). Origins are organizer logistics, not a broadcast. |
| **Should** | Treat origin as soft privacy: workspace members who can open the hangout can see it (same trust boundary as the rest of hangout detail). |
| **Should** | Auto-match may use origin text loosely (same free-text / area); do not require geocoded guest homes for v1 auto-match. |
| **Later** | Profile-level “usual area”; precise home for the account holder only; guest self-serve origin via public RSVP link; geocoded origins for better auto-match. |

### 4. Transport ownership

**Decision: per-invite transport is source of truth for a hangout; contact `drive` is a default only.**

| Priority | Requirement |
|----------|-------------|
| **Must** | Model transport **on the invite** (or a 1:1 invite extension): at least **need ride** / **can drive (offer)** / **self-arranged or N/A** / **unset**, plus **seat count** when offering. |
| **Must** | Contact `drive` (`yes`/`no`/`maybe`) remains as a **directory default**. On invite create (and when transport is still unset), prefill invite transport from that default (`no` → need, `yes` → offer with default seats, `maybe`/unset → unset or soft maybe per implementation). |
| **Must** | Organizer (and web flows that edit the hangout) can override per invite without changing the contact’s default. |
| **Must** | Organizer ride-needed alerts and MORE INFO / digest logistics use **invite** transport for confirmed guests, not only `profiles.drive`. |
| **Should** | Default seats when offering: small integer default (e.g. **3** free seats) editable on the board; clamp to a sane max (e.g. 1–8). |
| **Later** | Guests editing their own transport via authenticated guest UI. |

### 5. Carpool matching

**Decision: v1 supports both manual assignment and an auto-matching mode for convenience.**

Organizers should not have to wire every rider by hand when a simple greedy
assignment is “good enough.” Auto-match is a **mode / action**, not a black box
that forbids edits.

| Priority | Requirement |
|----------|-------------|
| **Must** | Hangout detail **carpool board**: needs ride / drivers with seats / self-arranged or unset. |
| **Must** | Organizer can **manually assign** rider(s) to a driver (stored assignment); warn when assigned riders exceed free seats. |
| **Must** | Provide **auto-match** (button and/or hangout toggle): fill unassigned “need ride” guests onto drivers who still have free seats. |
| **Must** | Auto-match is **convenience only**: organizer can always undo, re-run, or override any assignment afterward. Re-running should only touch **unassigned** riders (or make “replace auto assignments” an explicit choice so manual pins are safe). |
| **Must** | Seat accounting applies to auto and manual the same way (never silently overfill seats). |
| **Should** | Prefer simple, explainable rules for v1 auto-match, e.g.: confirmed guests first; fill drivers with the most remaining seats; optional weak preference for similar `origin_text` when both sides have it; leave self-arranged / unset alone. |
| **Should** | Show that a pairing came from **auto** vs **manual** (light badge or assignment source) so organizers trust and edit the board. |
| **Must not** | Guest self-serve “claim a seat” in v1 (no public guest hangout UI yet). |
| **Must not** | Require live traffic, road distance APIs, or paid routing for v1 auto-match. |
| **Should** | Closed hangouts show the board read-only (no re-match). |
| **Later** | Optimal global matching; multi-leg / meeting-point graph; guest claims; distance-matrix based scoring when origins are geocoded. |

### 6. SMS surface area

**Decision: v1 is read logistics in SMS; write path stays web.**

| Priority | Requirement |
|----------|-------------|
| **Must** | MORE INFO + organizer digests reflect invite transport + assignments (driver → named riders when assigned), whether pairings were manual or auto. |
| **Must** | Threshold “ride needed” fires when a guest **confirms** and invite transport is **need ride** (not only legacy profile `drive=no`). |
| **Must not** | Expand the invite reply menu with RIDE / DRIVE keywords in v1 (keep CONFIRM / NO / INFO / MORE INFO). |
| **Should** | Keep SMS length reasonable: summaries stay headcount-first; detailed carpool lines only in MORE INFO / digests. |
| **Could / Later** | Inbound keywords (e.g. `RIDE`, `DRIVE 3`) once product wants SMS write-back. |

### 7. Maps / rideshare deep links

**Decision: URL templates only; no booking APIs.**

| Priority | Requirement |
|----------|-------------|
| **Must** | When lat/lng **or** a non-empty location display string exists, hangout detail can offer **Open in Google Maps** and **Open in Apple Maps** (safe encoded query or `ll` style links). |
| **Should** | Optional Uber/Lyft universal links when coords exist — same “open app” pattern, no server-side booking. |
| **Must not** | Paid rideshare dispatch, live ETA, or traffic APIs in this epic. |
| **Should** | Prefer **not** stuffing long maps URLs into every invite SMS; link on web detail is enough. MORE INFO may omit maps links unless length is clearly fine. |
| **Must** | No API keys or secrets in URLs or SMS. |

---

## Entities (logical — schema in later issues)

Exact column names belong in `data-model.md` when implemented. Logical model for implementers:

```text
HangoutLocation (on hangout)          # product: "Location"
  display_text          # SMS/list/detail Where
  place_id?             # Google Places id when chosen via autocomplete
  latitude? longitude?  # from Place Details
  # maps URLs: prefer compute-at-read unless product later stores one

InviteTransport (on hangout_invite)
  mode                  # need | offer | self | unset  (names flexible)
  seats_free?           # when offer
  origin_text?          # optional free-text "coming from" (v1 privacy rule)

CarpoolAssignment
  hangout_id
  driver_invite_id
  rider_invite_id
  source?               # manual | auto  (for board badge / re-run safety)
  # uniqueness: one primary driver per rider per hangout (v1)

HangoutCarpoolSettings (optional columns on hangout)
  auto_match_enabled?   # if product uses a sticky mode vs one-shot button
```

Contact `drive` stays; it does **not** replace `InviteTransport`.

---

## v1 vs later cut line

### In scope for this epic (v1)

| Child | Delivers |
|-------|----------|
| **KIAN-500** | Hangout **location** as structured fields (display + place_id/lat/lng); UI + SMS `Where:` from display string |
| **KIAN-501** | Per-invite transport + seats; prefill from contact `drive`; alerts/digest/MORE INFO switch to invite fields |
| **KIAN-502** | Optional per-invite origin free text (organizer-visible; not in guest MORE INFO; weak input to auto-match) |
| **KIAN-503** | Carpool board + **manual** assignments + **auto-match** action/mode + seat warnings |
| **KIAN-504** | SMS / organizer copy for transport + assignments (no new inbound keywords) |
| **KIAN-505** | Maps (and optional rideshare) deep-link helpers + hangout detail links from location |
| **KIAN-506** | Tests + patch implementation topic docs only |

### Explicitly later (not v1)

- Optimal / multi-leg matching, distance-matrix scoring, guest seat claims  
- SMS keywords to set ride/drive  
- Profile home address or geocoded usual location  
- Client-side geocoding; non-Places geocoders  
- Live traffic, ETAs, Uber/Lyft booking APIs  
- In-app turn-by-turn map product  
- Consumables epic ([KIAN-507](https://linear.app/kian-finishing-zombie-granola/issue/KIAN-507)) — separate  

---

## Acceptance checklist (product lock)

- [x] Location model: hangout has a **Location** (display + optional place_id/coords); text-only is degraded entry, not the product concept  
- [x] Geocoding: server Places primary path to a full location  
- [x] Privacy: no contact home addresses; per-invite origin optional; not in guest MORE INFO  
- [x] Transport: per-invite source of truth; contact `drive` = default  
- [x] Matching: **manual + auto-match** for convenience; organizer always overrides  
- [x] SMS: logistics readback; no new reply keywords in v1  
- [x] Deep links: URL templates only; detail UI primary surface  
- [x] v1 vs later cut line listed above  

Schema/UI PRs for KIAN-500+ should follow this doc; conflicts mean update **this** file first, then implement.
