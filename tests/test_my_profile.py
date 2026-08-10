"""My Profile (account-holder) settings and hangout prefill."""

from app.models import Hangout, HangoutStatus, Profile, User
from app.users import LOCAL_USER_ID, get_or_create_user


def test_my_profile_page_loads(client):
    response = client.get("/me")
    assert response.status_code == 200
    assert "My Profile" in response.text
    assert "Display name" not in response.text
    assert 'placeholder="Your name"' in response.text
    assert "Default organizer SMS" in response.text
    # No instructional help paragraphs under headings.
    assert "personal account settings" not in response.text.lower()
    assert "prefills new hangouts" not in response.text.lower()


def test_my_profile_saves_name_phone_and_notify_defaults(client, db):
    response = client.post(
        "/me",
        data={
            "display_name": "Alex Organizer",
            "phone": "5551234567",
            "notify_enabled": "on",
            "notify_interval": "on",
            "notify_threshold": "on",
            "notify_interval_hours": "3",
            "notify_interval_only_if_changed": "on",
            "notify_on_new_confirm": "on",
            "notify_on_allergy": "on",
            "notify_confirm_goal": "4",
            "notify_threshold_cooldown_minutes": "15",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/me?notice=saved"

    user = db.query(User).filter(User.clerk_user_id == LOCAL_USER_ID).one()
    assert user.display_name == "Alex Organizer"
    assert user.phone == "+15551234567"
    assert user.phone_verified_at is None
    assert user.default_notify_enabled is True
    assert user.default_notify_interval is True
    assert user.default_notify_threshold is True
    assert user.default_notify_interval_hours == 3
    assert user.default_notify_on_decline is False
    assert user.default_notify_confirm_goal == 4
    assert user.default_notify_threshold_cooldown_minutes == 15


def test_my_profile_rejects_invalid_phone(client, db):
    client.post(
        "/me",
        data={"display_name": "Alex", "phone": "5551112222", "notify_interval_hours": "6"},
        follow_redirects=False,
    )
    response = client.post(
        "/me",
        data={"display_name": "Alex", "phone": "abc", "notify_interval_hours": "6"},
    )
    assert response.status_code == 400
    assert "does not look dialable" in response.text
    user = db.query(User).filter(User.clerk_user_id == LOCAL_USER_ID).one()
    assert user.phone == "+15551112222"


def test_changing_phone_clears_verification(client, db):
    user = get_or_create_user(db, LOCAL_USER_ID)
    user.phone = "+15551110000"
    from datetime import datetime, timezone

    user.phone_verified_at = datetime.now(timezone.utc)
    db.commit()

    client.post(
        "/me",
        data={
            "display_name": "Alex",
            "phone": "5559998888",
            "notify_interval_hours": "6",
        },
        follow_redirects=False,
    )
    db.refresh(user)
    assert user.phone == "+15559998888"
    assert user.phone_verified_at is None


def test_users_are_isolated_by_clerk_subject(db):
    a = get_or_create_user(db, "user_a")
    b = get_or_create_user(db, "user_b")
    a.display_name = "Alice"
    a.phone = "+15551111111"
    b.display_name = "Bob"
    b.phone = "+15552222222"
    db.commit()

    a2 = get_or_create_user(db, "user_a")
    b2 = get_or_create_user(db, "user_b")
    assert a2.display_name == "Alice"
    assert a2.phone == "+15551111111"
    assert b2.display_name == "Bob"
    assert b2.phone == "+15552222222"
    assert a2.id != b2.id


def test_new_hangout_page_has_no_organizer_sms_section(client, db):
    page = client.get("/hangouts/new")
    assert page.status_code == 200
    assert "Organizer SMS" not in page.text
    assert 'name="notify_enabled"' not in page.text
    assert 'name="organizer_profile_id"' not in page.text


def test_creating_hangout_stamps_my_profile_defaults(client, db, workspace):
    client.post(
        "/me",
        data={
            "display_name": "Alex",
            "phone": "5551234567",
            "notify_enabled": "on",
            "notify_interval": "on",
            "notify_interval_hours": "8",
            "notify_confirm_goal": "5",
        },
        follow_redirects=False,
    )
    me_contact = Profile(name="Alex Contact", phone="+15551234567", workspace_id=workspace.id)
    db.add(me_contact)
    db.commit()

    response = client.post(
        "/hangouts/new",
        data={
            "motive": "Game night",
            "action": "draft",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    hangout = db.query(Hangout).filter(Hangout.motive == "Game night").one()
    assert hangout.status == HangoutStatus.draft
    assert hangout.organizer_profile_id == me_contact.id
    assert hangout.organizer_phone == "+15551234567"
    assert hangout.notify_enabled is True
    assert hangout.notify_interval is True
    assert hangout.notify_interval_hours == 8
    assert hangout.notify_confirm_goal == 5


def test_creating_hangout_stamps_phone_without_matching_contact(client, db):
    client.post(
        "/me",
        data={
            "display_name": "Alex",
            "phone": "5551234567",
            "notify_enabled": "on",
            "notify_interval": "on",
            "notify_interval_hours": "6",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/hangouts/new",
        data={"motive": "No contact match", "action": "draft"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    hangout = db.query(Hangout).filter(Hangout.motive == "No contact match").one()
    assert hangout.organizer_profile_id is None
    assert hangout.organizer_phone == "+15551234567"
    assert hangout.notify_enabled is True


def test_notify_stays_off_without_my_profile_phone(client, db):
    response = client.post(
        "/hangouts/new",
        data={"motive": "No phone", "action": "draft"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    hangout = db.query(Hangout).filter(Hangout.motive == "No phone").one()
    assert hangout.notify_enabled is False
