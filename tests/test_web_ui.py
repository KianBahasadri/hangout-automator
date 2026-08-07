import re


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
