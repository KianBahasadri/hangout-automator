# Functional Specification: Hangout Automator

**Document type:** Functional Specification (MVP)  
**Product:** Hangout Automator — a website that helps plan hangouts and invite people via SMS  
**Status:** Draft  
**Last updated:** 2026-08-05

---

## 1. Purpose

Hangout Automator is a simple web application that lets someone plan a hangout, invite people from a shared contact profile list, and collect RSVPs by text message. The system drafts and sends individual SMS invites, optionally follows up if people do not reply, and keeps the host informed—via optional SMS updates and a detailed status view on the site.

This document defines **what** the MVP must do from a user and system perspective. It does not prescribe implementation technology, messaging providers, or detailed UI mockups.

---

## 2. Product Summary (MVP)

| Capability | MVP scope |
|------------|-----------|
| Access | **No authentication.** Anyone who opens the website can use it; there is no login, signup, or account system. |
| Multi-tenancy | **None.** A single shared dataset. Every visitor sees and can act on the same profiles, hangouts, and status. |
| Hangout planning | Create a hangout with optional details (time, day, location, motive, alcohol, weed, duration, etc.) |
| Contacts | Maintain profiles (name + phone required; lifestyle/logistics fields optional) |
| Invites | Select existing profiles and send individual SMS invites |
| RSVP | Invitees reply by text (e.g. confirm / decline) |
| Follow-ups | Up to one or two follow-up texts if no response; then stop |
| Organizer updates | Full status always available by reopening the site; optional SMS digests/alerts only if an organizer profile (with phone) is selected |

**Explicit non-MVP (deferred):** authentication, user accounts, multi-tenant isolation, per-user private data. These will be addressed later; they are not part of this MVP.

---

## 3. Actors

| Actor | Description |
|-------|-------------|
| **Organizer (site user)** | Anyone using the website. No registration. Creates hangouts, manages the shared profile list, sends invites, and reviews status. Multiple people opening the site all interact with the **same** data. |
| **Invitee** | Person contacted only by SMS (no website account). Identified by a profile in the shared list. |
| **System** | Website backend and SMS pipeline that sends messages, records replies, and updates hangout state. |

---

## 4. User Journeys (MVP)

### 4.1 Organizer: set up a hangout

1. Open the website (no login).
2. Optionally add contact profiles (name + phone; optional attributes)—shared list visible to anyone with the URL.
3. Start a new hangout and fill in any desired hangout details (all fields optional).
4. Select one or more existing profiles to invite.
5. Confirm and **set up the hangout**.
6. System sends a personalized SMS to each selected invitee.
7. Optionally select who the organizer is (from the profile list) and enable SMS notifications (intervals and/or thresholds)—neither is required.
8. Later, open the website again to see who is coming, who is not, and related details (allergies, rides, etc.). Same data for every visitor.

### 4.2 Invitee: receive and reply

1. Receive an SMS invite describing the hangout (based on what was filled in on the site).
2. Reply with a keyword (or equivalent short instruction) to **confirm**, **decline**, or otherwise respond as defined in the invite text.
3. If no reply, may receive up to one or two follow-up SMS messages; after that, the system stops asking that invitee.

### 4.3 Organizer: stay updated

1. At any time, open the website and view the full hangout picture (no login, no phone number required).
2. Optionally, if an organizer profile is selected for notifications, receive SMS updates at configured intervals and/or when thresholds are met (e.g. first confirmation, enough drivers, dietary-restriction flags).

---

## 5. Functional Requirements

### 5.1 Access model (no auth, no multi-tenancy)

| ID | Requirement | Priority |
|----|-------------|----------|
| ACCESS-1 | The website requires **no authentication**. There is no login, signup, password, session-as-identity, or account concept in the MVP. | Must |
| ACCESS-2 | Anyone who can open the website has full access to the same features and the same data (profiles, hangouts, status). | Must |
| ACCESS-3 | There is **no multi-tenant** behavior: no per-user data partitions, no “my hangouts vs your hangouts,” no org/workspace isolation. One shared application state. | Must |
| ACCESS-4 | Authentication and multi-tenancy are **out of scope** for this MVP and will be designed later. | Must |

### 5.2 Organizer identity (optional)

The organizer **does not have to** identify themselves. The site and hangout flows work fully without it. Selecting an organizer profile (so SMS can use that profile’s phone) is only needed if optional organizer SMS updates are enabled (§5.7).

