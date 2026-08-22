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
    craft_opt_in_reply,
    craft_organizer_digest,
    craft_unmatched_reply,
    is_opt_in,
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
    SmsOptOut,
    Workspace,
)
from app.sms import get_sms_provider, is_valid_phone, normalize_phone

logger = logging.getLogger(__name__)

# Returned by send_sms when the destination is on the permanent DNC list.
DNC_ERROR = "Number is on the do-not-contact list"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_sms_opted_out(db: Session, phone: str) -> bool:
    """True when this normalized phone is on the global permanent DNC list."""
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    return db.query(SmsOptOut.id).filter(SmsOptOut.phone == normalized).first() is not None


def opted_out_phones(db: Session, phones: list[str] | set[str] | None = None) -> set[str]:
    """Normalized phones currently on the DNC list.

    When *phones* is given, only those candidates are checked (for invitee UI).
    """
    q = db.query(SmsOptOut.phone)
    if phones is not None:
        normalized = {normalize_phone(p) for p in phones if p}
        normalized.discard("")
        if not normalized:
            return set()
        q = q.filter(SmsOptOut.phone.in_(normalized))
    return {row[0] for row in q.all()}


def record_sms_opt_out(
    db: Session,
    phone: str,
    *,
    source: str = "keyword",
    reason: str | None = None,
) -> SmsOptOut:
    """Upsert a permanent DNC row for *phone*. Idempotent on the same number."""
    normalized = normalize_phone(phone)
    existing = db.query(SmsOptOut).filter(SmsOptOut.phone == normalized).first()
    if existing is not None:
        if reason and not existing.reason:
            existing.reason = reason
        return existing
    row = SmsOptOut(
        phone=normalized,
        source=source,
        reason=reason,
        opted_out_at=utcnow(),
    )
    db.add(row)
    db.flush()
    audit_event(
        "sms.opt_out.recorded",
        phone=normalized,
        source=source,
        reason=reason,
        opt_out_id=row.id,
    )
    return row


def clear_sms_opt_out(db: Session, phone: str) -> bool:
    """Remove a DNC row. Returns True when a row was deleted."""
    normalized = normalize_phone(phone)
    row = db.query(SmsOptOut).filter(SmsOptOut.phone == normalized).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    audit_event("sms.opt_out.cleared", phone=normalized)
    return True


def normalize_tag_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def normalize_allergy_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def load_tags_by_ids(db: Session, tag_ids: list[int] | None, workspace) -> list:
    from app.models import Tag

    if not tag_ids:
        return []
    ids = sorted({int(t) for t in tag_ids})
    return db.query(Tag).filter(Tag.id.in_(ids), Tag.workspace_id == workspace.id).all()


def load_allergies_by_ids(db: Session, allergy_ids: list[int] | None, workspace) -> list:
    from app.models import Allergy

    if not allergy_ids:
        return []
    ids = sorted({int(a) for a in allergy_ids})
    return db.query(Allergy).filter(Allergy.id.in_(ids), Allergy.workspace_id == workspace.id).all()


def profile_has_allergies(profile: Profile) -> bool:
    return bool(getattr(profile, "has_allergies", False))


def profile_allergies_label(profile: Profile) -> str | None:
    return getattr(profile, "food_allergies_label", None)


