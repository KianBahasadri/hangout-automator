import re

from app.config import Settings


def _ws_id(db):
    from app.models import Workspace

    return db.query(Workspace.id).filter(Workspace.slug == "default").scalar()


def test_index_new_hangout_button_in_header_row(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert '<div class="actions"' not in html
    header_row = re.search(
        r'<div style="[^"]*display:flex;gap:1rem[^"]*">'
        r"\s*<h1[^>]*>Hangouts</h1>"
        r'\s*<a class="btn" href="/hangouts/new">New hangout</a>'
        r"\s*</div>",
        html,
    )
    assert header_row is not None, (
        "New hangout button must sit directly beside the 'Hangouts' heading"
    )


def test_index_lists_hangouts(client, db):
    from app.models import Hangout, HangoutStatus

    workspace_id = _ws_id(db)
    active_hangout = Hangout(
        status=HangoutStatus.active, motive="Board games", workspace_id=workspace_id
    )
    db.add(active_hangout)
    db.add(
        Hangout(status=HangoutStatus.closed, motive="Finished dinner", workspace_id=workspace_id)
    )
    db.add(Hangout(status=HangoutStatus.draft, motive="Movie night", workspace_id=workspace_id))
    db.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "Board games" in response.text
    assert "Happening Now" in response.text
    assert "Finished dinner" in response.text
    assert "Hangout Over" in response.text
    assert "Movie night" in response.text
    assert f"#{active_hangout.id}" not in response.text


def test_hangout_ui_humanizes_date_time_duration(client, db):
    from app.models import Hangout, HangoutStatus

    hangout = Hangout(
        status=HangoutStatus.active,
        motive="Dinner",
        day_date="2026-08-15",
        time="19:00",
        duration="3",
        workspace_id=_ws_id(db),
    )
    db.add(hangout)
    db.commit()

    index = client.get("/")
    assert index.status_code == 200
    assert "August 15, 2026" in index.text
    assert "7:00 PM" in index.text
    assert "3 hours" in index.text
    assert "2026-08-15" not in index.text
    assert "19:00" not in index.text

    detail = client.get(f"/hangouts/{hangout.id}")
    assert detail.status_code == 200
    assert "August 15, 2026" in detail.text
    assert "7:00 PM" in detail.text
    assert "3 hours" in detail.text


def test_draft_hangout_opens_a_prefilled_edit_form(client, db):
    from app.models import Hangout, HangoutInvite, HangoutStatus, Profile, YesNo

    workspace_id = _ws_id(db)
    organizer = Profile(name="Sam Rivera", phone="+15551110001", workspace_id=workspace_id)
    db.add(organizer)
    db.flush()
    hangout = Hangout(
        status=HangoutStatus.draft,
        workspace_id=workspace_id,
        day_date="2026-08-15",
        time="19:30",
        duration="2.5",
        location="Central Park",
        motive="Picnic",
        alcohol_involved=YesNo.yes,
        weed_involved=YesNo.no,
        notes="Bring a blanket",
        organizer_profile_id=organizer.id,
        organizer_phone=organizer.phone,
        notify_enabled=True,
        notify_interval=True,
        notify_threshold=True,
        notify_interval_hours=8,
        notify_interval_only_if_changed=False,
        notify_on_new_confirm=False,
        notify_on_decline=True,
        notify_on_allergy=False,
        notify_on_ride_needed=True,
        notify_confirm_goal=4,
        notify_threshold_cooldown_minutes=15,
    )
    db.add(hangout)
    db.flush()
    db.add(HangoutInvite(hangout_id=hangout.id, profile_id=organizer.id, workspace_id=workspace_id))
    db.commit()

    home = client.get("/")
    assert f'href="/hangouts/{hangout.id}/edit"' in home.text

    redirect = client.get(f"/hangouts/{hangout.id}", follow_redirects=False)

    assert redirect.status_code == 303
    assert redirect.headers["location"] == f"/hangouts/{hangout.id}/edit"

    response = client.get(f"/hangouts/{hangout.id}/edit")

    assert response.status_code == 200
    html = response.text
    assert "<title>Edit hangout · Hangout Automator</title>" in html
    assert f'action="/hangouts/{hangout.id}/edit"' in html
    assert 'name="day_date" type="date" value="2026-08-15"' in html
    assert 'name="time" type="time" value="19:30"' in html
    assert 'name="duration" type="number" min="0" step="0.5" value="2.5"' in html
    assert 'name="location"' in html and 'value="Central Park"' in html
    assert (
        'name="motive" type="text" placeholder="Dinner, game night, beach…" value="Picnic"' in html
    )
    assert '<option value="yes" selected>yes</option>' in html
    assert '<option value="no" selected>no</option>' in html
    assert "Bring a blanket" in html
    assert re.search(rf'name="profile_ids" value="{organizer.id}"\s+checked', html)
    # Organizer SMS is configured on My Profile, not on this form.
    assert "Organizer SMS" not in html
    assert 'name="notify_enabled"' not in html
    assert "Save changes" not in html
    assert "Save draft" not in html
    assert "data-draft-autosave" in html
    assert "data-draft-autosave-status" in html
    assert "hangout_draft_autosave.js" in html


def test_draft_edit_saves_updated_details_and_invitees(client, db):
    from app.models import Hangout, HangoutInvite, HangoutStatus, Profile, YesNo

    workspace_id = _ws_id(db)
    old_invitee = Profile(name="Taylor", phone="+15551110001", workspace_id=workspace_id)
    organizer = Profile(name="Morgan", phone="+15551110002", workspace_id=workspace_id)
    db.add_all([old_invitee, organizer])
    db.flush()
    hangout = Hangout(status=HangoutStatus.draft, motive="Old plan", workspace_id=workspace_id)
    db.add(hangout)
    db.flush()
    db.add(
        HangoutInvite(hangout_id=hangout.id, profile_id=old_invitee.id, workspace_id=workspace_id)
    )
    db.commit()

    hangout.notify_enabled = True
    hangout.notify_interval_hours = 12
    hangout.organizer_profile_id = organizer.id
    hangout.organizer_phone = organizer.phone
    db.commit()

    response = client.post(
        f"/hangouts/{hangout.id}/edit",
        data={
            "day_date": "2026-08-16",
            "time": "18:15",
            "duration": "4",
            "location": "Riverside Park",
            "motive": "Updated plan",
            "alcohol_involved": "no",
            "weed_involved": "yes",
            "notes": "Meet at the south gate",
            "profile_ids": str(organizer.id),
            "action": "draft",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout.id}/edit"

    db.refresh(hangout)
    assert hangout.status == HangoutStatus.draft
    assert hangout.day_date == "2026-08-16"
    assert hangout.time == "18:15"
    assert hangout.duration == "4"
    assert hangout.location == "Riverside Park"
    assert hangout.motive == "Updated plan"
    assert hangout.alcohol_involved == YesNo.no
    assert hangout.weed_involved == YesNo.yes
    assert hangout.notes == "Meet at the south gate"
    # Organizer SMS stamped at create is preserved across detail-only edits.
    assert hangout.organizer_profile_id == organizer.id
    assert hangout.organizer_phone == organizer.phone
    assert hangout.notify_enabled is True
    assert hangout.notify_interval_hours == 12
    assert {invite.profile_id for invite in hangout.invites} == {organizer.id}


def test_draft_edit_autosave_returns_no_content(client, db):
    from app.models import Hangout, HangoutStatus

    hangout = Hangout(status=HangoutStatus.draft, motive="Before", workspace_id=_ws_id(db))
    db.add(hangout)
    db.commit()

    response = client.post(
        f"/hangouts/{hangout.id}/edit",
        data={"motive": "Autosaved", "action": "draft"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 204
    db.refresh(hangout)
    assert hangout.motive == "Autosaved"


def test_draft_autosave_uses_the_form_action_attribute(client):
    script = client.get("/static/hangout_draft_autosave.js")

    assert script.status_code == 200
    assert 'form.getAttribute("action")' in script.text
    assert "fetch(saveUrl" in script.text
    assert "fetch(form.action" not in script.text
    assert 'window.__hangoutToast.show("Draft saved")' in script.text
    assert 'setStatus("Saved"' not in script.text


def test_draft_edit_can_set_up_the_saved_invitees(client, db):
    from app.models import Hangout, HangoutStatus, Profile

    workspace_id = _ws_id(db)
    invitee = Profile(name="Casey", phone="+15551110001", workspace_id=workspace_id)
    hangout = Hangout(status=HangoutStatus.draft, motive="Dinner", workspace_id=workspace_id)
    db.add_all([invitee, hangout])
    db.commit()

    response = client.post(
        f"/hangouts/{hangout.id}/edit",
        data={
            "motive": "Dinner",
            "profile_ids": str(invitee.id),
            "action": "setup",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout.id}"
    db.refresh(hangout)
    assert hangout.status == HangoutStatus.active
    assert {invite.profile_id for invite in hangout.invites} == {invitee.id}


def test_active_hangout_uses_end_label(client, sample_data):
    response = client.get(f"/hangouts/{sample_data['hangouts']['active']}")

    assert response.status_code == 200
    assert "End hangout" in response.text
    assert "Close hangout" not in response.text
    assert "Delete hangout" not in response.text


def test_non_draft_hangout_edit_route_returns_to_detail(client, sample_data):
    hangout_id = sample_data["hangouts"]["active"]

    response = client.get(f"/hangouts/{hangout_id}/edit", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout_id}"

    update = client.post(
        f"/hangouts/{hangout_id}/edit",
        data={"motive": "Should not change"},
        follow_redirects=False,
    )
    assert update.status_code == 303
    assert update.headers["location"] == f"/hangouts/{hangout_id}"


def test_closed_hangout_shows_delete_option(client, sample_data):
    response = client.get(f"/hangouts/{sample_data['hangouts']['closed']}")

    assert response.status_code == 200
    assert "Delete hangout" in response.text
    assert "End hangout" not in response.text
    assert "confirm(" not in response.text


def test_soft_delete_hides_closed_hangout_from_home(client, db, sample_data):
    from app.models import Hangout

    hangout_id = sample_data["hangouts"]["closed"]
    hangout = db.get(Hangout, hangout_id)
    assert hangout is not None
    motive = hangout.motive or "Closed plans"

    response = client.post(f"/hangouts/{hangout_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?toast=hangout_deleted"

    home = client.get("/")
    assert home.status_code == 200
    assert motive not in home.text or f"/hangouts/{hangout_id}" not in home.text

    db.refresh(hangout)
    assert hangout.deleted_at is not None

    deleted_page = client.get("/settings/deleted-hangouts")
    assert deleted_page.status_code == 200
    assert motive in deleted_page.text
    assert f"/hangouts/{hangout_id}" in deleted_page.text


def test_soft_delete_rejects_active_hangout(client, db, sample_data):
    from app.models import Hangout

    hangout_id = sample_data["hangouts"]["active"]
    response = client.post(f"/hangouts/{hangout_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout_id}"

    hangout = db.get(Hangout, hangout_id)
    assert hangout is not None
    assert hangout.deleted_at is None


def test_deleted_hangouts_search_by_motive(client, db, sample_data):
    from app.models import Hangout
    from app.services import utcnow

    hangout = db.get(Hangout, sample_data["hangouts"]["closed"])
    hangout.motive = "Secret picnic"
    hangout.deleted_at = utcnow()
    db.commit()

    match = client.get("/settings/deleted-hangouts", params={"q": "picnic"})
    assert match.status_code == 200
    assert "Secret picnic" in match.text

    miss = client.get("/settings/deleted-hangouts", params={"q": "zzz-no-match"})
    assert miss.status_code == 200
    assert "Secret picnic" not in miss.text


def test_restore_brings_hangout_back_to_home(client, db, sample_data):
    from app.models import Hangout
    from app.services import utcnow

    hangout_id = sample_data["hangouts"]["closed"]
    hangout = db.get(Hangout, hangout_id)
    hangout.motive = "Restore me"
    hangout.deleted_at = utcnow()
    db.commit()

    response = client.post(f"/hangouts/{hangout_id}/restore", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout_id}"

    db.refresh(hangout)
    assert hangout.deleted_at is None
    assert "Restore me" in client.get("/").text


def test_new_hangout_without_invitees_redirects_instead_of_500(client):
    response = client.post(
        "/hangouts/new",
        data={"action": "setup"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("?error=need_profiles")


def test_existing_hangout_without_invitees_redirects_instead_of_500(client, db):
    from app.models import Hangout, HangoutStatus

    hangout = Hangout(status=HangoutStatus.draft, motive="Empty setup", workspace_id=_ws_id(db))
    db.add(hangout)
    db.commit()

    response = client.post(
        f"/hangouts/{hangout.id}/setup",
        data={},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout.id}?error=need_profiles"

    edit = client.get(response.headers["location"])
    assert edit.status_code == 200
    assert "Select at least one profile before setting up the hangout." in edit.text
    assert f'action="/hangouts/{hangout.id}/edit"' in edit.text


def test_settings_links_to_log_download(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert 'href="/settings/logs"' in response.text
    assert 'href="/settings/sms-simulator"' in response.text
    assert 'href="/settings/deleted-hangouts"' in response.text
    assert "Dietary Restrictions" in response.text
    assert "Food allergies" not in response.text
    # Defaults seeded on init_db
    assert "meat" in response.text
    assert "pork" in response.text


def test_sms_simulator_page_renders_sample_messages(client):
    response = client.get("/settings/sms-simulator")
    assert response.status_code == 200
    assert "SMS simulator" in response.text
    # Apostrophe is HTML-escaped in the preformatted body.
    assert "invited:" in response.text
    assert "MORE INFO" in response.text
    assert "Organizer digest" in response.text


def test_new_hangout_has_preview_invite_button(client):
    response = client.get("/hangouts/new")
    assert response.status_code == 200
    assert 'id="preview-invite-sms"' in response.text
    assert "Preview invite SMS" in response.text
    assert "hangout_sms_preview.js" in response.text


def test_new_hangout_keeps_manual_draft_save(client):
    response = client.get("/hangouts/new")

    assert response.status_code == 200
    assert "Save draft" in response.text
    assert "data-draft-autosave" not in response.text


def test_new_hangout_has_back_button_to_hangout_list(client):
    response = client.get("/hangouts/new")

    assert response.status_code == 200
    assert '<a class="btn btn-secondary btn-sm" href="/">← All hangouts</a>' in response.text


def test_new_hangout_enables_places_when_configured(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.web.get_settings",
        lambda: Settings(google_maps_api_key="places-test-key", _env_file=None),
    )

    response = client.get("/hangouts/new")

    assert response.status_code == 200
    assert 'id="location-autocomplete"' in response.text
    assert 'class="location-field"' in response.text
    assert 'data-places-enabled="true"' in response.text
    assert 'id="location-status"' in response.text
    assert "location_autocomplete.js" in response.text


def test_header_has_theme_toggle_after_settings(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="theme-toggle"' in html
    assert "__hangoutTheme" in html
    assert 'data-theme="dark"' in html
    assert 'html[data-theme="light"]' in client.get("/static/style.css").text
    # Toggle is the control immediately after the Settings link.
    assert re.search(
        r'href="/settings">Settings</a>\s*'
        r"<button\b[^>]*\bid=\"theme-toggle\"",
        html,
    )


def test_settings_logs_download_returns_file(client, tmp_path, monkeypatch):
    # Point at a standalone file (not the live audit stream) so concurrent
    # request logging does not append extra lines mid-assertion.
    log_path = tmp_path / "download-me.log"
    log_path.write_text('{"event":"test"}\n', encoding="utf-8")
    monkeypatch.setattr(
        "app.routers.web.get_settings",
        lambda: Settings(log_file=str(log_path), _env_file=None),
    )

    response = client.get("/settings/logs")

    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "").lower()
    assert "download-me.log" in response.headers.get("content-disposition", "")
    assert response.content == b'{"event":"test"}\n'


def test_settings_logs_download_missing_file_is_404(client, tmp_path, monkeypatch):
    missing = tmp_path / "no-such" / "server.log"
    monkeypatch.setattr(
        "app.routers.web.get_settings",
        lambda: Settings(log_file=str(missing), _env_file=None),
    )

    response = client.get("/settings/logs")

    assert response.status_code == 404


def test_delete_confirmations_survive_an_apostrophe(client, db):
    """Names with apostrophes must not break out of the confirm() string literal.

    Rendering the message into `onsubmit="return confirm('…')"` meant Jinja's
    HTML escaping turned O'Brien into O&#39;Brien, which the parser decoded back
    to a bare quote — the handler then failed to parse and the form deleted the
    row with no prompt at all.
    """
    from app.models import Allergy, Profile, Tag

    workspace_id = _ws_id(db)
    db.add(Profile(name="Kian O'Brien", phone="+15551234567", workspace_id=workspace_id))
    db.add(Tag(name="Sam's crew", workspace_id=workspace_id))
    db.add(Allergy(name="Cow's milk", workspace_id=workspace_id))
    db.commit()

    for path in ("/profiles", "/settings"):
        html = client.get(path).text
        assert "onsubmit" not in html, f"{path} still builds JS by string interpolation"
        assert "confirm('" not in html

    profiles_html = client.get("/profiles").text
    assert 'data-confirm="Delete Kian O&#39;Brien?"' in profiles_html
    assert 'data-confirm="Delete tag Sam&#39;s crew?"' in profiles_html
    assert 'data-confirm="Delete restriction Cow&#39;s milk?"' in client.get("/settings").text


def test_confirm_script_is_loaded_on_every_page(client):
    assert '<script src="/static/confirm.js"></script>' in client.get("/").text