| ID | Requirement | Priority |
|----|-------------|----------|
| ORG-1 | When creating a hangout, the organizer **may** select who they are from the shared profile list (dropdown). This field is optional. | Must |
| ORG-2 | Creating hangouts, managing profiles, inviting people, and viewing hangout status must work without an organizer selection. | Must |
| ORG-3 | If organizer SMS notifications are enabled, an organizer profile with a phone must be selected first (or a settings fallback phone exists). Without a resolvable phone, notifications stay disabled. | Must |
| ORG-4 | Organizer SMS may also be used for other optional organizer-facing messages (e.g. hangout started confirmation)—product choice; not required for MVP core flows. | Should |

### 5.3 Contact profiles

Profiles live in a **single shared list** for the application. An invitee must exist as a profile before they can be selected for a hangout.

| ID | Requirement | Priority |
|----|-------------|----------|
| PROF-1 | A site user can create a profile with **required** fields: **name**, **phone number**. | Must |
| PROF-2 | A site user can edit and delete profiles. | Must |
| PROF-3 | Profiles can be listed/searched when selecting invitees. | Must |
| PROF-4 | A person can only be invited if they already exist as a profile (no ad-hoc phone entry at invite time without first creating a profile). | Must |
| PROF-5 | Optional profile attributes (all optional at create/edit): | Must |

**Optional profile attributes**

| Attribute | Description / allowed values |
|-----------|------------------------------|
| Drinks alcohol? | Yes / No (blank if unset) |
| Smokes? | Yes / No (blank if unset) |
| Dietary Restrictions | Zero or more from a shared list (managed in Settings; defaults include meat and pork) |
| Drive? | Yes / No / Maybe (blank if unset) |
| Tags | Zero or more labels (e.g. friends, coworkers) for bulk invite selection |

| ID | Requirement | Priority |
|----|-------------|----------|
| PROF-6 | Optional fields may be left blank; blank must not block invites. | Must |
| PROF-7 | Profiles are **shared application data** (not owned by individual accounts). Any visitor sees the same list. | Must |
| PROF-8 | Site users can create and delete shared tags and assign tags to profiles. | Must |
| PROF-8b | Site users can create and delete shared dietary-restriction options in Settings and assign them to profiles. Defaults include meat and pork. | Must |
| PROF-9 | When selecting invitees, the UI must support toggling all profiles with a given tag, and toggling groups by profile fields (e.g. drinks / does not drink, drives / needs ride, has dietary restrictions). | Should |

### 5.4 Hangout creation and details

Hangout details describe the plan. **All hangout detail fields are optional** so a minimal invite can still be sent.

| ID | Requirement | Priority |
|----|-------------|----------|
| HANG-1 | A site user can create a new hangout without logging in. | Must |
| HANG-2 | All hangout detail fields are optional. The system must allow creating and sending invites with sparse or empty detail sets. | Must |
| HANG-3 | Supported hangout detail fields (MVP set): | Must |

**Hangout detail fields (all optional)**

| Field | Description |
|-------|-------------|
| Day / date | When the hangout is planned |
| Time | Start time (and optionally end time if duration is not used alone) |
| Estimated duration / how long | Expected length |
| Location | Where the hangout takes place |
| Motive / purpose | Why people are hanging out (e.g. dinner, game night, beach) |
| Alcohol involved | Whether alcohol is expected or present |
| Weed involved | Whether weed is expected or present |
| Additional notes | Free-form notes for anything else to include |

| ID | Requirement | Priority |
|----|-------------|----------|
| HANG-4 | Hangout details can be edited before sending invites; behavior after invites are sent is defined in open questions / later design. | Must |
| HANG-5 | Invitees are selected from the shared profile list (multi-select). | Must |
| HANG-6 | An explicit **set up hangout** action (or equivalent) triggers outbound SMS invites. | Must |
| HANG-7 | System records hangout state (e.g. draft vs. active/sent) so anyone reopening the site can continue from shared state. | Must |

### 5.5 Invites and outbound SMS

| ID | Requirement | Priority |
|----|-------------|----------|
| SMS-1 | On hangout setup, the system crafts an SMS and sends it **individually** to each selected invitee’s phone number. | Must |
| SMS-2 | Message content should include available hangout details (date/time, location, motive, alcohol, weed, duration, etc.) and clear reply instructions. | Must |
| SMS-3 | Message instructs the invitee how to respond, including at least: **confirm**, **decline**, plus **info** (headcount) and **more info** (full guest list). Exact keywords and wording are implementation/copy decisions but must be documented in product copy. | Must |
| SMS-4 | Each invite is tracked per invitee (delivery/send status and response status). | Must |
| SMS-5 | Failed sends (invalid number, provider error) are visible on the hangout status view. | Should |

