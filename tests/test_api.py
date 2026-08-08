import pytest


def _places_settings():
    from app.config import Settings

    return Settings(google_maps_api_key="places-test-key", sms_provider="mock", _env_file=None)


def _no_places_settings():
    from app.config import Settings

    return Settings(google_maps_api_key="", sms_provider="mock", _env_file=None)


def _create_profile(client, name="Sam", phone="+15551112222"):
    response = client.post("/api/profiles", json={"name": name, "phone": phone})
    assert response.status_code == 201
    return response.json()


def _create_hangout(client, payload=None):
    response = client.post("/api/hangouts", json=payload or {})
    assert response.status_code == 201
    return response.json()


def test_preview_invite_sms_uses_form_fields(client_no_raise):
    response = client_no_raise.post(
        "/api/sms/preview-invite",
        json={
            "recipient_name": "Sam",
            "motive": "Dinner",
            "day_date": "2026-08-15",
            "time": "19:00",
            "location": "Sam's place",
            "alcohol_involved": "yes",
        },
    )
    assert response.status_code == 200
    body = response.json()["body"]
    assert "Hey Sam!" in body
    assert "You're invited:" in body
    assert "Dinner" in body
    assert "When: August 15, 2026 at 7:00 PM" in body
    assert "Where: Sam's place" in body
    assert "Alcohol: yes" in body
    assert "MORE INFO" in body


