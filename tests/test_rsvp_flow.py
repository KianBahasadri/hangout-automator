from app.models import (
    Hangout,
    HangoutInvite,
    HangoutStatus,
    InviteStatus,
    MessageDirection,
    MessageLog,
    Profile,
)
from app.services import process_inbound_sms


def _profile(db, name, phone):
    profile = Profile(name=name, phone=phone)
    db.add(profile)
    db.flush()
    return profile


def _active_hangout_with_invite(db, invitee, organizer=None, **notify_kwargs):
    hangout = Hangout(
        status=HangoutStatus.active,
        motive="Beach day",
        organizer_profile_id=organizer.id if organizer else None,
        organizer_phone=organizer.phone if organizer else None,
        **notify_kwargs,
    )
    db.add(hangout)
    db.flush()
    invite = HangoutInvite(
        hangout_id=hangout.id, profile_id=invitee.id, status=InviteStatus.pending
    )
    db.add(invite)
    db.flush()
    return hangout, invite


def _outbound_logs(db, phone=None):
    query = db.query(MessageLog).filter(MessageLog.direction == MessageDirection.outbound)
    if phone:
        query = query.filter(MessageLog.phone == phone)
    return query.all()


def test_invitee_reply_confirm_updates_status(db):
    invitee = _profile(db, "Sam", "+15551112222")
    organizer = _profile(db, "Lee", "+15553334444")
    hangout, invite = _active_hangout_with_invite(db, invitee, organizer)
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "confirm")

    assert f"You're confirmed for hangout #{hangout.id}." in reply
    assert "See you!" in reply
    db.refresh(invite)
    assert invite.status == InviteStatus.confirmed
    assert invite.responded_at is not None


def test_invitee_reply_decline_updates_status(db):
    invitee = _profile(db, "Sam", "+15551112222")
    organizer = _profile(db, "Lee", "+15553334444")
    _, invite = _active_hangout_with_invite(db, invitee, organizer)
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "no")

    assert "not coming" in reply
    db.refresh(invite)
    assert invite.status == InviteStatus.declined


def test_invitee_reply_remind_marks_remind_and_sends_reminder(db):
    invitee = _profile(db, "Sam", "+15551112222")
    _, invite = _active_hangout_with_invite(db, invitee)
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "remind")

    assert "reminder" in reply.lower()
    db.refresh(invite)
    assert invite.status == InviteStatus.remind
    logs = _outbound_logs(db, phone="+15551112222")
    assert any("Reminder for Sam" in log.body for log in logs)


def test_unknown_phone_gets_unmatched_message(db):
    _profile(db, "Sam", "+15551112222")
    db.commit()

    reply = process_inbound_sms(db, "+19998887777", "confirm")

    assert "couldn't match" in reply


def test_reply_matches_by_last_10_digits_fallback(db):
    invitee = _profile(db, "Sam", "+15551112222")
    _, invite = _active_hangout_with_invite(db, invitee)
    db.commit()

    reply = process_inbound_sms(db, "+0015551112222", "confirm")

    assert "confirmed" in reply
    db.refresh(invite)
    assert invite.status == InviteStatus.confirmed


def test_reply_to_inactive_hangout_is_unmatched(db):
    invitee = _profile(db, "Sam", "+15551112222")
    hangout = Hangout(status=HangoutStatus.draft, motive="Draft")
    db.add(hangout)
    db.flush()
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=invitee.id, status=InviteStatus.pending))
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "confirm")

    assert "couldn't match" in reply


def test_opt_out_keyword_is_treated_as_decline(db):
    invitee = _profile(db, "Sam", "+15551112222")
    _, invite = _active_hangout_with_invite(db, invitee)
    db.commit()

    process_inbound_sms(db, "+15551112222", "STOP")

    db.refresh(invite)
    assert invite.status == InviteStatus.declined


def test_opt_out_gets_no_reply(db):
    """Twilio blocks the number on STOP and sends its own confirmation; ours would bounce."""
    invitee = _profile(db, "Sam", "+15551112222")
    _active_hangout_with_invite(db, invitee)
    db.commit()

    assert process_inbound_sms(db, "+15551112222", "STOP") == ""


def test_opt_out_from_unknown_number_gets_no_reply(db):
    assert process_inbound_sms(db, "+19998887777", "unsubscribe") == ""


