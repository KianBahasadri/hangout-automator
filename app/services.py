from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.event_logging import audit_event
from app.messages import (
    craft_confirm_reply,
    craft_decline_reply,
    craft_followup_message,
    craft_help_reply,
    craft_info_detail,
    craft_info_summary,
    craft_invite_message,
    craft_organizer_digest,
    craft_remind_ack_reply,
    craft_reminder_message,
    craft_unmatched_reply,
    is_opt_out,
    parse_reply_intent,
)
from app.models import (
    Hangout,
    HangoutInvite,
    HangoutStatus,
    InviteStatus,
    MessageDirection,
    MessageLog,
    Profile,
)
from app.sms import get_sms_provider, is_valid_phone, normalize_phone

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_tag_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def normalize_allergy_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def load_tags_by_ids(db: Session, tag_ids: list[int] | None) -> list:
    from app.models import Tag

    if not tag_ids:
        return []
    ids = sorted({int(t) for t in tag_ids})
    return db.query(Tag).filter(Tag.id.in_(ids)).all()


def load_allergies_by_ids(db: Session, allergy_ids: list[int] | None) -> list:
    from app.models import Allergy

    if not allergy_ids:
        return []
    ids = sorted({int(a) for a in allergy_ids})
    return db.query(Allergy).filter(Allergy.id.in_(ids)).all()


def profile_has_allergies(profile: Profile) -> bool:
    return bool(getattr(profile, "has_allergies", False))


def profile_allergies_label(profile: Profile) -> str | None:
    return getattr(profile, "food_allergies_label", None)


def resolve_organizer_phone(db: Session, hangout: Hangout) -> str | None:
    """Phone for organizer SMS: selected profile, then legacy hangout phone."""
    if hangout.organizer_profile_id:
        profile = hangout.organizer if hangout.organizer is not None else db.get(Profile, hangout.organizer_profile_id)
        if profile and profile.phone:
            return profile.phone
    if hangout.organizer_phone:
        return hangout.organizer_phone
    return None


INTERVAL_HOUR_OPTIONS = (1, 2, 3, 4, 6, 8, 12, 24)
COOLDOWN_MINUTE_OPTIONS = (0, 5, 15, 30, 60)
CONFIRM_GOAL_OPTIONS = (0, 2, 3, 4, 5, 6, 8, 10)

# Total failed sends logged against one invite after which follow-ups stop.
FOLLOWUP_FAILURE_LIMIT = 3


def clamp_choice(value: int | str | None, allowed: tuple[int, ...], default: int) -> int:
    try:
        n = int(value) if value is not None and str(value).strip() != "" else default
    except (TypeError, ValueError):
        return default
    return n if n in allowed else default


def hangout_digest_fingerprint(hangout: Hangout) -> str:
    """Compact RSVP/logistics snapshot used to skip unchanged interval digests."""
    parts: list[str] = []
    for inv in sorted(hangout.invites, key=lambda i: i.profile_id):
        allergy = "1" if profile_has_allergies(inv.profile) else "0"
        drive = inv.profile.drive.value if inv.profile.drive is not None else ""
        parts.append(f"{inv.profile_id}:{inv.status.value}:{allergy}:{drive}")
    return "|".join(parts)


def confirmed_count(hangout: Hangout) -> int:
    return sum(1 for inv in hangout.invites if inv.status == InviteStatus.confirmed)


def threshold_cooldown_elapsed(hangout: Hangout, now: datetime | None = None) -> bool:
    minutes = hangout.notify_threshold_cooldown_minutes or 0
    if minutes <= 0:
        return True
    last = hangout.last_organizer_notify_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    when = now or utcnow()
    return when >= last + timedelta(minutes=minutes)


def maybe_send_organizer_threshold(
    db: Session,
    hangout: Hangout,
    *,
    reasons: list[str],
    high_priority: bool,
) -> bool:
    phone = resolve_organizer_phone(db, hangout)
    if not phone or not hangout.notify_enabled or not hangout.notify_threshold:
        return False
    if not reasons:
        return False
    if not high_priority and not threshold_cooldown_elapsed(hangout):
        logger.info(
            "Skipping threshold SMS for hangout %s (cooldown); reasons=%s",
            hangout.id,
            reasons,
        )
        return False
    body = craft_organizer_digest(hangout) + f"\n(Event: {', '.join(reasons)})"
    ok, _ = send_sms(db, to=phone, body=body, hangout_id=hangout.id)
    if ok:
        hangout.last_organizer_notify_at = utcnow()
        hangout.last_digest_fingerprint = hangout_digest_fingerprint(hangout)
        db.commit()
    return ok


