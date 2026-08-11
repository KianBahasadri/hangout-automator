"""Permanent SMS do-not-contact (KIAN-532)."""

from app.models import InviteStatus, MessageDirection, MessageLog, SmsOptOut
from app.services import (
    DNC_ERROR,
    clear_sms_opt_out,
    is_sms_opted_out,
    process_inbound_sms,
    record_sms_opt_out,
    send_sms,
    setup_hangout,
)
from tests.test_rsvp_flow import _active_hangout_with_invite, _profile


def test_stop_records_global_dnc_and_declines_invite(db):
    invitee = _profile(db, "Sam", "+15551112222")
    _, invite = _active_hangout_with_invite(db, invitee)
    db.commit()

    assert process_inbound_sms(db, "+15551112222", "STOP") == ""

    db.refresh(invite)
    assert invite.status == InviteStatus.declined
    assert is_sms_opted_out(db, "+15551112222")
    row = db.query(SmsOptOut).filter(SmsOptOut.phone == "+15551112222").one()
    assert row.source == "keyword"


def test_stop_forever_records_dnc_without_invite(db):
    assert process_inbound_sms(db, "+15559998888", "STOP FOREVER") == ""
    assert is_sms_opted_out(db, "+15559998888")


def test_send_sms_rejects_dnc_without_provider(db, monkeypatch):
    calls = []

    class UnexpectedProvider:
        def send(self, to, body):
            calls.append((to, body))
            return True, None

    monkeypatch.setattr("app.services.get_sms_provider", lambda: UnexpectedProvider())
    record_sms_opt_out(db, "+15551234567", source="admin", reason="test")
    db.commit()

    ok, error = send_sms(db, to="+15551234567", body="must not send")
    db.commit()

    assert ok is False
    assert error == DNC_ERROR
    assert calls == []
    entry = db.query(MessageLog).filter(MessageLog.direction == MessageDirection.outbound).one()
    assert entry.success is False
    assert entry.error == DNC_ERROR


def test_setup_skips_dnc_and_marks_declined(db, monkeypatch, workspace):
    calls = []

    class Provider:
        def send(self, to, body):
            calls.append(to)
            return True, None

    monkeypatch.setattr("app.services.get_sms_provider", lambda: Provider())

    ok_person = _profile(db, "Ok", "+15550001111")
    dnc_person = _profile(db, "Dnc", "+15550002222")
    record_sms_opt_out(db, dnc_person.phone, source="keyword", reason="STOP")
    from app.models import Hangout, HangoutStatus

    hangout = Hangout(status=HangoutStatus.draft, workspace_id=workspace.id, motive="test")
    db.add(hangout)
    db.commit()

    setup_hangout(db, hangout, [ok_person.id, dnc_person.id], workspace)
    db.refresh(hangout)

    assert hangout.status == HangoutStatus.active
    by_profile = {inv.profile_id: inv for inv in hangout.invites}
    assert by_profile[ok_person.id].status == InviteStatus.pending
    assert by_profile[dnc_person.id].status == InviteStatus.declined
    assert calls == ["+15550001111"]


def test_start_clears_dnc_and_replies(db, monkeypatch):
    record_sms_opt_out(db, "+15551112222", source="keyword", reason="STOP")
    db.commit()
    assert is_sms_opted_out(db, "+15551112222")

    reply = process_inbound_sms(db, "+15551112222", "START")
    assert "opted back in" in reply.lower()
    assert not is_sms_opted_out(db, "+15551112222")

    calls = []

    class Provider:
        def send(self, to, body):
            calls.append((to, body))
            return True, None

    monkeypatch.setattr("app.services.get_sms_provider", lambda: Provider())
    ok, err = send_sms(db, to="+15551112222", body="welcome back")
    assert (ok, err) == (True, None)
    assert calls == [("+15551112222", "welcome back")]


def test_hangout_only_no_does_not_set_dnc(db):
    invitee = _profile(db, "Sam", "+15551112222")
    _, invite = _active_hangout_with_invite(db, invitee)
    db.commit()

    process_inbound_sms(db, "+15551112222", "NO")
    db.refresh(invite)
    assert invite.status == InviteStatus.declined
    assert not is_sms_opted_out(db, "+15551112222")


def test_admin_opt_outs_page(client, db):
    record_sms_opt_out(db, "+15551112222", source="admin", reason="admin")
    db.commit()

    response = client.get("/admin/opt-outs")
    assert response.status_code == 200
    assert "SMS opt-outs" in response.text
    # Pretty-printed NANP form in the table
    assert "555" in response.text


def test_admin_add_and_remove_opt_out(client, db):
    add = client.post("/admin/opt-outs", data={"phone": "555 333 4444"}, follow_redirects=False)
    assert add.status_code == 303
    db.expire_all()
    assert is_sms_opted_out(db, "+15553334444")

    row = db.query(SmsOptOut).filter(SmsOptOut.phone == "+15553334444").one()
    row_id = row.id
    db.commit()  # release locks before the client session mutates the same row
    remove = client.post(f"/admin/opt-outs/{row_id}/delete", follow_redirects=False)
    assert remove.status_code == 303
    db.expire_all()
    assert not is_sms_opted_out(db, "+15553334444")


def test_clear_sms_opt_out_helper(db):
    record_sms_opt_out(db, "+15551110000", source="admin")
    db.commit()
    assert clear_sms_opt_out(db, "+15551110000") is True
    assert clear_sms_opt_out(db, "+15551110000") is False
    db.commit()
