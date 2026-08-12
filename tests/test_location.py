"""Hangout location structure (KIAN-500)."""

from __future__ import annotations

from app.location import apply_hangout_location, location_display
from app.messages import format_hangout_summary
from app.models import Hangout, HangoutStatus


def test_apply_hangout_location_text_only():
    hangout = Hangout(status=HangoutStatus.draft)
    apply_hangout_location(hangout, location="  Sam's backyard  ")
    assert hangout.location == "Sam's backyard"
    assert hangout.location_place_id is None
    assert hangout.location_latitude is None
    assert hangout.location_longitude is None
    assert location_display(hangout) == "Sam's backyard"


def test_apply_hangout_location_with_places_structure():
    hangout = Hangout(status=HangoutStatus.draft)
    apply_hangout_location(
        hangout,
        location="Central Park, New York, NY, USA",
        location_place_id="places/ChIJ4zGFAZpYwokRGUGph3Mf37k",
        location_latitude=40.7829,
        location_longitude=-73.9654,
    )
    assert hangout.location == "Central Park, New York, NY, USA"
    assert hangout.location_place_id.startswith("places/")
    assert hangout.location_latitude == 40.7829
    assert hangout.location_longitude == -73.9654


def test_apply_hangout_location_clears_incomplete_coords():
    hangout = Hangout(status=HangoutStatus.draft)
    apply_hangout_location(
        hangout,
        location="Somewhere",
        location_place_id="pid",
        location_latitude=40.0,
        location_longitude=None,
    )
    assert hangout.location_place_id == "pid"
    assert hangout.location_latitude is None
    assert hangout.location_longitude is None


def test_apply_hangout_location_empty_clears_all():
    hangout = Hangout(
        status=HangoutStatus.draft,
        location="Old",
        location_place_id="pid",
        location_latitude=1.0,
        location_longitude=2.0,
    )
    apply_hangout_location(hangout, location="  ")
    assert hangout.location is None
    assert hangout.location_place_id is None
    assert hangout.location_latitude is None
    assert hangout.location_longitude is None


def test_sms_where_uses_display_only():
    hangout = Hangout(
        status=HangoutStatus.draft,
        location="The Spot",
        location_place_id="places/secret",
        location_latitude=40.0,
        location_longitude=-73.0,
        motive="Dinner",
    )
    summary = format_hangout_summary(hangout)
    assert "Where: The Spot" in summary
    assert "places/" not in summary
    assert "40.0" not in summary


def test_web_form_persists_structured_location(client, db):
    response = client.post(
        "/hangouts/new",
        data={
            "location": "Riverside Park, New York, NY, USA",
            "location_place_id": "places/ChIJ_test_place",
            "location_latitude": "40.8006",
            "location_longitude": "-73.9701",
            "motive": "Walk",
            "action": "draft",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    hangout = db.query(Hangout).order_by(Hangout.id.desc()).first()
    assert hangout is not None
    assert hangout.location == "Riverside Park, New York, NY, USA"
    assert hangout.location_place_id == "places/ChIJ_test_place"
    assert hangout.location_latitude == 40.8006
    assert hangout.location_longitude == -73.9701

    edit = client.get(f"/hangouts/{hangout.id}/edit")
    assert edit.status_code == 200
    assert 'name="location_place_id"' in edit.text
    assert "places/ChIJ_test_place" in edit.text
    assert 'name="location_latitude"' in edit.text


def test_web_form_text_only_clears_structure_on_edit(client, db):
    from app.models import Workspace

    workspace_id = db.query(Workspace.id).filter(Workspace.slug == "default").scalar()
    hangout = Hangout(
        status=HangoutStatus.draft,
        location="Old place",
        location_place_id="places/old",
        location_latitude=1.0,
        location_longitude=2.0,
        workspace_id=workspace_id,
    )
    db.add(hangout)
    db.commit()

    response = client.post(
        f"/hangouts/{hangout.id}/edit",
        data={
            "location": "Just a nickname",
            "location_place_id": "",
            "location_latitude": "",
            "location_longitude": "",
            "motive": "Chat",
            "action": "draft",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(hangout)
    assert hangout.location == "Just a nickname"
    assert hangout.location_place_id is None
    assert hangout.location_latitude is None
    assert hangout.location_longitude is None


def test_api_create_and_patch_location(client, db):
    created = client.post(
        "/api/hangouts",
        json={
            "location": "Park",
            "location_place_id": "places/abc",
            "location_latitude": 40.7,
            "location_longitude": -74.0,
            "motive": "Hang",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["location"] == "Park"
    assert body["location_place_id"] == "places/abc"
    assert body["location_latitude"] == 40.7
    assert body["location_longitude"] == -74.0
    hangout_id = body["id"]

    patched = client.patch(
        f"/api/hangouts/{hangout_id}",
        json={"location": "Nickname only"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["location"] == "Nickname only"
    assert body["location_place_id"] is None
    assert body["location_latitude"] is None
    assert body["location_longitude"] is None

    full = client.patch(
        f"/api/hangouts/{hangout_id}",
        json={
            "location": "Full again",
            "location_place_id": "places/xyz",
            "location_latitude": 41.0,
            "location_longitude": -75.0,
        },
    )
    assert full.status_code == 200
    body = full.json()
    assert body["location_place_id"] == "places/xyz"
    assert body["location_latitude"] == 41.0