def evaluate_organizer_threshold_for_reply(
    db: Session,
    hangout: Hangout,
    *,
    intent: str,
    prev_status: InviteStatus,
    profile: Profile,
) -> None:
    """Fire threshold alerts based on hangout preferences after an invitee reply."""
    if not hangout.notify_enabled or not hangout.notify_threshold:
        return

    reasons: list[str] = []
    high_priority = False
    goal_hit = False

    if intent == "confirm" and prev_status != InviteStatus.confirmed:
        if hangout.notify_on_new_confirm:
            reasons.append("new confirmation")
        if hangout.notify_on_allergy and profile_has_allergies(profile):
            reasons.append("dietary restriction")
            high_priority = True
        if hangout.notify_on_ride_needed and profile.drive is not None and profile.drive.value == "no":
            reasons.append("ride needed")
            high_priority = True

        goal = hangout.notify_confirm_goal or 0
        if goal > 0 and not hangout.notify_confirm_goal_sent and confirmed_count(hangout) >= goal:
            reasons.append(f"confirmed goal reached ({goal})")
            goal_hit = True
            high_priority = True

    elif intent == "decline" and prev_status != InviteStatus.declined:
        if hangout.notify_on_decline:
            reasons.append("decline")

    if not reasons:
        return

    ok = maybe_send_organizer_threshold(
        db, hangout, reasons=reasons, high_priority=high_priority
    )
    if ok and goal_hit:
        hangout.notify_confirm_goal_sent = True
        db.commit()


def log_message(
    db: Session,
    *,
    phone: str,
    body: str,
    direction: MessageDirection,
    success: bool,
    error: str | None = None,
    invite_id: int | None = None,
    hangout_id: int | None = None,
) -> MessageLog:
    entry = MessageLog(
        phone=phone,
        body=body,
        direction=direction,
        success=success,
        error=error,
        invite_id=invite_id,
        hangout_id=hangout_id,
    )
    db.add(entry)
    return entry


def send_sms(
    db: Session,
    *,
    to: str,
    body: str,
    invite_id: int | None = None,
    hangout_id: int | None = None,
) -> tuple[bool, str | None]:
    phone = normalize_phone(to)
    if not is_valid_phone(phone):
        error = "Destination phone number is not usable"
        log_message(
            db,
            phone=phone,
            body=body,
            direction=MessageDirection.outbound,
            success=False,
            error=error,
            invite_id=invite_id,
            hangout_id=hangout_id,
        )
        audit_event(
            "sms.outbound.rejected",
            level=logging.WARNING,
            to=phone,
            body=body,
            reason="invalid_destination_phone",
            error=error,
            invite_id=invite_id,
            hangout_id=hangout_id,
        )
        return False, error

    provider = get_sms_provider()
    provider_name = type(provider).__name__
    audit_event(
        "sms.outbound.started",
        provider=provider_name,
        to=phone,
        body=body,
        invite_id=invite_id,
        hangout_id=hangout_id,
    )
    try:
        ok, err = provider.send(phone, body)
    except Exception as exc:
        error = str(exc)
        log_message(
            db,
            phone=phone,
            body=body,
            direction=MessageDirection.outbound,
            success=False,
            error=error,
            invite_id=invite_id,
            hangout_id=hangout_id,
        )
        audit_event(
            "sms.outbound.failed",
            level=logging.ERROR,
            exc_info=True,
            provider=provider_name,
            to=phone,
            body=body,
            invite_id=invite_id,
            hangout_id=hangout_id,
            exception_type=type(exc).__name__,
            exception_message=error,
        )
        raise
    log_message(
        db,
        phone=phone,
        body=body,
        direction=MessageDirection.outbound,
        success=ok,
        error=err,
        invite_id=invite_id,
        hangout_id=hangout_id,
    )
    audit_event(
        "sms.outbound.completed",
        provider=provider_name,
        to=phone,
        body=body,
        success=ok,
        error=err,
        invite_id=invite_id,
        hangout_id=hangout_id,
    )
    return ok, err


def load_hangout(db: Session, hangout_id: int) -> Hangout | None:
    return (
        db.query(Hangout)
        .options(
            joinedload(Hangout.invites)
            .joinedload(HangoutInvite.profile)
            .joinedload(Profile.allergies),
            joinedload(Hangout.invites)
            .joinedload(HangoutInvite.profile)
            .joinedload(Profile.tags),
            joinedload(Hangout.organizer),
        )
        .filter(Hangout.id == hangout_id)
        .first()
    )


