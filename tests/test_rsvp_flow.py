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

    assert reply == f"You're confirmed for hangout #{hangout.id}. See you!"
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
