from app.models import Profile


def _count(db, name):
    return db.query(Profile).filter(Profile.name == name).count()


# --- web form ---


def test_contacts_page_links_to_dedicated_add_page(client):
    response = client.get("/contacts")

    assert response.status_code == 200
    assert 'href="/contacts/new"' in response.text
    assert "Add new contact" in response.text
    assert "Contacts" in response.text
    assert '<h2 style="margin-top:0;">Add contact</h2>' not in response.text


def test_legacy_profiles_paths_redirect_to_contacts(client):
    list_response = client.get("/profiles", follow_redirects=False)
    new_response = client.get("/profiles/new", follow_redirects=False)

    assert list_response.status_code == 307
    assert list_response.headers["location"] == "/contacts"
    assert new_response.status_code == 307
    assert new_response.headers["location"] == "/contacts/new"


def test_new_contacts_page_has_import_and_batch_controls(client):
    response = client.get("/contacts/new")

    assert response.status_code == 200
    assert "Import from phone" in response.text
    assert "Add another contact" in response.text
    assert "Save contacts" in response.text
    assert 'name="contacts[0][name]"' in response.text
    assert 'name="contacts[0][phone]"' in response.text


def test_batch_form_saves_contacts_together(client, db):
    response = client.post(
        "/contacts",
        data={
            "contacts[0][name]": "Sam Rivera",
            "contacts[0][phone]": "(555) 111-2222",
            "contacts[0][drinks]": "yes",
            "contacts[0][smokes]": "",
            "contacts[0][drive]": "",
            "contacts[1][name]": "Taylor Kim",
            "contacts[1][phone]": "+1 (555) 333-4444",
            "contacts[1][drinks]": "",
            "contacts[1][smokes]": "no",
            "contacts[1][drive]": "maybe",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/contacts"
    assert _count(db, "Sam Rivera") == 1
    assert _count(db, "Taylor Kim") == 1
    assert db.query(Profile).filter(Profile.phone == "+15551112222").one().drinks.value == "yes"


def test_legacy_profiles_form_prefix_still_saves(client, db):
    response = client.post(
        "/profiles",
        data={
            "profiles[0][name]": "Legacy Name",
            "profiles[0][phone]": "+15559998888",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/contacts"
    assert _count(db, "Legacy Name") == 1


def test_batch_form_rejects_duplicate_without_partial_save(client, db):
    client.post("/contacts", data={"name": "Existing", "phone": "+15551112222"})

    response = client.post(
        "/contacts",
        data={
            "contacts[0][name]": "New contact",
            "contacts[0][phone]": "+15551113333",
            "contacts[1][name]": "Duplicate contact",
            "contacts[1][phone]": "+15551112222",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "already exists" in response.text
    assert _count(db, "New contact") == 0


def test_batch_validation_keeps_entered_values_for_correction(client, db):
    response = client.post(
        "/contacts",
        data={
            "contacts[0][name]": "Sam Rivera",
            "contacts[0][phone]": "not a phone",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert 'value="Sam Rivera"' in response.text
    assert 'value="not a phone"' in response.text
    assert _count(db, "Sam Rivera") == 0


def test_form_rejects_unusable_phone(client, db):
    response = client.post(
        "/contacts", data={"name": "Bad", "phone": "abc"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/contacts?error=bad_phone"
    assert _count(db, "Bad") == 0


def test_form_shows_the_error_after_redirect(client):
    response = client.get("/contacts?error=bad_phone")

    assert response.status_code == 200
    assert "isn&#39;t usable" in response.text or "isn't usable" in response.text


def test_form_reports_duplicate_phone(client, db):
    client.post("/contacts", data={"name": "Sam", "phone": "555 111 2222"})
    response = client.post(
        "/contacts", data={"name": "Sam Again", "phone": "+15551112222"}, follow_redirects=False
    )

    assert response.headers["location"] == "/contacts?error=duplicate_phone"
    assert _count(db, "Sam Again") == 0


def test_form_creates_contact_with_normalized_phone(client, db):
    client.post("/contacts", data={"name": "Sam", "phone": "(555) 111-2222"})

    profile = db.query(Profile).filter(Profile.name == "Sam").one()
    assert profile.phone == "+15551112222"


# --- JSON API ---


def test_api_rejects_phone_that_normalizes_to_nothing(client, db):
    response = client.post("/api/contacts", json={"name": "Bad", "phone": "abcde"})

    assert response.status_code == 400
    assert _count(db, "Bad") == 0


def test_legacy_api_profiles_path_still_works(client, db):
    response = client.post("/api/profiles", json={"name": "Legacy", "phone": "+15551110000"})

    assert response.status_code == 201
    assert response.json()["name"] == "Legacy"
    assert _count(db, "Legacy") == 1


def test_deleting_an_invited_contact_takes_their_invites_and_keeps_the_sms_log(client, db):
    from app.models import HangoutInvite, MessageLog

    profile = client.post("/api/contacts", json={"name": "Sam", "phone": "+15551112222"}).json()
    hangout = client.post("/api/hangouts", json={"profile_ids": [profile["id"]]}).json()
    client.post(f"/api/hangouts/{hangout['id']}/setup", json={"profile_ids": [profile["id"]]})

    response = client.delete(f"/api/contacts/{profile['id']}")

    assert response.status_code == 204
    assert db.query(HangoutInvite).filter(HangoutInvite.profile_id == profile["id"]).count() == 0
    assert db.query(MessageLog).count() == 1


def test_api_patch_rejects_unusable_phone(client, db):
    created = client.post("/api/contacts", json={"name": "Sam", "phone": "+15551112222"}).json()

    response = client.patch(f"/api/contacts/{created['id']}", json={"phone": "not a phone"})

    assert response.status_code == 400
    db.expire_all()
    assert db.get(Profile, created["id"]).phone == "+15551112222"