### 5.6 Invitee responses and follow-ups

| ID | Requirement | Priority |
|----|-------------|----------|
| RSVP-1 | System accepts inbound SMS replies and maps them to the correct hangout invitee (by phone number / active invite context). | Must |
| RSVP-2 | Recognized reply intents include: **confirm** (attending), **decline**, **info** (headcount), and **more info** (named guest list + logistics). Info intents are read-only. | Must |
| RSVP-3 | If an invitee does not respond, the system may send **one or two follow-up** SMS messages, then **stop** contacting that invitee for that hangout. | Must |
| RSVP-4 | Follow-up cadence (timing between messages) is configurable or set to sensible defaults; exact schedule is an implementation/product setting. | Should |
| RSVP-5 | Once the follow-up limit is reached with no response, invitee status remains **no response** (or equivalent) and no further automated asks are sent. | Must |
| RSVP-6 | Confirmed (and other recognized) statuses update the hangout record and are visible on the website status view. | Must |

### 5.7 Organizer notifications (optional)

Organizer SMS updates help the host stay current without constantly opening the site.

| ID | Requirement | Priority |
|----|-------------|----------|
| NOTIF-1 | SMS notifications for a hangout can optionally be enabled (hangout-level settings). | Must |
| NOTIF-2 | Notifications may be driven by **intervals**, **thresholds**, or **both**. | Must |
| NOTIF-3 | Interval digests are customizable per hangout: cadence (hours between digests) and whether to skip when RSVP/logistics are unchanged since the last SMS. | Must |
| NOTIF-4 | Threshold alerts are customizable per hangout: which events fire (new confirm, decline, dietary restriction on confirm, ride needed on confirm), an optional one-shot confirmed-count milestone, and an optional cooldown for routine confirm/decline alerts. Restriction, ride, and milestone alerts bypass cooldown. | Must |
| NOTIF-5 | Notification content should summarize useful logistics, such as: who is coming / not coming / no response; notable allergies; who needs a ride or can drive. | Should |
| NOTIF-6 | Notification preferences can be disabled or adjusted. | Must |
| NOTIF-7 | Organizer SMS is only available if an organizer profile (with phone) has been selected (§5.2), or a settings fallback phone exists. Missing phone must never block non-SMS use of the product. | Must |

### 5.8 Hangout status (website)

| ID | Requirement | Priority |
|----|-------------|----------|
| STAT-1 | Opening the website shows the **full picture** for hangouts: per-invitee status and relevant profile-derived logistics. No login required. | Must |
| STAT-2 | Status view shows at least: invited list; response state (confirmed / no response / declined if supported); send/follow-up history at a high level. | Must |
| STAT-3 | Status view surfaces logistics derived from confirmed (or all invited) profiles where available: dietary restrictions, driving capability, drink/smoke preferences if relevant to planning. | Should |
| STAT-4 | Status is the source of truth for detailed review; SMS notifications are optional summaries only. | Must |

### 5.9 Messaging boundaries (MVP assumptions)

| ID | Requirement | Priority |
|----|-------------|----------|
| PRIV-1 | Invitees do not need a website account or login. | Must |
| PRIV-2 | MVP data is **not private per user**: all site visitors share the same profiles and hangouts. Real isolation is deferred with auth/multi-tenancy. | Must |
| PRIV-3 | System only texts numbers that were explicitly added as profiles and selected for a hangout. | Must |
| PRIV-4 | Automated outbound volume to an invitee is limited (initial invite + at most two follow-ups). | Must |

---

## 6. Data Concepts (logical model)

Logical entities (not a database schema). **Single shared app state**—no `Account` or tenant root:

```
Application (single shared instance)
  ├── organizer_phone_number? (optional settings fallback — only needed for organizer SMS)
  ├── notification preferences (optional)
  ├── Profiles[]
  │     name, phone
  │     drinks?, smokes?, allergies[], drive?, tags[]
  └── Hangouts[]
        details (all optional): day/date, time, duration, location, motive, alcohol, weed, notes
        organizer? → Profile (optional — for organizer SMS)
        status: draft | active | closed (example)
        Invitees[]
          → Profile
          invite status: pending | confirmed | remind | no_response | failed_send | …
          outbound message history
        notification settings (interval cadence / skip-if-unchanged; threshold events / milestone / cooldown; optional)
```

