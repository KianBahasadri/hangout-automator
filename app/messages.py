from __future__ import annotations

from app.models import Hangout, InviteStatus, Profile, YesNo


def _yn(value: YesNo | None) -> str | None:
    if value == YesNo.yes:
        return "yes"
    if value == YesNo.no:
        return "no"
    return None


def format_hangout_summary(hangout: Hangout) -> str:
    parts: list[str] = []
    if hangout.motive:
        parts.append(hangout.motive)
    if hangout.day_date:
        parts.append(f"on {hangout.day_date}")
    if hangout.time:
        parts.append(f"at {hangout.time}")
    if hangout.duration:
        parts.append(f"({hangout.duration})")
    alcohol = _yn(hangout.alcohol_involved)
    if alcohol == "yes":
        parts.append("alcohol: yes")
    elif alcohol == "no":
        parts.append("alcohol: no")
    weed = _yn(hangout.weed_involved)
    if weed == "yes":
        parts.append("weed: yes")
    elif weed == "no":
        parts.append("weed: no")
    if hangout.notes:
        parts.append(hangout.notes)
    return " ".join(parts).strip() or "a hangout"


def craft_invite_message(hangout: Hangout, profile: Profile) -> str:
    summary = format_hangout_summary(hangout)
    return (
        f"Hey {profile.name}! You're invited to {summary}. "
        f'Reply CONFIRM to confirm, REMIND for a later reminder, or NO to decline.'
    )


def craft_followup_message(hangout: Hangout, profile: Profile, attempt: int) -> str:
    summary = format_hangout_summary(hangout)
    return (
        f"Hey {profile.name}, reminder ({attempt}): still free for {summary}? "
        f"Reply CONFIRM, REMIND, or NO."
    )


def craft_reminder_message(hangout: Hangout, profile: Profile) -> str:
    summary = format_hangout_summary(hangout)
    return f"Reminder for {profile.name}: {summary}. Reply CONFIRM if you're still in!"


def craft_organizer_digest(hangout: Hangout) -> str:
    confirmed: list[str] = []
    pending: list[str] = []
    declined: list[str] = []
    allergies: list[str] = []
    rides_needed: list[str] = []
    can_drive: list[str] = []

    for inv in hangout.invites:
        name = inv.profile.name
        if inv.status == InviteStatus.confirmed:
            confirmed.append(name)
        elif inv.status == InviteStatus.declined:
            declined.append(name)
        elif inv.status in (InviteStatus.pending, InviteStatus.remind, InviteStatus.no_response):
            pending.append(name)

        if inv.profile.food_allergies_label and inv.status == InviteStatus.confirmed:
            allergies.append(f"{name}: {inv.profile.food_allergies_label}")
        if inv.status == InviteStatus.confirmed and inv.profile.drive is not None:
            if inv.profile.drive.value == "no":
                rides_needed.append(name)
            elif inv.profile.drive.value == "yes":
                can_drive.append(name)

    lines = [
        f"Hangout #{hangout.id} update:",
        f"Coming ({len(confirmed)}): {', '.join(confirmed) or '—'}",
        f"Pending ({len(pending)}): {', '.join(pending) or '—'}",
        f"Declined ({len(declined)}): {', '.join(declined) or '—'}",
    ]
    if allergies:
        lines.append("Allergies: " + "; ".join(allergies))
    if rides_needed:
        lines.append("Needs ride: " + ", ".join(rides_needed))
    if can_drive:
        lines.append("Can drive: " + ", ".join(can_drive))
    return "\n".join(lines)


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
    """Map free-text SMS to confirm | remind | decline | None."""
    text = (body or "").strip().lower()
    # Take first token / first line
    token = text.replace(",", " ").split()[0] if text.split() else ""
    confirm_words = {"confirm", "yes", "y", "in", "attending", "coming"}
    remind_words = {"remind", "reminder", "later"}
    decline_words = {"no", "n", "decline", "can't", "cant", "out", "nope"} | OPT_OUT_WORDS
    if token in confirm_words or text in confirm_words:
        return "confirm"
    if token in remind_words or text in remind_words:
        return "remind"
    if token in decline_words or text in decline_words:
        return "decline"
    return None
