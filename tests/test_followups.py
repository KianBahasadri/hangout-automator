from datetime import timedelta

import pytest

from app.models import (
    Hangout,
    HangoutInvite,
    HangoutStatus,
    InviteStatus,
    MessageDirection,
    MessageLog,
    Profile,
    Workspace,
)
from app.services import FOLLOWUP_FAILURE_LIMIT, process_followups, utcnow

# conftest sets FOLLOWUP_HOURS=1,2, so delays are [1.0, 2.0] and the budget is 2.


@pytest.fixture
def invite(db):
    workspace_id = db.query(Workspace.id).filter(Workspace.slug == "default").scalar()
    profile = Profile(name="Sam", phone="+15551112222", workspace_id=workspace_id)
    db.add(profile)
    db.flush()
    hangout = Hangout(status=HangoutStatus.active, motive="Beach day", workspace_id=workspace_id)
    db.add(hangout)
    db.flush()
    row = HangoutInvite(
        hangout_id=hangout.id,
        profile_id=profile.id,
        status=InviteStatus.pending,
        last_outbound_at=utcnow() - timedelta(hours=5),
        workspace_id=workspace_id,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def failing_sms(monkeypatch):
    class FailingProvider:
        def send(self, to, body):
            return False, "21610 attempt to send to unsubscribed recipient"

    monkeypatch.setattr("app.services.get_sms_provider", lambda: FailingProvider())


def _age(db, invite, hours):
    invite.last_outbound_at = utcnow() - timedelta(hours=hours)
    db.commit()


def _failed_logs(db, invite):
    return (
        db.query(MessageLog)
        .filter(MessageLog.invite_id == invite.id, MessageLog.success.is_(False))
        .count()
    )


def test_final_followup_does_not_immediately_mark_no_response(db, invite):
    assert process_followups(db) == 1
    db.refresh(invite)
    assert invite.followups_sent == 1

    _age(db, invite, 5)
    assert process_followups(db) == 1
    db.refresh(invite)

    assert invite.followups_sent == 2
    assert invite.status == InviteStatus.pending, "must not be no_response in the sending pass"

    # Budget is spent but the final follow-up still has its delay window.
    assert process_followups(db) == 0
    db.refresh(invite)
    assert invite.status == InviteStatus.pending


def test_no_response_once_final_window_elapses(db, invite):
    process_followups(db)
    _age(db, invite, 5)
    process_followups(db)
    db.refresh(invite)
    assert invite.followups_sent == 2

    _age(db, invite, 3)  # past the 2h final delay
    assert process_followups(db) == 0
    db.refresh(invite)
    assert invite.status == InviteStatus.no_response


def test_failed_followup_backs_off_instead_of_retrying_every_tick(db, invite, failing_sms):
    assert process_followups(db) == 0
    db.refresh(invite)

    assert invite.followups_sent == 0, "a failed send must not burn the budget"
    assert invite.status == InviteStatus.pending
    assert _failed_logs(db, invite) == 1

    # Immediately due again only if the clock was not advanced.
    process_followups(db)
    process_followups(db)
    assert _failed_logs(db, invite) == 1, "must wait for the next delay window, not retry per tick"


def test_repeated_failures_stop_followups(db, invite, failing_sms):
    for _ in range(FOLLOWUP_FAILURE_LIMIT):
        _age(db, invite, 5)
        process_followups(db)

    db.refresh(invite)
    assert _failed_logs(db, invite) == FOLLOWUP_FAILURE_LIMIT
    assert invite.status == InviteStatus.failed_send

    # Dropped out of the follow-up query entirely.
    _age(db, invite, 5)
    process_followups(db)
    assert _failed_logs(db, invite) == FOLLOWUP_FAILURE_LIMIT


def test_failure_streak_resets_after_a_successful_send(db, invite, failing_sms):
    """An old outage plus one fresh failure must not retire a number that works."""
    for _ in range(FOLLOWUP_FAILURE_LIMIT - 1):
        db.add(
            MessageLog(
                phone=invite.profile.phone,
                body="earlier outage",
                direction=MessageDirection.outbound,
                success=False,
                invite_id=invite.id,
            )
        )
    db.add(
        MessageLog(
            phone=invite.profile.phone,
            body="recovered",
            direction=MessageDirection.outbound,
            success=True,
            invite_id=invite.id,
        )
    )
    db.commit()

    process_followups(db)
    db.refresh(invite)

    assert _failed_logs(db, invite) == FOLLOWUP_FAILURE_LIMIT, "lifetime failures reach the limit"
    assert invite.status == InviteStatus.pending, "but the streak since the success is only 1"


def test_successful_followup_logs_outbound(db, invite):
    process_followups(db)
    logs = (
        db.query(MessageLog)
        .filter(MessageLog.invite_id == invite.id)
        .filter(MessageLog.direction == MessageDirection.outbound)
        .all()
    )
    assert any("reminder (1)" in log.body for log in logs)