def resolve_organizer_phone(db: Session, hangout: Hangout) -> str | None:
    """Phone for organizer SMS: selected contact profile, then hangout stamp.

    The hangout stamp may come from a selected profile or from the creator's
    My Profile phone (copied at create/edit time). The profile lookup is
    filtered to the hangout's own workspace so this stays safe when called
    from the worker's cross-workspace sweep.
    """
    if hangout.organizer_profile_id:
        profile = hangout.organizer
        if profile is None:
            profile = (
                db.query(Profile)
                .filter(
                    Profile.id == hangout.organizer_profile_id,
                    Profile.workspace_id == hangout.workspace_id,
                )
                .first()
            )
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
    ok, _ = send_sms(
        db,
        to=phone,
        body=body,
        hangout_id=hangout.id,
        workspace_id=hangout.workspace_id,
    )
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
        if (
            hangout.notify_on_ride_needed
            and profile.drive is not None
            and profile.drive.value == "no"
        ):
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

    ok = maybe_send_organizer_threshold(db, hangout, reasons=reasons, high_priority=high_priority)
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
    workspace_id: int | None = None,
) -> MessageLog:
    entry = MessageLog(
        phone=phone,
        body=body,
        direction=direction,
        success=success,
        error=error,
        invite_id=invite_id,
        hangout_id=hangout_id,
        workspace_id=workspace_id,
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
    workspace_id: int | None = None,
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
            workspace_id=workspace_id,
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

    # Permanent DNC (STOP / STOP FOREVER / admin): never call the provider.
    if is_sms_opted_out(db, phone):
        error = DNC_ERROR
        log_message(
            db,
            phone=phone,
            body=body,
            direction=MessageDirection.outbound,
            success=False,
            error=error,
            invite_id=invite_id,
            hangout_id=hangout_id,
            workspace_id=workspace_id,
        )
        audit_event(
            "sms.outbound.rejected",
            level=logging.WARNING,
            to=phone,
            body=body,
            reason="do_not_contact",
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
            workspace_id=workspace_id,
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
        workspace_id=workspace_id,
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


def load_hangout(db: Session, hangout_id: int, workspace) -> Hangout | None:
    return (
        db.query(Hangout)
        .options(
            joinedload(Hangout.invites)
            .joinedload(HangoutInvite.profile)
            .joinedload(Profile.allergies),
            joinedload(Hangout.invites).joinedload(HangoutInvite.profile).joinedload(Profile.tags),
            joinedload(Hangout.organizer),
        )
        .filter(Hangout.id == hangout_id, Hangout.workspace_id == workspace.id)
        .first()
    )


def setup_hangout(
    db: Session, hangout: Hangout, profile_ids: list[int] | None, workspace
) -> Hangout:
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

    requested_ids = (
        profile_ids if profile_ids is not None else [i.profile_id for i in hangout.invites]
    )
    ids = list(
        dict.fromkeys(
            pid
            for pid in requested_ids
            if db.query(Profile)
            .filter(Profile.id == pid, Profile.workspace_id == workspace.id)
            .first()
            is not None
        )
    )
    if not ids:
        audit_event(
            "hangout.setup.rejected",
            hangout_id=hangout.id,
            reason="no_valid_contacts_selected" if requested_ids else "no_contacts_selected",
        )
        raise ValueError(
            "No valid contacts to invite"
            if requested_ids
            else "Select at least one contact to invite"
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
        profile = (
            db.query(Profile)
            .filter(Profile.id == pid, Profile.workspace_id == workspace.id)
            .first()
        )
        if profile is None:
            continue
        inv = existing.get(pid)
        if inv is None:
            inv = HangoutInvite(
                hangout_id=hangout.id,
                profile_id=pid,
                status=InviteStatus.pending,
                workspace_id=workspace.id,
            )
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
        ok, err = send_sms(
            db,
            to=profile.phone,
            body=body,
            invite_id=inv.id,
            hangout_id=hangout.id,
            workspace_id=workspace.id,
        )
        inv.last_outbound_at = now
        inv.followups_sent = 0
        if ok:
            inv.status = InviteStatus.pending
            sms_successes += 1
        elif err == DNC_ERROR:
            # Permanent opt-out: do not retry; surface as declined so follow-ups
            # leave this invite alone.
            inv.status = InviteStatus.declined
            inv.responded_at = now
            sms_failures += 1
            logger.info("Invite SMS skipped for opted-out profile %s", pid)
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
    return load_hangout(db, hangout.id, workspace)  # type: ignore[return-value]


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
        opt_in=is_opt_in(body),
    )
    return reply


def _handle_inbound_sms(db: Session, from_phone: str, body: str) -> str:
    phone = normalize_phone(from_phone)
    # The inbound row starts with no workspace: it is attributed only when an
    # invite is matched, so unmatched rows stay NULL (message_logs.workspace_id
    # is deliberately nullable).
    inbound_log = log_message(
        db, phone=phone, body=body, direction=MessageDirection.inbound, success=True
    )

    intent = parse_reply_intent(body)
    audit_event(
        "sms.inbound.received",
        from_phone=phone,
        body=body,
        parsed_intent=intent,
    )

    # Permanent opt-out always lands on the global DNC list, even with no
    # matching invite. Carrier STOP gets no auto-reply (outer process_inbound).
    if is_opt_out(body):
        reason = (body or "").strip()[:120] or "stop"
        record_sms_opt_out(db, phone, source="keyword", reason=reason)

    # START / UNSTOP clears DNC before invite matching so re-opt-in works even
    # without an active hangout.
    if intent == "opt_in":
        cleared = clear_sms_opt_out(db, phone)
        db.commit()
        audit_event(
            "sms.inbound.opt_in",
            from_phone=phone,
            body=body,
            cleared=cleared,
        )
        return craft_opt_in_reply()
    # A reply is almost always about the last text this person received, so rank
    # by most recent outbound before falling back to newest invite.
    recency = (
        HangoutInvite.last_outbound_at.desc().nullslast(),
        HangoutInvite.id.desc(),
    )
    candidates = (
        db.query(HangoutInvite)
        .join(HangoutInvite.profile)
        .join(HangoutInvite.hangout)
        .options(joinedload(HangoutInvite.profile), joinedload(HangoutInvite.hangout))
        .filter(Profile.phone == phone)
        .filter(Hangout.status == HangoutStatus.active)
        .order_by(*recency)
        .all()
    )
    invite = candidates[0] if candidates else None
    candidate_workspaces = {inv.workspace_id for inv in candidates}

    # Fallback: match by last 10 digits if exact normalize mismatch
    if invite is None:
        digits = "".join(c for c in phone if c.isdigit())[-10:]
        all_active = (
            db.query(HangoutInvite)
            .join(HangoutInvite.profile)
            .join(HangoutInvite.hangout)
            .options(joinedload(HangoutInvite.profile), joinedload(HangoutInvite.hangout))
            .filter(Hangout.status == HangoutStatus.active)
            .order_by(*recency)
            .all()
        )
        matches = [
            inv
            for inv in all_active
            if "".join(c for c in inv.profile.phone if c.isdigit())[-10:] == digits
        ]
        if matches:
            invite = matches[0]
            candidates = matches
            candidate_workspaces = {inv.workspace_id for inv in matches}

    if invite is None:
        db.commit()
        audit_event(
            "sms.inbound.unmatched",
            from_phone=phone,
            body=body,
            parsed_intent=intent,
        )
        return craft_unmatched_reply()

    # Matched: attribute the inbound row to the invite's workspace.
    if inbound_log.workspace_id is None:
        inbound_log.workspace_id = invite.workspace_id
    if len(candidate_workspaces) > 1:
        # The same person was invited to active hangouts in several
        # workspaces in this window; we picked by most-recent-outbound, but
        # the ambiguity is observable. Per-workspace Twilio numbers are the
        # real fix (see docs/tenancy.md).
        audit_event(
            "sms.inbound.ambiguous_workspace",
            from_phone=phone,
            chosen_workspace_id=invite.workspace_id,
            candidate_workspace_ids=sorted(candidate_workspaces),
        )

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

    # INFO / MORE INFO are read-only — do not change RSVP status
    if intent in {"info", "info2"}:
        hangout = load_hangout(db, invite.hangout_id, Workspace(id=invite.workspace_id))
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
    hangout = load_hangout(db, invite.hangout_id, Workspace(id=invite.workspace_id))
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
    outbound = (
        MessageLog.invite_id == invite_id,
        MessageLog.direction == MessageDirection.outbound,
    )
    last_ok = (
        db.query(func.max(MessageLog.id))
        .filter(*outbound)
        .filter(MessageLog.success.is_(True))
        .scalar()
    )
    query = (
        db.query(func.count(MessageLog.id)).filter(*outbound).filter(MessageLog.success.is_(False))
    )
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

    # Candidate snapshot (no locks): who owns each row this tick is decided by
    # the per-invite claim below. Locking the whole batch up front cannot work
    # — the per-invite claim commit would release every other row's lock at
    # once, letting a second worker re-claim the rest.
    invites = (
        db.query(HangoutInvite)
        .join(Hangout)
        .options(joinedload(HangoutInvite.profile), joinedload(HangoutInvite.hangout))
        .filter(Hangout.status == HangoutStatus.active)
        .filter(HangoutInvite.status.in_([InviteStatus.pending, InviteStatus.remind]))
        .all()
    )
    db.commit()  # close the snapshot transaction; each claim below is its own

    for candidate in invites:
        # CLAIM this invite row in its own short transaction. SKIP LOCKED
        # makes the loser walk away: two workers can never both own one invite.
        claimed = (
            db.query(HangoutInvite.id)
            .filter(HangoutInvite.id == candidate.id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if claimed is None:
            continue  # another worker owns this invite for this tick
        # Re-read the row under the lock with a FRESH statement snapshot: the
        # claim statement's snapshot may predate the other worker's committed
        # clock advance (the lock can be acquired mid-scan, after that commit),
        # and the clock must be current before deciding due-ness.
        # populate_existing is load-bearing: the row is in this session's
        # identity map from the candidate snapshot, and without it the object
        # can keep stale attributes when its expiration state races the other
        # worker's commit.
        inv = (
            db.query(HangoutInvite)
            .filter(HangoutInvite.id == claimed.id)
            .populate_existing()
            .first()
        )
        if inv.last_outbound_at is None:
            # Nothing has gone out yet, so there is no clock to measure against.
            db.commit()
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
            db.commit()
            continue

        next_delay = delays[inv.followups_sent]
        due_at = last + timedelta(hours=next_delay)
        # For sequential follow-ups after first, measure from last outbound
        if now < due_at:
            db.commit()
            continue

        # CLAIM: advance and commit the clock BEFORE dispatching to Twilio. A
        # crash between send and commit must not re-send on the next tick. A
        # number that permanently rejects SMS (an opt-out, say) is likewise
        # never retried on every scheduler tick.
        inv.last_outbound_at = now
        db.commit()

        attempt = inv.followups_sent + 1
        body = craft_followup_message(inv.hangout, inv.profile, attempt)
        try:
            ok, err = send_sms(
                db,
                to=inv.profile.phone,
                body=body,
                invite_id=inv.id,
                hangout_id=inv.hangout_id,
                workspace_id=inv.workspace_id,
            )
        except Exception:
            # Persist the failure log row even though the send raised.
            db.commit()
            raise
        if ok:
            # Only advance the follow-up budget on a successful send, otherwise
            # an outage would burn the budget and mark invitees no_response
            inv.followups_sent = attempt
            sent += 1
        elif err == DNC_ERROR:
            inv.status = InviteStatus.declined
            inv.responded_at = now
            logger.info("Follow-up skipped for opted-out invite %s", inv.id)
        elif _failed_send_count(db, inv.id) >= FOLLOWUP_FAILURE_LIMIT:
            # Retried across several delay windows and still failing — stop and
            # surface it instead of texting into the void forever.
            inv.status = InviteStatus.failed_send
            logger.warning(
                "Giving up on follow-ups for invite %s after repeated failures: %s", inv.id, err
            )
        db.commit()
    # Close the sweep transaction even when every invite was skipped: the
    # FOR UPDATE row locks must not outlive the tick, or the next write
    # against those rows (the per-test wipe included) blocks forever.
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
    # Candidate snapshot (no locks) — the per-hangout claim below decides who
    # owns each row this tick. The lock target is hangouts, not invites: the
    # clock this sweep advances is Hangout.last_organizer_notify_at.
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
    db.commit()  # close the snapshot transaction; each claim below is its own

    for candidate in hangouts:
        # CLAIM this hangout row in its own short transaction; the loser of a
        # concurrent claim walks away (SKIP LOCKED).
        claimed = (
            db.query(Hangout.id)
            .filter(Hangout.id == candidate.id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if claimed is None:
            continue  # another worker owns this hangout for this tick
        # Fresh-snapshot re-read under the lock (see process_followups): the
        # clock must be current, or a mid-scan lock acquisition double-sends.
        h = db.query(Hangout).filter(Hangout.id == claimed.id).populate_existing().first()
        phone = resolve_organizer_phone(db, h)
        if not phone:
            db.commit()
            continue
        hours = h.notify_interval_hours or settings.organizer_interval_hours or 6
        last = h.last_organizer_notify_at or h.activated_at
        if last is None:
            last = now
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now < last + timedelta(hours=hours):
            db.commit()
            continue

        fingerprint = hangout_digest_fingerprint(h)
        if h.notify_interval_only_if_changed and h.last_digest_fingerprint == fingerprint:
            # Advance the clock so we don't re-check every scheduler tick forever,
            # but do not SMS when nothing meaningful changed.
            h.last_organizer_notify_at = now
            db.commit()
            continue

        # CLAIM: commit the clock before dispatching, so a crash between send
        # and commit cannot double-send on the next tick.
        h.last_organizer_notify_at = now
        db.commit()

        body = craft_organizer_digest(h)
        try:
            ok, _ = send_sms(db, to=phone, body=body, hangout_id=h.id, workspace_id=h.workspace_id)
        except Exception:
            db.commit()  # persist the failure log row even though the send raised.
            raise
        if ok:
            h.last_digest_fingerprint = fingerprint
            sent += 1
        db.commit()
    # Close the sweep transaction so no row locks outlive the tick
    # (see process_followups).
    db.commit()
    audit_event(
        "organizer_intervals.scan.completed",
        at=now,
        candidate_count=len(hangouts),
        sent_count=sent,
    )
    return sent