---

## 7. Message Behavior (behavioral rules)

### 7.1 Outbound invite SMS

- Sent once per selected invitee when hangout is set up (unless resend is later specified).
- Body includes: hangout details that were provided; reply instructions (`confirm`, `no`, etc.).
- Individualized per number (no single group blast identity assumed).

### 7.2 Follow-ups

- Triggered only when invitee has not produced a recognized response.
- Maximum **two** follow-ups after the initial invite (product may choose one by default).
- After the cap, stop automated outreach for that invitee/hangout.

### 7.3 Inbound parsing

- System matches reply keywords (case-insensitive, trimmed) to intents.
- Unrecognized replies may be ignored, marked for review on the site, or trigger a short help SMS—behavior TBD; MVP minimum is reliable handling of confirm and decline.

### 7.4 Organizer SMS

- Off by default or explicitly opt-in (product choice; must be optional).
- Interval and/or threshold modes as configured.
- Does not replace the website status view.

---

## 8. Non-Goals (MVP)

The following are **out of scope** for the MVP unless explicitly added later:

- **Authentication / accounts / login** (deferred; not part of MVP)
- **Multi-tenancy / per-user private data** (deferred; not part of MVP)
- Invitee mobile app or invitee web accounts
- Shared calendars, maps, or automatic venue booking
- Multi-organizer *account* collaboration models (MVP is simply open shared access)
- Public/social discovery of hangouts outside this single app instance
- Payments, tickets, or group expense splitting
- Voice calls or email as primary invite channel
- AI-generated hangout ideas (unless later specified)
- Real-time chat between invitees

---

## 9. Success Criteria (MVP)

MVP is successful when someone can:

1. Open the website with **no login** and use all features against the **same shared data** as any other visitor.
2. Add contact profiles with name, phone, and optional attributes.
3. Create a hangout with any combination of optional details (including none).
4. Select profiles and launch the hangout so each invitee receives an individual SMS with reply instructions.
5. Have non-responders receive at most one–two follow-ups, then no more automated asks.
6. Optionally provide a phone number and receive organizer SMS updates at intervals and/or thresholds.
7. Reopen the website and see a clear, detailed status of who is coming and relevant logistics (allergies, rides, etc.).

---

## 10. Open Questions

These items need product decisions before or during implementation:

1. **Exact RSVP keyword set** — confirm / decline only, or also maybe / stop?
2. **Follow-up timing** — when are automatic unresponded nudges sent?
3. **Editing after send** — can hangout details change, and do invitees get an update SMS?
4. **Default follow-up timing** — e.g. +24h and +48h.
5. **Threshold examples for organizer SMS** — ~~which events fire by default?~~ Defaults: new confirm, dietary restriction, ride needed; decline off; milestone off; no cooldown. Interval default 6h with skip-if-unchanged.
6. **Timezone handling** for hangout day/time and scheduled messages.
7. **Regulatory/compliance** — SMS consent language, STOP handling, region (e.g. US A2P). Open site + SMS has obvious abuse risk; acceptable for private MVP only?
8. **Duplicate profiles** — prevent same phone twice in the shared list?
9. **Hangout close** — when does outreach and notifications stop (manual close, past event time)?
10. **Post-MVP** — how auth and multi-tenancy will map onto this shared-data MVP (not required now).

---

## 11. Glossary

| Term | Meaning |
|------|---------|
| **Hangout** | A planned social event managed on the site. |
| **Profile** | Shared contact record (name, phone, optional attributes). |
| **Invitee** | A profile selected for a specific hangout and messaged by SMS. |
| **Organizer / site user** | Anyone using the website; no account identity in MVP. |
| **Confirm** | Invitee reply intent meaning they plan to attend. |
| **Follow-up** | Automatic nudge SMS to invitees who have not yet confirmed or declined. |
| **Follow-up** | Automated re-prompt SMS when an invitee has not responded. |
| **Organizer notification** | Optional SMS summarizing hangout progress to a configured number. |
| **Shared instance** | Single app dataset; no per-user isolation in MVP. |

---

## 12. Document History

| Date | Change |
|------|--------|
| 2026-08-05 | Initial functional specification from product description (MVP). |
| 2026-08-05 | Organizer phone number is fully optional (not required for core flows). |
| 2026-08-05 | Removed auth and multi-tenancy from MVP: open access, single shared dataset. |
| 2026-08-05 | Implementation: FastAPI + SQLite MVP; Azure B1s VM via Terraform. |
