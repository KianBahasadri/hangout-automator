from app.models import Profile


def _count(db, name):
    return db.query(Profile).filter(Profile.name == name).count()


# --- web form ---


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


def test_api_patch_rejects_unusable_phone(client, db):
    created = client.post("/api/profiles", json={"name": "Sam", "phone": "+15551112222"}).json()

    response = client.patch(f"/api/profiles/{created['id']}", json={"phone": "not a phone"})

    assert response.status_code == 400
    db.expire_all()
    assert db.get(Profile, created["id"]).phone == "+15551112222"
