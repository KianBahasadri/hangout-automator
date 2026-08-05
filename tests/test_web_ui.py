import re


def test_index_new_hangout_button_in_header_row(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert '<div class="actions"' not in html
    header_row = re.search(
        r'<div style="[^"]*display:flex;justify-content:space-between[^"]*">'
        r"\s*<h1[^>]*>Hangouts</h1>"
        r'\s*<a class="btn" href="/hangouts/new">New hangout</a>'
        r"\s*</div>",
        html,
    )
    assert header_row is not None, "New hangout button must sit in the 'Hangouts' header row"


def test_index_lists_hangouts(client, db):
    from app.models import Hangout, HangoutStatus

    db.add(Hangout(status=HangoutStatus.active, motive="Board games"))
    db.add(Hangout(status=HangoutStatus.draft, motive="Movie night"))
    db.commit()

    response = client.get("/")
    assert response.status_code == 200
    assert "Board games" in response.text
    assert "Movie night" in response.text