def test_opt_out_webhook_returns_twiml_without_a_message(db, client):
    invitee = _profile(db, "Sam", "+15551112222")
    _active_hangout_with_invite(db, invitee)
    db.commit()

    response = client.post("/webhooks/sms", json={"From": "+15551112222", "Body": "STOP"})

    assert response.status_code == 200
    assert "<Message>" not in response.text


def test_reply_applies_to_most_recently_messaged_invite(db):
    from datetime import timedelta

    from app.services import utcnow

    invitee = _profile(db, "Sam", "+15551112222")
    older_hangout, older_invite = _active_hangout_with_invite(db, invitee)
    newer_hangout, newer_invite = _active_hangout_with_invite(db, invitee)
    # The older hangout is the one that just texted them.
    older_invite.last_outbound_at = utcnow()
    newer_invite.last_outbound_at = utcnow() - timedelta(days=3)
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "confirm")

    assert f"#{older_hangout.id}" in reply
    db.refresh(older_invite)
    db.refresh(newer_invite)
    assert older_invite.status == InviteStatus.confirmed
    assert newer_invite.status == InviteStatus.pending


def test_confirm_triggers_organizer_threshold_sms(db):
    invitee = _profile(db, "Sam", "+15551112222")
    organizer = _profile(db, "Lee", "+15553334444")
    _, invite = _active_hangout_with_invite(
        db,
        invitee,
        organizer,
        notify_enabled=True,
        notify_threshold=True,
        notify_on_new_confirm=True,
        notify_on_decline=False,
        notify_on_allergy=False,
        notify_on_ride_needed=False,
        notify_confirm_goal=0,
    )
    db.commit()

    process_inbound_sms(db, "+15551112222", "confirm")

    logs = _outbound_logs(db, phone="+15553334444")
    assert any("new confirmation" in log.body for log in logs)
    db.refresh(invite)
    assert invite.status == InviteStatus.confirmed


def test_info_returns_headcounts_without_changing_status(db):
    from app.models import Drive

    sam = _profile(db, "Sam", "+15551112222")
    lee = _profile(db, "Lee", "+15553334444")
    pat = _profile(db, "Pat", "+15555556666")
    hangout, sam_invite = _active_hangout_with_invite(db, sam)
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=lee.id, status=InviteStatus.confirmed))
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=pat.id, status=InviteStatus.declined))
    lee.drive = Drive.yes
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "INFO")

    assert "headcount" in reply.lower()
    assert "Coming: 1" in reply
    assert "Pending: 1" in reply
    assert "Declined: 1" in reply
    assert "Lee" not in reply  # names only on INFO 2
    assert "Your RSVP: pending" in reply
    db.refresh(sam_invite)
    assert sam_invite.status == InviteStatus.pending
    assert sam_invite.responded_at is None


def test_info_2_returns_named_guest_list(db):
    from app.models import Drive

    sam = _profile(db, "Sam", "+15551112222")
    lee = _profile(db, "Lee", "+15553334444")
    pat = _profile(db, "Pat", "+15555556666")
    hangout, sam_invite = _active_hangout_with_invite(db, sam)
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=lee.id, status=InviteStatus.confirmed))
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=pat.id, status=InviteStatus.declined))
    lee.drive = Drive.yes
    db.commit()

    reply = process_inbound_sms(db, "+15551112222", "info 2")

    assert "guest list" in reply.lower()
    assert "Coming (1): Lee" in reply
    assert "Pending (1): Sam" in reply
    assert "Declined (1): Pat" in reply
    assert "Can drive: Lee" in reply
    assert "Your RSVP: pending" in reply
    db.refresh(sam_invite)
    assert sam_invite.status == InviteStatus.pending


def test_invite_message_is_multiline_with_info_options(db):
    from app.services import load_hangout, setup_hangout

    invitee = _profile(db, "Sam", "+15551112222")
    hangout = Hangout(
        status=HangoutStatus.draft,
        motive="Dinner",
        day_date="2026-08-08",
        time="19:00",
        location="Sam's place",
    )
    db.add(hangout)
    db.flush()
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=invitee.id, status=InviteStatus.pending))
    db.commit()

    setup_hangout(db, load_hangout(db, hangout.id), profile_ids=[invitee.id])
    body = (
        db.query(MessageLog)
        .filter(MessageLog.direction == MessageDirection.outbound)
        .order_by(MessageLog.id.desc())
        .first()
        .body
    )
    assert "You're invited:" in body
    assert "When: 2026-08-08 at 19:00" in body
    assert "Where: Sam's place" in body
    assert "INFO — headcount" in body
    assert "INFO 2 — full guest list" in body