def setup_hangout(db: Session, hangout: Hangout, profile_ids: list[int] | None = None) -> Hangout:
    """Activate hangout and send invite SMS to selected (or existing) invitees."""
    audit_event(
        "hangout.setup.started",
        hangout_id=hangout.id,
        current_status=hangout.status,
        requested_profile_ids=profile_ids,
    )
    if hangout.status == HangoutStatus.closed:
        audit_event(
            "hangout.setup.rejected",
            hangout_id=hangout.id,
            reason="hangout_closed",
        )
        raise ValueError("Hangout is closed")

    requested_ids = profile_ids if profile_ids is not None else [i.profile_id for i in hangout.invites]
    ids = list(
        dict.fromkeys(pid for pid in requested_ids if db.get(Profile, pid) is not None)
    )
    if not ids:
        audit_event(
            "hangout.setup.rejected",
            hangout_id=hangout.id,
            reason="no_valid_profiles_selected" if requested_ids else "no_profiles_selected",
        )
        raise ValueError(
            "No valid profiles to invite"
            if requested_ids
            else "Select at least one profile to invite"
        )

    existing = {inv.profile_id: inv for inv in hangout.invites}
    now = utcnow()
    sms_attempts = 0
    sms_successes = 0
    sms_failures = 0

    if profile_ids is not None:
        # Rows for people left out of an explicit selection who have never been
        # messaged were never really invited. Drop them, otherwise they sit in
        # "pending" forever: process_followups has no clock to measure them
        # against, so they are never texted, chased, or resolved.
        wanted = set(ids)
        for pid, inv in list(existing.items()):
            if pid not in wanted and inv.last_outbound_at is None:
                db.delete(inv)
                del existing[pid]

    for pid in ids:
        profile = db.get(Profile, pid)
        if profile is None:
            continue
        inv = existing.get(pid)
        if inv is None:
            inv = HangoutInvite(hangout_id=hangout.id, profile_id=pid, status=InviteStatus.pending)
            db.add(inv)
            db.flush()
            existing[pid] = inv
        if hangout.status == HangoutStatus.active and inv.status not in (
            InviteStatus.pending,
            InviteStatus.failed_send,
            InviteStatus.no_response,
        ):
            # Already confirmed/declined/remind — skip re-send
            continue
        if (
            hangout.status == HangoutStatus.active
            and inv.status == InviteStatus.pending
            and inv.last_outbound_at is not None
        ):
            # Already invited and waiting for a reply — don't restart the follow-up clock
            continue

        body = craft_invite_message(hangout, profile)
        sms_attempts += 1
        ok, err = send_sms(db, to=profile.phone, body=body, invite_id=inv.id, hangout_id=hangout.id)
        inv.last_outbound_at = now
        inv.followups_sent = 0
        if ok:
            inv.status = InviteStatus.pending
            sms_successes += 1
        else:
            inv.status = InviteStatus.failed_send
            sms_failures += 1
            logger.warning("Invite SMS failed for profile %s: %s", pid, err)

    hangout.status = HangoutStatus.active
    hangout.activated_at = hangout.activated_at or now
    db.commit()
    audit_event(
        "hangout.setup.completed",
        hangout_id=hangout.id,
        invited_profile_ids=ids,
        sms_attempts=sms_attempts,
        sms_successes=sms_successes,
        sms_failures=sms_failures,
        status=hangout.status,
    )
    return load_hangout(db, hangout.id)  # type: ignore[return-value]


def process_inbound_sms(db: Session, from_phone: str, body: str) -> str:
    """Handle invitee reply. Returns auto-response text; "" means reply with nothing."""
    reply = _handle_inbound_sms(db, from_phone, body)
    opt_out = is_opt_out(body)
    if opt_out:
        # The carrier and Twilio have already blocked this number and send their
        # own opt-out confirmation. Anything we reply is undeliverable (Twilio
        # error 21610), so record the decline and stay quiet.
        reply = ""
    audit_event(
        "sms.inbound.processed",
        from_phone=normalize_phone(from_phone),
        body=body,
        reply=reply,
        opt_out=opt_out,
    )
    return reply


