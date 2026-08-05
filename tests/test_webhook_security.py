import hashlib
import json

import pytest
from twilio.request_validator import RequestValidator

from app.models import Hangout, HangoutInvite, HangoutStatus, InviteStatus, Profile

AUTH_TOKEN = "test-twilio-auth-token"
PUBLIC_BASE_URL = "https://hangout.example.com"
WEBHOOK_PATH = "/webhooks/sms"


@pytest.fixture
def twilio_settings(monkeypatch):
    from app.config import Settings

    settings = Settings(
        sms_provider="twilio",
        twilio_account_sid="AC123",
        twilio_auth_token=AUTH_TOKEN,
        twilio_from_number="+15005550006",
        public_base_url=PUBLIC_BASE_URL,
        _env_file=None,
    )
    monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: settings)
    return settings


def _sign(url, params=None):
    return RequestValidator(AUTH_TOKEN).compute_signature(url, params)


def _setup_invitee(db, phone="+15551112222"):
    profile = Profile(name="Sam", phone=phone)
    db.add(profile)
    db.flush()
    hangout = Hangout(status=HangoutStatus.active, motive="Beach day")
    db.add(hangout)
    db.flush()
    db.add(
        HangoutInvite(hangout_id=hangout.id, profile_id=profile.id, status=InviteStatus.pending)
    )
    db.commit()
    return profile, hangout


# --- mock provider: JSON/form allowed without signature (documented local path) ---


def test_mock_json_webhook_accepted(client, db):
    _setup_invitee(db)
    response = client.post(WEBHOOK_PATH, json={"from": "+15551112222", "body": "confirm"})
    assert response.status_code == 200
    assert "confirmed" in response.text


def test_mock_form_webhook_accepted(client, db):
    _setup_invitee(db)
    response = client.post(WEBHOOK_PATH, data={"From": "+15551112222", "Body": "confirm"})
    assert response.status_code == 200
    assert "confirmed" in response.text


def test_webhook_missing_from_is_400(client, db):
    _setup_invitee(db)
    response = client.post(WEBHOOK_PATH, json={"body": "confirm"})
    assert response.status_code == 400


# --- twilio provider: signature required for form and JSON alike ---


def test_form_without_signature_rejected(client, db, twilio_settings):
    _setup_invitee(db)
    response = client.post(WEBHOOK_PATH, data={"From": "+15551112222", "Body": "confirm"})
    assert response.status_code == 403


def test_form_with_wrong_signature_rejected(client, db, twilio_settings):
    _setup_invitee(db)
    response = client.post(
        WEBHOOK_PATH,
        data={"From": "+15551112222", "Body": "confirm"},
        headers={"X-Twilio-Signature": "not-a-valid-signature"},
    )
    assert response.status_code == 403


def test_form_with_valid_signature_accepted(client, db, twilio_settings):
    profile, _ = _setup_invitee(db)
    params = {"From": "+15551112222", "Body": "confirm"}
    signature = _sign(PUBLIC_BASE_URL + WEBHOOK_PATH, params)
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers={"X-Twilio-Signature": signature},
    )
    assert response.status_code == 200
    assert "confirmed" in response.text
    db.expire_all()
    invite = (
        db.query(HangoutInvite)
        .filter(HangoutInvite.profile_id == profile.id)
        .first()
    )
    assert invite.status == InviteStatus.confirmed


def test_json_without_signature_rejected(client, db, twilio_settings):
    _setup_invitee(db)
    response = client.post(WEBHOOK_PATH, json={"from": "+15551112222", "body": "confirm"})
    assert response.status_code == 403


def test_json_with_valid_signature_accepted(client, db, twilio_settings):
    _setup_invitee(db)
    body = json.dumps({"From": "+15551112222", "Body": "confirm"})
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    url = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}?bodySHA256={body_hash}"
    response = client.post(
        WEBHOOK_PATH,
        params={"bodySHA256": body_hash},
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Twilio-Signature": _sign(url, {}),
        },
    )
    assert response.status_code == 200
    assert "confirmed" in response.text


def test_invalid_json_is_400(client, db, twilio_settings):
    response = client.post(
        WEBHOOK_PATH,
        content="{not json",
        headers={
            "Content-Type": "application/json",
            "X-Twilio-Signature": _sign(PUBLIC_BASE_URL + WEBHOOK_PATH, {}),
        },
    )
    assert response.status_code == 400