def test_places_autocomplete_returns_renderable_place_predictions(client_no_raise, monkeypatch):
    calls = {}
    place_id = "ChIJ" + "x" * 300

    async def fake_google_request(method, url, **kwargs):
        calls.update(method=method, url=url, kwargs=kwargs)
        return {
            "suggestions": [
                {
                    "placePrediction": {
                        "placeId": place_id,
                        "text": {"text": "Central Park, New York, NY, USA"},
                        "structuredFormat": {
                            "mainText": {"text": "Central Park"},
                            "secondaryText": {"text": "New York, NY, USA"},
                        },
                    }
                },
                {"queryPrediction": {"text": {"text": "parks near me"}}},
            ]
        }

    monkeypatch.setattr("app.routers.api.get_settings", _places_settings)
    monkeypatch.setattr("app.routers.api._google_places_request", fake_google_request)

    response = client_no_raise.get(
        "/api/places/autocomplete",
        params={"input": "central", "session_token": "session-123"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "suggestions": [
            {
                "place_id": place_id,
                "text": "Central Park, New York, NY, USA",
                "main_text": "Central Park",
                "secondary_text": "New York, NY, USA",
            }
        ]
    }
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/places:autocomplete")
    assert calls["kwargs"]["field_mask"] == (
        "suggestions.placePrediction.placeId,"
        "suggestions.placePrediction.text.text,"
        "suggestions.placePrediction.structuredFormat.mainText.text,"
        "suggestions.placePrediction.structuredFormat.secondaryText.text"
    )
    assert calls["kwargs"]["json_body"] == {
        "input": "central",
        "sessionToken": "session-123",
    }


def test_place_details_returns_address_and_coordinates(client_no_raise, monkeypatch):
    calls = {}

    async def fake_google_request(method, url, **kwargs):
        calls.update(method=method, url=url, kwargs=kwargs)
        return {
            "id": "ChIJexample_123",
            "formattedAddress": "Central Park, New York, NY 10022, USA",
            "location": {"latitude": 40.7829, "longitude": -73.9654},
        }

    monkeypatch.setattr("app.routers.api.get_settings", _places_settings)
    monkeypatch.setattr("app.routers.api._google_places_request", fake_google_request)

    response = client_no_raise.get(
        "/api/places/details",
        params={"place_id": "ChIJexample_123", "session_token": "session-123"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "place_id": "ChIJexample_123",
        "formatted_address": "Central Park, New York, NY 10022, USA",
        "latitude": 40.7829,
        "longitude": -73.9654,
    }
    assert calls["method"] == "GET"
    assert calls["url"].endswith("/places/ChIJexample_123?sessionToken=session-123")
    assert calls["kwargs"]["field_mask"] == (
        "id,formattedAddress,location.latitude,location.longitude"
    )


def test_place_details_rejects_path_like_place_ids(client_no_raise, monkeypatch):
    calls = []

    async def fake_google_request(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr("app.routers.api.get_settings", _places_settings)
    monkeypatch.setattr("app.routers.api._google_places_request", fake_google_request)

    response = client_no_raise.get(
        "/api/places/details", params={"place_id": "places/ChIJexample_123"}
    )

    assert response.status_code == 422
    assert calls == []


def test_places_routes_are_disabled_without_a_key(client_no_raise, monkeypatch):
    monkeypatch.setattr("app.routers.api.get_settings", _no_places_settings)

    response = client_no_raise.get(
        "/api/places/autocomplete", params={"input": "central"}
    )

    assert response.status_code == 404


def test_default_dietary_restrictions_are_seeded(client_no_raise):
    response = client_no_raise.get("/api/allergies")
    assert response.status_code == 200
    names = {row["name"].lower() for row in response.json()}
    assert "meat" in names
    assert "pork" in names


def test_deleted_default_dietary_restriction_stays_gone(client_no_raise):
    """Deleting a default must not come back on the next init_db()."""
    from app.database import init_db

    listed = client_no_raise.get("/api/allergies")
    assert listed.status_code == 200
    meat = next(row for row in listed.json() if row["name"].lower() == "meat")

    deleted = client_no_raise.delete(f"/api/allergies/{meat['id']}")
    assert deleted.status_code == 204

    init_db()

    after = client_no_raise.get("/api/allergies")
    assert after.status_code == 200
    names = {row["name"].lower() for row in after.json()}
    assert "meat" not in names
    assert "pork" in names


def test_preview_invite_sms_formats_duration_with_hours(client_no_raise):
    response = client_no_raise.post(
        "/api/sms/preview-invite",
        json={
            "recipient_name": "Sam",
            "motive": "Dinner",
            "day_date": "2026-08-15",
            "time": "19:00",
            "duration": "3",
            "location": "Sam's place",
        },
    )
    assert response.status_code == 200
    body = response.json()["body"]
    assert "When: August 15, 2026 at 7:00 PM (3 hours)" in body


def test_empty_hangout_payload_creates_a_draft(client_no_raise):
    hangout = _create_hangout(client_no_raise)

    assert hangout["status"] == "draft"
    assert hangout["day_date"] is None
    assert hangout["motive"] is None
    assert hangout["location"] is None
    assert hangout["invites"] == []


def test_hangout_location_round_trips_via_api(client_no_raise):
    hangout = _create_hangout(
        client_no_raise,
        {"motive": "Dinner", "location": "  123 Main St  "},
    )
    assert hangout["location"] == "123 Main St"

    updated = client_no_raise.patch(
        f"/api/hangouts/{hangout['id']}",
        json={"location": "Park picnic table"},
    )
    assert updated.status_code == 200
    assert updated.json()["location"] == "Park picnic table"

    cleared = client_no_raise.patch(
        f"/api/hangouts/{hangout['id']}",
        json={"location": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["location"] is None


def test_hangout_location_appears_in_invite_sms(client_no_raise, db):
    from app.models import MessageLog

    profile = _create_profile(client_no_raise)
    hangout = _create_hangout(
        client_no_raise,
        {
            "motive": "Dinner",
            "location": "Sam's place",
            "profile_ids": [profile["id"]],
        },
    )
    response = client_no_raise.post(
        f"/api/hangouts/{hangout['id']}/setup",
        json={"profile_ids": [profile["id"]]},
    )
    assert response.status_code == 200

    body = db.query(MessageLog).order_by(MessageLog.id.desc()).first().body
    assert "Sam's place" in body
    assert "Where: Sam's place" in body


@pytest.mark.parametrize("payload", [None, {}, {"profile_ids": []}])
def test_setup_without_profiles_returns_400_not_500(client_no_raise, payload):
    hangout = _create_hangout(client_no_raise)
    request = {} if payload is None else {"json": payload}

    response = client_no_raise.post(f"/api/hangouts/{hangout['id']}/setup", **request)

    assert response.status_code == 400
    assert response.json() == {"detail": "Select at least one profile to invite"}
    current = client_no_raise.get(f"/api/hangouts/{hangout['id']}")
    assert current.status_code == 200
    assert current.json()["status"] == "draft"
    assert current.json()["invites"] == []


def test_setup_with_only_unknown_profiles_returns_400_not_500(client_no_raise):
    hangout = _create_hangout(client_no_raise)

    response = client_no_raise.post(
        f"/api/hangouts/{hangout['id']}/setup",
        json={"profile_ids": [999999]},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "No valid profiles to invite"}
    current = client_no_raise.get(f"/api/hangouts/{hangout['id']}")
    assert current.json()["status"] == "draft"
    assert current.json()["invites"] == []


def test_setup_with_explicit_empty_selection_does_not_reuse_existing_invites(client_no_raise):
    profile = _create_profile(client_no_raise)
    hangout = _create_hangout(client_no_raise, {"profile_ids": [profile["id"]]})

    response = client_no_raise.post(
        f"/api/hangouts/{hangout['id']}/setup",
        json={"profile_ids": []},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Select at least one profile to invite"}
    current = client_no_raise.get(f"/api/hangouts/{hangout['id']}")
    assert current.json()["status"] == "draft"
    assert [invite["profile_id"] for invite in current.json()["invites"]] == [profile["id"]]


def test_setup_with_a_profile_activates_and_sends_a_mock_invite(client_no_raise):
    profile = _create_profile(client_no_raise)
    hangout = _create_hangout(client_no_raise)

    response = client_no_raise.post(
        f"/api/hangouts/{hangout['id']}/setup",
        json={"profile_ids": [profile["id"]]},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "active"
    assert len(result["invites"]) == 1
    assert result["invites"][0]["profile_id"] == profile["id"]
    assert result["invites"][0]["status"] == "pending"
    assert result["invites"][0]["last_outbound_at"] is not None


def test_setup_with_malformed_profile_ids_returns_422(client_no_raise):
    hangout = _create_hangout(client_no_raise)

    response = client_no_raise.post(
        f"/api/hangouts/{hangout['id']}/setup",
        json={"profile_ids": ["not-an-integer"]},
    )

    assert response.status_code == 422


def test_patch_rejects_null_for_a_setting_the_column_requires(client_no_raise):
    hangout = _create_hangout(client_no_raise)

    response = client_no_raise.patch(
        f"/api/hangouts/{hangout['id']}", json={"notify_enabled": None}
    )

    assert response.status_code == 400
    assert "notify_enabled" in response.json()["detail"]


def test_row_ids_larger_than_the_database_can_store_are_rejected(client_no_raise):
    too_large = 10**19

    assert client_no_raise.get(f"/api/hangouts/{too_large}").status_code == 422
    assert (
        client_no_raise.post("/api/hangouts", json={"organizer_profile_id": too_large}).status_code
        == 422
    )


def test_setup_missing_hangout_returns_404(client_no_raise):
    response = client_no_raise.post("/api/hangouts/999999/setup")

    assert response.status_code == 404
    assert response.json() == {"detail": "Hangout not found"}


def test_closed_hangout_cannot_be_set_up_again(client_no_raise):
    hangout = _create_hangout(client_no_raise)
    closed = client_no_raise.post(f"/api/hangouts/{hangout['id']}/close")
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    response = client_no_raise.post(f"/api/hangouts/{hangout['id']}/setup")

    assert response.status_code == 400
    assert response.json() == {"detail": "Hangout is closed"}
