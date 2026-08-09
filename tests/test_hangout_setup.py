from app.models import Hangout, HangoutInvite, HangoutStatus, InviteStatus, Profile, Workspace
from app.services import load_hangout, process_followups, setup_hangout


def _workspace(db):
    return db.query(Workspace).filter(Workspace.slug == "default").one()


def _draft_with_invites(db, *names):
    workspace = _workspace(db)
    hangout = Hangout(status=HangoutStatus.draft, motive="Beach day", workspace_id=workspace.id)
    db.add(hangout)
    db.flush()
    profiles = []
    for index, name in enumerate(names):
        profile = Profile(name=name, phone=f"+1555111{index:04d}", workspace_id=workspace.id)
        db.add(profile)
        db.flush()
        db.add(
            HangoutInvite(
                hangout_id=hangout.id,
                profile_id=profile.id,
                workspace_id=workspace.id,
            )
        )
        profiles.append(profile)
    db.commit()
    return hangout, profiles


def test_setup_sends_to_the_selected_profiles(db):
    hangout, (sam, lee) = _draft_with_invites(db, "Sam", "Lee")
    workspace = _workspace(db)

    setup_hangout(db, load_hangout(db, hangout.id, workspace), [sam.id, lee.id], workspace)

    invites = load_hangout(db, hangout.id, workspace).invites
    assert {inv.profile_id for inv in invites} == {sam.id, lee.id}
    assert all(inv.last_outbound_at is not None for inv in invites)


def test_deselected_uninvited_rows_are_dropped(db):
    """Otherwise they show as "pending" forever: nothing was sent, so the
    follow-up job has no clock to measure them against and skips them."""
    hangout, (sam, lee) = _draft_with_invites(db, "Sam", "Lee")
    workspace = _workspace(db)

    setup_hangout(db, load_hangout(db, hangout.id, workspace), [sam.id], workspace)

    invites = load_hangout(db, hangout.id, workspace).invites
    assert [inv.profile_id for inv in invites] == [sam.id]

    process_followups(db)
    assert db.query(HangoutInvite).filter(HangoutInvite.profile_id == lee.id).count() == 0


def test_already_messaged_invites_survive_a_narrower_selection(db):
    hangout, (sam, lee) = _draft_with_invites(db, "Sam", "Lee")
    workspace = _workspace(db)
    setup_hangout(db, load_hangout(db, hangout.id, workspace), [sam.id, lee.id], workspace)

    # Re-running setup for one person must not discard the other's answer.
    setup_hangout(db, load_hangout(db, hangout.id, workspace), [sam.id], workspace)

    invites = {inv.profile_id: inv for inv in load_hangout(db, hangout.id, workspace).invites}
    assert set(invites) == {sam.id, lee.id}
    assert invites[lee.id].status == InviteStatus.pending
