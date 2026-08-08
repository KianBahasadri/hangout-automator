import pytest


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


def test_default_dietary_restrictions_are_seeded(client_no_raise):
    response = client_no_raise.get("/api/allergies")
    assert response.status_code == 200
    names = {row["name"].lower() for row in response.json()}
    assert "meat" in names
    assert "pork" in names


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
