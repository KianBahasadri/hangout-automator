from app.models import Profile


def _count(db, name):
    return db.query(Profile).filter(Profile.name == name).count()


# --- web form ---


def test_profiles_page_links_to_dedicated_add_page(client):
    response = client.get("/profiles")

    assert response.status_code == 200
    assert 'href="/profiles/new"' in response.text
    assert "Add new profile" in response.text
    assert '<h2 style="margin-top:0;">Add profile</h2>' not in response.text


def test_new_profiles_page_has_import_and_batch_controls(client):
    response = client.get("/profiles/new")

    assert response.status_code == 200
    assert "Import from phone" in response.text
    assert "Add another profile" in response.text
    assert "Save profiles" in response.text
    assert 'name="profiles[0][name]"' in response.text
    assert 'name="profiles[0][phone]"' in response.text


def test_batch_form_saves_profiles_together(client, db):
    response = client.post(
        "/profiles",
        data={
            "profiles[0][name]": "Sam Rivera",
            "profiles[0][phone]": "(555) 111-2222",
            "profiles[0][drinks]": "yes",
            "profiles[0][smokes]": "",
            "profiles[0][drive]": "",
            "profiles[1][name]": "Taylor Kim",
            "profiles[1][phone]": "+1 (555) 333-4444",
            "profiles[1][drinks]": "",
            "profiles[1][smokes]": "no",
            "profiles[1][drive]": "maybe",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/profiles"
    assert _count(db, "Sam Rivera") == 1
    assert _count(db, "Taylor Kim") == 1
    assert db.query(Profile).filter(Profile.phone == "+15551112222").one().drinks.value == "yes"


def test_batch_form_rejects_duplicate_without_partial_save(client, db):
    client.post("/profiles", data={"name": "Existing", "phone": "+15551112222"})

    response = client.post(
        "/profiles",
        data={
            "profiles[0][name]": "New profile",
            "profiles[0][phone]": "+15551113333",
            "profiles[1][name]": "Duplicate profile",
            "profiles[1][phone]": "+15551112222",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "already exists" in response.text
    assert _count(db, "New profile") == 0


def test_batch_validation_keeps_entered_values_for_correction(client, db):
    response = client.post(
        "/profiles",
        data={
            "profiles[0][name]": "Sam Rivera",
            "profiles[0][phone]": "not a phone",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert 'value="Sam Rivera"' in response.text
    assert 'value="not a phone"' in response.text
    assert _count(db, "Sam Rivera") == 0


def test_form_rejects_unusable_phone(client, db):
    response = client.post("/profiles", data={"name": "Bad", "phone": "abc"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/profiles?error=bad_phone"
    assert _count(db, "Bad") == 0


def test_form_shows_the_error_after_redirect(client):
    response = client.get("/profiles?error=bad_phone")

    assert response.status_code == 200
    assert "isn&#39;t usable" in response.text or "isn't usable" in response.text


def test_form_reports_duplicate_phone(client, db):
    client.post("/profiles", data={"name": "Sam", "phone": "555 111 2222"})
    response = client.post(
        "/profiles", data={"name": "Sam Again", "phone": "+15551112222"}, follow_redirects=False
    )

    assert response.headers["location"] == "/profiles?error=duplicate_phone"
    assert _count(db, "Sam Again") == 0


def test_form_creates_profile_with_normalized_phone(client, db):
    client.post("/profiles", data={"name": "Sam", "phone": "(555) 111-2222"})

    profile = db.query(Profile).filter(Profile.name == "Sam").one()
    assert profile.phone == "+15551112222"


# --- JSON API ---


def test_api_rejects_phone_that_normalizes_to_nothing(client, db):
    response = client.post("/api/profiles", json={"name": "Bad", "phone": "abcde"})

    assert response.status_code == 400
    assert _count(db, "Bad") == 0


def test_deleting_an_invited_profile_takes_their_invites_and_keeps_the_sms_log(client, db):
    from app.models import HangoutInvite, MessageLog

    profile = client.post("/api/profiles", json={"name": "Sam", "phone": "+15551112222"}).json()
    hangout = client.post("/api/hangouts", json={"profile_ids": [profile["id"]]}).json()
    client.post(f"/api/hangouts/{hangout['id']}/setup", json={"profile_ids": [profile["id"]]})

    response = client.delete(f"/api/profiles/{profile['id']}")

    assert response.status_code == 204
    assert db.query(HangoutInvite).filter(HangoutInvite.profile_id == profile["id"]).count() == 0
    assert db.query(MessageLog).count() == 1


def test_api_patch_rejects_unusable_phone(client, db):
    created = client.post("/api/profiles", json={"name": "Sam", "phone": "+15551112222"}).json()

    response = client.patch(f"/api/profiles/{created['id']}", json={"phone": "not a phone"})

    assert response.status_code == 400
    db.expire_all()
    assert db.get(Profile, created["id"]).phone == "+15551112222"
