from __future__ import annotations

import logging
import math
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.models import Hangout, HangoutInvite, InviteStatus, Profile, YesNo

logger = logging.getLogger(__name__)


def _yn(value: YesNo | None) -> str | None:
    if value == YesNo.yes:
        return "yes"
    if value == YesNo.no:
        return "no"
    return None


def format_day_date(value: str | None) -> str | None:
    """ISO-ish dates → long form (e.g. August 8, 2026). Unknown strings pass through."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        except ValueError:
            continue
    return raw


def format_time(value: str | None) -> str | None:
    """24h / ISO times → 12-hour (e.g. 7:00 PM). Unknown strings pass through."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p"):
        try:
            dt = datetime.strptime(raw, fmt)
            hour12 = dt.hour % 12 or 12
            ampm = "AM" if dt.hour < 12 else "PM"
            return f"{hour12}:{dt.minute:02d} {ampm}"
        except ValueError:
            continue
    return raw


def format_duration(value: str | None) -> str | None:
    """Bare numeric durations → hours label (e.g. 3 → 3 hours). Other text passes through."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        hours = float(raw)
    except ValueError:
        return raw
    # Reject nan/inf — float() accepts them but int() raises.
    if not math.isfinite(hours):
        return raw
    if hours == int(hours):
        number = str(int(hours))
        whole = int(hours)
    else:
        number = str(hours)
        whole = None
    unit = "hour" if whole == 1 else "hours"
    return f"{number} {unit}"


# Back-compat aliases (SMS was the first consumer).
format_day_date_for_sms = format_day_date
format_time_for_sms = format_time


def _reply_options_footer(*, include_info: bool = True) -> str:
    lines = [
        "Reply:",
        "CONFIRM",
        "NO",
    ]
    if include_info:
        lines.extend(
            [
                "INFO",
                "MORE INFO",
            ]
        )
    return "\n".join(lines)


def public_site_url() -> str | None:
    """Canonical site URL for SMS footers from PUBLIC_BASE_URL, or None if unusable.

    Strips a trailing slash. Omits empty values and anything that is not an
    absolute http(s) URL (so SMS never gets a bare `http://` or relative junk).
    """
    raw = (get_settings().public_base_url or "").strip().rstrip("/")
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        logger.warning(
            "PUBLIC_BASE_URL is not a usable absolute URL for SMS footers: %r",
            get_settings().public_base_url,
        )
        return None
    return raw


def web_link_footer() -> str | None:
    """One-line `Web: <url>` footer, or None when the base URL is missing/invalid."""
    url = public_site_url()
    return f"Web: {url}" if url else None


def _with_web_link(body: str) -> str:
    """Append the public site footer when configured; otherwise return *body* unchanged."""
    footer = web_link_footer()
    if not footer:
        return body
    return f"{body.rstrip()}\n\n{footer}"


def format_hangout_summary(hangout: Hangout) -> str:
    """Multi-line hangout details for SMS. Falls back to a short phrase if empty."""
    lines: list[str] = []
    if hangout.motive:
        lines.append(hangout.motive)

    when_parts: list[str] = []
    day = format_day_date(hangout.day_date)
    if day:
        when_parts.append(day)
    time = format_time(hangout.time)
    if time:
        when_parts.append(f"at {time}")
    duration = format_duration(hangout.duration)
    if duration:
        when_parts.append(f"({duration})")
    if when_parts:
        lines.append("When: " + " ".join(when_parts))

    if hangout.location:
        lines.append(f"Where: {hangout.location}")

    alcohol = _yn(hangout.alcohol_involved)
    if alcohol is not None:
        lines.append(f"Alcohol: {alcohol}")
    weed = _yn(hangout.weed_involved)
    if weed is not None:
        lines.append(f"Weed: {weed}")

    if hangout.notes:
        lines.append(f"Notes: {hangout.notes}")

    return "\n".join(lines).strip() or "a hangout"


def craft_invite_message(hangout: Hangout, profile: Profile) -> str:
    summary = format_hangout_summary(hangout)
    return _with_web_link(
        "\n".join(
            [
                f"Hey {profile.name}!",
                "",
                "You're invited:",
                summary,
                "",
                _reply_options_footer(),
            ]
        )
    )


def craft_followup_message(hangout: Hangout, profile: Profile, attempt: int) -> str:
    summary = format_hangout_summary(hangout)
    return _with_web_link(
        "\n".join(
            [
                f"Hey {profile.name}, reminder ({attempt}):",
                "",
                "Still free for:",
                summary,
                "",
                _reply_options_footer(),
            ]
        )
    )


def _invite_groups(hangout: Hangout) -> tuple[list[str], list[str], list[str]]:
    confirmed: list[str] = []
    pending: list[str] = []
    declined: list[str] = []
    for inv in hangout.invites:
        name = inv.profile.name
        if inv.status == InviteStatus.confirmed:
            confirmed.append(name)
        elif inv.status == InviteStatus.declined:
            declined.append(name)
        elif inv.status in (InviteStatus.pending, InviteStatus.remind, InviteStatus.no_response):
            pending.append(name)
    return confirmed, pending, declined


def _confirmed_logistics(hangout: Hangout) -> tuple[list[str], list[str], list[str]]:
    restrictions: list[str] = []
    rides_needed: list[str] = []
    can_drive: list[str] = []
    for inv in hangout.invites:
        if inv.status != InviteStatus.confirmed:
            continue
        name = inv.profile.name
        if inv.profile.food_allergies_label:
            restrictions.append(f"{name}: {inv.profile.food_allergies_label}")
        if inv.profile.drive is not None:
            if inv.profile.drive.value == "no":
                rides_needed.append(name)
            elif inv.profile.drive.value == "yes":
                can_drive.append(name)
    return restrictions, rides_needed, can_drive


def _status_label(status: InviteStatus) -> str:
    return {
        InviteStatus.confirmed: "confirmed",
        InviteStatus.declined: "not coming",
        InviteStatus.remind: "remind later",
        InviteStatus.pending: "pending",
        InviteStatus.no_response: "no response",
        InviteStatus.failed_send: "invite failed",
    }.get(status, status.value)


def craft_organizer_digest(hangout: Hangout) -> str:
    confirmed, pending, declined = _invite_groups(hangout)
    restrictions, rides_needed, can_drive = _confirmed_logistics(hangout)

    lines = [
        f"Hangout #{hangout.id} update",
        "",
        f"Coming ({len(confirmed)}): {', '.join(confirmed) or '—'}",
        f"Pending ({len(pending)}): {', '.join(pending) or '—'}",
        f"Declined ({len(declined)}): {', '.join(declined) or '—'}",
    ]
    if restrictions:
        lines.append("Restrictions: " + "; ".join(restrictions))
    if rides_needed:
        lines.append("Needs ride: " + ", ".join(rides_needed))
    if can_drive:
        lines.append("Can drive: " + ", ".join(can_drive))
    return _with_web_link("\n".join(lines))


def craft_info_summary(hangout: Hangout, invite: HangoutInvite) -> str:
    """INFO — headcounts only (no names)."""
    confirmed, pending, declined = _invite_groups(hangout)
    total = len(confirmed) + len(pending) + len(declined)
    lines = [
        f"Hangout #{hangout.id} headcount",
        "",
        f"Coming: {len(confirmed)}",
        f"Pending: {len(pending)}",
        f"Declined: {len(declined)}",
        f"Invited: {total}",
        "",
        f"Your RSVP: {_status_label(invite.status)}",
        "",
        "Reply MORE INFO.",
    ]
    return _with_web_link("\n".join(lines))


def craft_info_detail(hangout: Hangout, invite: HangoutInvite) -> str:
    """MORE INFO — names plus logistics for confirmed guests."""
    confirmed, pending, declined = _invite_groups(hangout)
    restrictions, rides_needed, can_drive = _confirmed_logistics(hangout)

    lines = [
        f"Hangout #{hangout.id} guest list",
        "",
        f"Coming ({len(confirmed)}): {', '.join(confirmed) or '—'}",
        f"Pending ({len(pending)}): {', '.join(pending) or '—'}",
        f"Declined ({len(declined)}): {', '.join(declined) or '—'}",
    ]
    if restrictions:
        lines.append("Restrictions: " + "; ".join(restrictions))
    if rides_needed:
        lines.append("Needs ride: " + ", ".join(rides_needed))
    if can_drive:
        lines.append("Can drive: " + ", ".join(can_drive))
    lines.extend(
        [
            "",
            f"Your RSVP: {_status_label(invite.status)}",
        ]
    )
    return _with_web_link("\n".join(lines))


def craft_confirm_reply(hangout: Hangout) -> str:
    return _with_web_link(
        "\n".join(
            [
                f"You're confirmed for hangout #{hangout.id}.",
                "See you!",
                "",
                "Reply INFO or MORE INFO.",
            ]
        )
    )


def craft_decline_reply(hangout: Hangout) -> str:
    return _with_web_link(
        "\n".join(
            [
                f"You're marked as not coming for hangout #{hangout.id}.",
                "Thanks for letting us know.",
            ]
        )
    )


def craft_help_reply() -> str:
    return _reply_options_footer()


def craft_unmatched_reply() -> str:
    return "Thanks! We couldn't match this number to an active hangout invite."


def hangout_from_fields(
    *,
    hangout_id: int = 0,
    day_date: str | None = None,
    time: str | None = None,
    duration: str | None = None,
    location: str | None = None,
    motive: str | None = None,
    alcohol_involved: str | None = None,
    weed_involved: str | None = None,
    notes: str | None = None,
) -> SimpleNamespace:
    """Build an in-memory hangout-like object for SMS previews (not persisted)."""

    def _opt_yn(value: str | None) -> YesNo | None:
        if not value or not str(value).strip():
            return None
        try:
            return YesNo(str(value).strip().lower())
        except ValueError:
            return None

    return SimpleNamespace(
        id=hangout_id,
        day_date=(day_date or "").strip() or None,
        time=(time or "").strip() or None,
        duration=(duration or "").strip() or None,
        location=(location or "").strip() or None,
        motive=(motive or "").strip() or None,
        alcohol_involved=_opt_yn(alcohol_involved),
        weed_involved=_opt_yn(weed_involved),
        notes=(notes or "").strip() or None,
        invites=[],
    )


def craft_invite_preview(
    *,
    recipient_name: str = "Alex",
    day_date: str | None = None,
    time: str | None = None,
    duration: str | None = None,
    location: str | None = None,
    motive: str | None = None,
    alcohol_involved: str | None = None,
    weed_involved: str | None = None,
    notes: str | None = None,
) -> str:
    hangout = hangout_from_fields(
        day_date=day_date,
        time=time,
        duration=duration,
        location=location,
        motive=motive,
        alcohol_involved=alcohol_involved,
        weed_involved=weed_involved,
        notes=notes,
    )
    profile = SimpleNamespace(name=(recipient_name or "").strip() or "Alex")
    return craft_invite_message(hangout, profile)  # type: ignore[arg-type]


# Simulated invite statuses for multi-person previews (not real RSVPs).
_PREVIEW_STATUS_CYCLE = (
    InviteStatus.pending,
    InviteStatus.confirmed,
    InviteStatus.declined,
)


def _preview_profile_from_name(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=(name or "").strip() or "Alex",
        phone="",
        food_allergies_label=None,
        drive=None,
    )


def _preview_profile_from_contact(profile: Any) -> SimpleNamespace:
    """Snapshot contact fields used by INFO / organizer logistics builders."""
    label = None
    if hasattr(profile, "food_allergies_label"):
        label = profile.food_allergies_label
    return SimpleNamespace(
        name=getattr(profile, "name", None) or "Guest",
        phone=getattr(profile, "phone", "") or "",
        food_allergies_label=label,
        drive=getattr(profile, "drive", None),
    )


def build_preview_scene(
    *,
    hangout_id: int = 7,
    day_date: str | None = None,
    time: str | None = None,
    duration: str | None = None,
    location: str | None = None,
    motive: str | None = None,
    alcohol_involved: str | None = None,
    weed_involved: str | None = None,
    notes: str | None = None,
    recipient_name: str = "Alex",
    contacts: list[Any] | None = None,
) -> tuple[Any, Any, Any]:
    """In-memory hangout + primary invitee + INFO requester for SMS previews.

    Selected contacts get synthetic statuses so group messages (INFO, digests)
    look realistic without persisting anything. With no contacts, invite copy
    uses ``recipient_name`` and headcounts stay empty.
    """
    hangout = hangout_from_fields(
        hangout_id=hangout_id,
        day_date=day_date,
        time=time,
        duration=duration,
        location=location,
        motive=motive,
        alcohol_involved=alcohol_involved,
        weed_involved=weed_involved,
        notes=notes,
    )
    if contacts:
        invite_profiles = [_preview_profile_from_contact(p) for p in contacts]
        invites = [
            SimpleNamespace(
                status=_PREVIEW_STATUS_CYCLE[i % len(_PREVIEW_STATUS_CYCLE)],
                profile=p,
            )
            for i, p in enumerate(invite_profiles)
        ]
        hangout.invites = invites
        primary = invite_profiles[0]
        requester = invites[0]
        return hangout, primary, requester

    primary = _preview_profile_from_name(recipient_name)
    requester = SimpleNamespace(status=InviteStatus.pending, profile=primary)
    hangout.invites = []
    return hangout, primary, requester


def preview_message_catalog(
    *,
    recipient_name: str = "Alex",
    day_date: str | None = None,
    time: str | None = None,
    duration: str | None = None,
    location: str | None = None,
    motive: str | None = None,
    alcohol_involved: str | None = None,
    weed_involved: str | None = None,
    notes: str | None = None,
    contacts: list[Any] | None = None,
) -> list[dict[str, str]]:
    """Labeled SMS bodies from the same craft_* builders as production.

    Used by the SMS simulator (SSR + live ``POST /api/sms/preview``). Does not
    send SMS or create hangouts.
    """
    hangout, profile, invite = build_preview_scene(
        day_date=day_date,
        time=time,
        duration=duration,
        location=location,
        motive=motive,
        alcohol_involved=alcohol_involved,
        weed_involved=weed_involved,
        notes=notes,
        recipient_name=recipient_name,
        contacts=contacts,
    )
    return [
        {
            "key": "invite",
            "title": "Invite (initial SMS)",
            "description": "Sent when the hangout is set up.",
            "body": craft_invite_message(hangout, profile),  # type: ignore[arg-type]
        },
        {
            "key": "followup",
            "title": "Follow-up reminder",
            "description": "Automatic nudge if they have not replied yet.",
            "body": craft_followup_message(hangout, profile, attempt=1),  # type: ignore[arg-type]
        },
        {
            "key": "confirm_ack",
            "title": "CONFIRM acknowledgement",
            "description": "Auto-reply after they confirm.",
            "body": craft_confirm_reply(hangout),  # type: ignore[arg-type]
        },
        {
            "key": "decline_ack",
            "title": "NO / decline acknowledgement",
            "description": "Auto-reply after they decline.",
            "body": craft_decline_reply(hangout),  # type: ignore[arg-type]
        },
        {
            "key": "info",
            "title": "INFO",
            "description": "Counts only — no names. Guest statuses are simulated.",
            "body": craft_info_summary(hangout, invite),  # type: ignore[arg-type]
        },
        {
            "key": "info2",
            "title": "MORE INFO",
            "description": "Named lists plus logistics for confirmed guests (simulated statuses).",
            "body": craft_info_detail(hangout, invite),  # type: ignore[arg-type]
        },
        {
            "key": "help",
            "title": "Help (unrecognized reply)",
            "description": "When the keyword is not recognized.",
            "body": craft_help_reply(),
        },
        {
            "key": "organizer",
            "title": "Organizer digest",
            "description": "Interval / threshold SMS to the organizer.",
            "body": craft_organizer_digest(hangout) + "\n(Event: new confirmation)",  # type: ignore[arg-type]
        },
        {
            "key": "unmatched",
            "title": "Unmatched number",
            "description": "Inbound from a phone with no active invite.",
            "body": craft_unmatched_reply(),
        },
    ]


# Twilio's carrier opt-out keywords. The number is blocked at the provider the
# moment one of these arrives, so they count as a decline (an answer, not a
# reason to keep retrying) and nothing may be sent back — see is_opt_out.
# All of these also write a permanent do-not-contact row (sms_opt_outs).
OPT_OUT_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
# Explicit permanent-opt-out phrases (first token is already "stop").
OPT_OUT_PHRASES = {"stop forever", "stopforever", "stop all"}

# Re-opt-in (clears sms_opt_outs). Carrier UNSTOP re-enables at Twilio; we
# mirror that for our own DNC list. Safe to auto-reply a short confirmation.
OPT_IN_WORDS = {"start", "unstop", "yesstart"}
OPT_IN_PHRASES = {"start forever", "opt in", "optin"}


def is_opt_out(body: str) -> bool:
    """True when an inbound SMS is a permanent opt-out / carrier STOP keyword."""
    text = (body or "").strip().lower()
    if not text:
        return False
    tokens = text.replace(",", " ").replace("-", " ").split()
    token = tokens[0] if tokens else ""
    if text in OPT_OUT_WORDS or token in OPT_OUT_WORDS:
        return True
    if text in OPT_OUT_PHRASES:
        return True
    if len(tokens) >= 2 and tokens[0] == "stop" and tokens[1] in {"forever", "all"}:
        return True
    return False


def is_opt_in(body: str) -> bool:
    """True when an inbound SMS requests re-opt-in (clear permanent DNC)."""
    text = (body or "").strip().lower()
    if not text:
        return False
    tokens = text.replace(",", " ").replace("-", " ").split()
    token = tokens[0] if tokens else ""
    if text in OPT_IN_WORDS or token in OPT_IN_WORDS:
        return True
    if text in OPT_IN_PHRASES:
        return True
    if len(tokens) >= 2 and tokens[0] == "opt" and tokens[1] == "in":
        return True
    if len(tokens) >= 2 and tokens[0] == "start" and tokens[1] == "forever":
        return True
    return False


def craft_opt_in_reply() -> str:
    """Short confirmation after START / UNSTOP clears the DNC list."""
    return "You're opted back in. You may receive hangout invites again."


def parse_reply_intent(body: str) -> str | None:
    """Map free-text SMS to confirm | decline | info | info2 | opt_in | None.

    Permanent opt-out bodies (STOP, STOP FOREVER, carrier keywords) map to
    decline so the matching invite stops retrying; process_inbound_sms also
    records a global DNC row and suppresses any auto-reply.
    """
    text = (body or "").strip().lower()
    tokens = text.replace(",", " ").replace("-", " ").split()
    token = tokens[0] if tokens else ""

    # Re-opt-in before confirm (START must not mean "confirm attendance").
    if is_opt_in(body):
        return "opt_in"

    # INFO / MORE INFO (multi-token before single-token confirm/decline sets)
    # Legacy INFO 2 / info2 still accepted.
    more_info_phrases = {
        "more info",
        "moreinfo",
        "info 2",
        "info two",
        "info2",
    }
    if (
        token in {"info", "info2", "moreinfo"}
        or text in more_info_phrases
        or (len(tokens) >= 2 and tokens[0] == "more" and tokens[1] == "info")
    ):
        if (
            token in {"info2", "moreinfo"}
            or text in more_info_phrases
            or (len(tokens) >= 2 and tokens[0] == "more" and tokens[1] == "info")
            or (
                len(tokens) >= 2
                and tokens[0] == "info"
                and tokens[1] in {"2", "two", "full", "list", "details"}
            )
        ):
            return "info2"
        return "info"

    confirm_words = {"confirm", "yes", "y", "in", "attending", "coming"}
    decline_words = {"no", "n", "decline", "can't", "cant", "out", "nope"} | OPT_OUT_WORDS
    if token in confirm_words or text in confirm_words:
        return "confirm"
    if is_opt_out(body) or token in decline_words or text in decline_words:
        return "decline"
    return None