def _handle_inbound_sms(db: Session, from_phone: str, body: str) -> str:
    phone = normalize_phone(from_phone)
    log_message(db, phone=phone, body=body, direction=MessageDirection.inbound, success=True)

    intent = parse_reply_intent(body)
    audit_event(
        "sms.inbound.received",
        from_phone=phone,
        body=body,
        parsed_intent=intent,
    )
    # A reply is almost always about the last text this person received, so rank
    # by most recent outbound before falling back to newest invite.
    recency = (
        HangoutInvite.last_outbound_at.desc().nullslast(),
        HangoutInvite.id.desc(),
    )
    invite = (
        db.query(HangoutInvite)
        .join(HangoutInvite.profile)
        .join(HangoutInvite.hangout)
        .options(joinedload(HangoutInvite.profile), joinedload(HangoutInvite.hangout))
        .filter(Profile.phone == phone)
        .filter(Hangout.status == HangoutStatus.active)
        .order_by(*recency)
        .first()
    )

    # Fallback: match by last 10 digits if exact normalize mismatch
    if invite is None:
        digits = "".join(c for c in phone if c.isdigit())[-10:]
        candidates = (
            db.query(HangoutInvite)
            .join(HangoutInvite.profile)
            .join(HangoutInvite.hangout)
            .options(joinedload(HangoutInvite.profile), joinedload(HangoutInvite.hangout))
            .filter(Hangout.status == HangoutStatus.active)
            .order_by(*recency)
            .all()
        )
        for inv in candidates:
            pdigits = "".join(c for c in inv.profile.phone if c.isdigit())[-10:]
            if pdigits == digits:
                invite = inv
                break

    if invite is None:
        db.commit()
        audit_event(
            "sms.inbound.unmatched",
            from_phone=phone,
            body=body,
            parsed_intent=intent,
        )
        return craft_unmatched_reply()

    if intent is None:
        db.commit()
        audit_event(
            "sms.inbound.unrecognized",
            from_phone=phone,
            body=body,
            invite_id=invite.id,
            hangout_id=invite.hangout_id,
        )
        return craft_help_reply()

    # INFO / INFO 2 are read-only — do not change RSVP status
    if intent in {"info", "info2"}:
        hangout = load_hangout(db, invite.hangout_id)
        db.commit()
        if hangout is None:
            return craft_unmatched_reply()
        # Refresh invite against fully-loaded hangout graph
        live_invite = next((i for i in hangout.invites if i.id == invite.id), invite)
        reply = (
            craft_info_detail(hangout, live_invite)
            if intent == "info2"
            else craft_info_summary(hangout, live_invite)
        )
        audit_event(
            "sms.inbound.info",
            from_phone=phone,
            body=body,
            parsed_intent=intent,
            invite_id=invite.id,
            hangout_id=invite.hangout_id,
        )
        return reply

    prev_status = invite.status
    now = utcnow()
    if intent == "confirm":
        invite.status = InviteStatus.confirmed
        invite.responded_at = now
        reply = craft_confirm_reply(invite.hangout)
    elif intent == "remind":
        invite.status = InviteStatus.remind
        invite.responded_at = now
        reply = craft_remind_ack_reply()
        # Immediate short reminder + schedule via status
        rem = craft_reminder_message(invite.hangout, invite.profile)
        send_sms(
            db,
            to=invite.profile.phone,
            body=rem,
            invite_id=invite.id,
            hangout_id=invite.hangout_id,
        )
    else:  # decline
        invite.status = InviteStatus.declined
        invite.responded_at = now
        reply = craft_decline_reply(invite.hangout)

    db.commit()

    audit_event(
        "rsvp.status_changed",
        from_phone=phone,
        body=body,
        parsed_intent=intent,
        invite_id=invite.id,
        hangout_id=invite.hangout_id,
        profile_id=invite.profile_id,
        previous_status=prev_status,
        new_status=invite.status,
    )

    # Threshold organizer notify based on hangout preferences
    hangout = load_hangout(db, invite.hangout_id)
    if hangout:
        evaluate_organizer_threshold_for_reply(
            db,
            hangout,
            intent=intent,
            prev_status=prev_status,
            profile=invite.profile,
        )

    return reply


def _failed_send_count(db: Session, invite_id: int) -> int:
    """Failed outbound sends *since the last successful one* for this invite.

    Counting every failure the invite ever logged would let a transient outage
    months ago combine with one fresh failure to retire a working number, so a
    successful send resets the streak. (The session has autoflush off.)
    """
    db.flush()
    outbound = (MessageLog.invite_id == invite_id, MessageLog.direction == MessageDirection.outbound)
    last_ok = (
        db.query(func.max(MessageLog.id))
        .filter(*outbound)
        .filter(MessageLog.success.is_(True))
        .scalar()
    )
    query = db.query(func.count(MessageLog.id)).filter(*outbound).filter(MessageLog.success.is_(False))
    if last_ok is not None:
        query = query.filter(MessageLog.id > last_ok)
    return query.scalar() or 0


