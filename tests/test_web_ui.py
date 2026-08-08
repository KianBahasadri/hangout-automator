import re

from app.config import Settings


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
    assert header_row is not None, "New hangout button must sit directly beside the 'Hangouts' heading"


def test_index_lists_hangouts(client, db):
    from app.models import Hangout, HangoutStatus

    active_hangout = Hangout(status=HangoutStatus.active, motive="Board games")
    db.add(active_hangout)
    db.add(Hangout(status=HangoutStatus.closed, motive="Finished dinner"))
    db.add(Hangout(status=HangoutStatus.draft, motive="Movie night"))
    db.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "Board games" in response.text
    assert "Happening Now" in response.text
    assert "Finished dinner" in response.text
    assert "Hangout Over" in response.text
    assert "Movie night" in response.text
    assert f"#{active_hangout.id}" not in response.text


def test_active_hangout_uses_end_label(client, sample_data):
    response = client.get(f"/hangouts/{sample_data['hangouts']['active']}")

    assert response.status_code == 200
    assert "End hangout" in response.text
    assert "Close hangout" not in response.text
    assert "Delete hangout" not in response.text


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

    hangout = Hangout(status=HangoutStatus.draft, motive="Empty setup")
    db.add(hangout)
    db.commit()

    response = client.post(
        f"/hangouts/{hangout.id}/setup",
        data={},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/hangouts/{hangout.id}?error=need_profiles"


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
