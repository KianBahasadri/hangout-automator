"""Concurrent sweeps must not double-send.

Two layers of locking make N workers behave like one: the advisory lock around
each tick (app/locks.py) and FOR UPDATE SKIP LOCKED row claiming inside the
sweeps (app/services.py). These tests exercise the row layer directly with two
threads running the same sweep against the same database.
"""

import contextlib
from datetime import timedelta
from threading import Barrier, Thread

from sqlalchemy.orm import Session

from app.database import SessionLocal
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
from app.services import process_followups, utcnow


def _default_workspace_id(db: Session) -> int:
    return db.query(Workspace.id).filter(Workspace.slug == "default").scalar()


def _seed_due_invites(db: Session, n: int) -> list[int]:
    """n invites in one active hangout, all due for their first follow-up."""
    ws = _default_workspace_id(db)
    hangout = Hangout(status=HangoutStatus.active, motive="Concurrent test", workspace_id=ws)
    db.add(hangout)
    db.flush()
    invite_ids: list[int] = []
    for index in range(n):
        profile = Profile(name=f"Person {index}", phone=f"+1555222{index:04d}", workspace_id=ws)
        db.add(profile)
        db.flush()
        invite = HangoutInvite(
            hangout_id=hangout.id,
            profile_id=profile.id,
            status=InviteStatus.pending,
            last_outbound_at=utcnow() - timedelta(hours=5),
            workspace_id=ws,
        )
        db.add(invite)
        db.flush()
        invite_ids.append(invite.id)
    db.commit()
    return invite_ids


def _outbound_success_count(db: Session) -> int:
    return (
        db.query(MessageLog)
        .filter(
            MessageLog.direction == MessageDirection.outbound,
            MessageLog.success.is_(True),
        )
        .count()
    )


def _run_sweep(session: Session, barrier: Barrier, results: list[int]) -> None:
    barrier.wait()
    results.append(process_followups(session))
    session.close()


def test_concurrent_sweeps_send_each_due_invite_exactly_once(db):
    n = 5
    _seed_due_invites(db, n)

    results: list[int] = []
    barrier = Barrier(2)
    sessions = [SessionLocal(), SessionLocal()]
    threads = [Thread(target=_run_sweep, args=(sessions[i], barrier, results)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "sweep thread did not finish"

    # The work may split between the sweeps in any disjoint partition, but
    # each due invite is claimed by exactly one of them.
    assert sum(results) == n, f"sweeps reported {results}, expected {n} sends total"
    # Exactly n successful outbound sends — not 2n.
    check = SessionLocal()
    try:
        assert _outbound_success_count(check) == n
    finally:
        check.close()


def test_failure_after_send_does_not_duplicate_on_next_tick(db, monkeypatch):
    """A crash between send and commit must not re-send: the clock was already
    committed before dispatch."""
    from app import services

    _seed_due_invites(db, 1)

    real_send_sms = services.send_sms

    def crash_after_send(db, **kwargs):
        real_send_sms(db, **kwargs)  # the SMS is sent and logged...
        raise RuntimeError("simulated crash after send, before commit")

    monkeypatch.setattr(services, "send_sms", crash_after_send)

    first = SessionLocal()
    second = SessionLocal()
    try:
        with _expect_crash():
            process_followups(first)
        # Next tick: the invite's clock was already advanced, so nothing is due.
        assert process_followups(second) == 0
    finally:
        first.close()
        second.close()

    check = SessionLocal()
    try:
        assert _outbound_success_count(check) == 1, "exactly one send despite the crash"
    finally:
        check.close()


@contextlib.contextmanager
def _expect_crash():
    try:
        yield
    except RuntimeError as exc:
        assert "simulated crash" in str(exc)
    else:
        raise AssertionError("expected the simulated crash to propagate")