def process_followups(db: Session) -> int:
    """Send due follow-up SMS. Returns count sent."""
    settings = get_settings()
    delays = settings.followup_hour_list
    budget = min(settings.max_followups, len(delays))
    now = utcnow()
    sent = 0
    audit_event(
        "followups.scan.started",
        at=now,
        delays_hours=delays,
        budget=budget,
    )

    invites = (
        db.query(HangoutInvite)
        .join(Hangout)
        .options(joinedload(HangoutInvite.profile), joinedload(HangoutInvite.hangout))
        .filter(Hangout.status == HangoutStatus.active)
        .filter(HangoutInvite.status.in_([InviteStatus.pending, InviteStatus.remind]))
        .all()
    )

    for inv in invites:
        # Nothing has gone out yet, so there is no clock to measure against.
        if inv.last_outbound_at is None:
            continue
        last = inv.last_outbound_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        if inv.followups_sent >= budget:
            # Budget spent. Give the final follow-up its own delay window to be
            # answered before declaring no response, so nobody is marked
            # no_response in the same pass that texted them.
            grace = delays[-1] if delays else 24.0
            if now >= last + timedelta(hours=grace):
                previous_status = inv.status
                inv.status = InviteStatus.no_response
                audit_event(
                    "followup.invite_marked_no_response",
                    invite_id=inv.id,
                    hangout_id=inv.hangout_id,
                    profile_id=inv.profile_id,
                    previous_status=previous_status,
                    new_status=inv.status,
                )
            continue

        next_delay = delays[inv.followups_sent]
        due_at = last + timedelta(hours=next_delay)
        # For sequential follow-ups after first, measure from last outbound
        if now < due_at:
            continue

        attempt = inv.followups_sent + 1
        body = craft_followup_message(inv.hangout, inv.profile, attempt)
        ok, err = send_sms(db, to=inv.profile.phone, body=body, invite_id=inv.id, hangout_id=inv.hangout_id)
        # Always advance the clock: a number that permanently rejects SMS (an
        # opt-out, say) must not be retried on every scheduler tick.
        inv.last_outbound_at = now
        if ok:
            # Only advance the follow-up budget on a successful send, otherwise
            # an outage would burn the budget and mark invitees no_response
            inv.followups_sent = attempt
            sent += 1
        elif _failed_send_count(db, inv.id) >= FOLLOWUP_FAILURE_LIMIT:
            # Retried across several delay windows and still failing — stop and
            # surface it instead of texting into the void forever.
            inv.status = InviteStatus.failed_send
            logger.warning(
                "Giving up on follow-ups for invite %s after repeated failures: %s", inv.id, err
            )

    db.commit()
    audit_event(
        "followups.scan.completed",
        at=now,
        candidate_count=len(invites),
        sent_count=sent,
    )
    return sent


def process_organizer_intervals(db: Session) -> int:
    settings = get_settings()
    now = utcnow()
    sent = 0
    audit_event("organizer_intervals.scan.started", at=now)
    hangouts = (
        db.query(Hangout)
        .options(
            joinedload(Hangout.invites)
            .joinedload(HangoutInvite.profile)
            .joinedload(Profile.allergies),
            joinedload(Hangout.organizer),
        )
        .filter(Hangout.status == HangoutStatus.active)
        .filter(Hangout.notify_enabled.is_(True))
        .filter(Hangout.notify_interval.is_(True))
        .all()
    )
    for h in hangouts:
        phone = resolve_organizer_phone(db, h)
        if not phone:
            continue
        hours = h.notify_interval_hours or settings.organizer_interval_hours or 6
        last = h.last_organizer_notify_at or h.activated_at
        if last is None:
            last = now
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now < last + timedelta(hours=hours):
            continue

        fingerprint = hangout_digest_fingerprint(h)
        if h.notify_interval_only_if_changed and h.last_digest_fingerprint == fingerprint:
            # Advance the clock so we don't re-check every scheduler tick forever,
            # but do not SMS when nothing meaningful changed.
            h.last_organizer_notify_at = now
            continue

        body = craft_organizer_digest(h)
        ok, _ = send_sms(db, to=phone, body=body, hangout_id=h.id)
        if ok:
            h.last_organizer_notify_at = now
            h.last_digest_fingerprint = fingerprint
            sent += 1
    db.commit()
    audit_event(
        "organizer_intervals.scan.completed",
        at=now,
        candidate_count=len(hangouts),
        sent_count=sent,
    )
    return sent
