from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from app.models import Drive, Hangout, HangoutInvite, InviteStatus, Profile, YesNo


def _yn(value: YesNo | None) -> str | None:
    if value == YesNo.yes:
        return "yes"
    if value == YesNo.no:
        return "no"
    return None


def format_day_date_for_sms(value: str | None) -> str | None:
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


def format_time_for_sms(value: str | None) -> str | None:
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


def format_hangout_summary(hangout: Hangout) -> str:
    """Multi-line hangout details for SMS. Falls back to a short phrase if empty."""
    lines: list[str] = []
    if hangout.motive:
        lines.append(hangout.motive)

    when_parts: list[str] = []
    day = format_day_date_for_sms(hangout.day_date)
    if day:
        when_parts.append(day)
    time = format_time_for_sms(hangout.time)
    if time:
        when_parts.append(f"at {time}")
    if hangout.duration:
        when_parts.append(f"({hangout.duration})")
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
    return "\n".join(
        [
            f"Hey {profile.name}!",
            "",
            "You're invited:",
            summary,
            "",
            _reply_options_footer(),
        ]
    )


def craft_followup_message(hangout: Hangout, profile: Profile, attempt: int) -> str:
    summary = format_hangout_summary(hangout)
    return "\n".join(
        [
            f"Hey {profile.name}, reminder ({attempt}):",
            "",
            "Still free for:",
            summary,
            "",
            _reply_options_footer(),
        ]
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
    return "\n".join(lines)


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
    return "\n".join(lines)


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
    return "\n".join(lines)


def craft_confirm_reply(hangout: Hangout) -> str:
    return "\n".join(
        [
            f"You're confirmed for hangout #{hangout.id}.",
            "See you!",
            "",
            "Reply INFO or MORE INFO.",
        ]
    )


def craft_decline_reply(hangout: Hangout) -> str:
    return "\n".join(
        [
            f"You're marked as not coming for hangout #{hangout.id}.",
            "Thanks for letting us know.",
        ]
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


def _sample_preview_scene() -> tuple[Any, Any, Any]:
    """Sample hangout + invitee for the SMS simulator catalog."""
    alex = SimpleNamespace(
        name="Alex Rivera",
        phone="+15551110001",
        food_allergies_label="pork",
        drive=Drive.no,
    )
    sam = SimpleNamespace(
        name="Sam Chen",
        phone="+15551110002",
        food_allergies_label=None,
        drive=Drive.yes,
    )
    jo = SimpleNamespace(
        name="Jo Patel",
        phone="+15551110003",
        food_allergies_label=None,
        drive=None,
    )
    hangout = hangout_from_fields(
        hangout_id=7,
        day_date="2026-08-15",
        time="19:00",
        duration="3",
        location="Sam's place",
        motive="Game night",
        alcohol_involved="yes",
        weed_involved=None,
        notes="Bring snacks if you can",
    )
    invites = [
        SimpleNamespace(status=InviteStatus.confirmed, profile=alex),
        SimpleNamespace(status=InviteStatus.pending, profile=sam),
        SimpleNamespace(status=InviteStatus.declined, profile=jo),
    ]
    hangout.invites = invites
    requester = SimpleNamespace(status=InviteStatus.pending, profile=sam)
    return hangout, sam, requester


def preview_message_catalog() -> list[dict[str, str]]:
    """Labeled sample SMS bodies for the settings simulator page."""
    hangout, profile, invite = _sample_preview_scene()
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
            "description": "Counts only — no names.",
            "body": craft_info_summary(hangout, invite),  # type: ignore[arg-type]
        },
        {
            "key": "info2",
            "title": "MORE INFO",
            "description": "Named lists plus logistics for confirmed guests.",
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
OPT_OUT_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}


def is_opt_out(body: str) -> bool:
    """True when an inbound SMS is a carrier opt-out keyword."""
    text = (body or "").strip().lower()
    token = text.replace(",", " ").split()[0] if text.split() else ""
    return token in OPT_OUT_WORDS or text in OPT_OUT_WORDS


def parse_reply_intent(body: str) -> str | None:
    """Map free-text SMS to confirm | decline | info | info2 | None."""
    text = (body or "").strip().lower()
    tokens = text.replace(",", " ").replace("-", " ").split()
    token = tokens[0] if tokens else ""

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
    if token in decline_words or text in decline_words:
        return "decline"
    return None
