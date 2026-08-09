import pytest

from app.models import MessageDirection, MessageLog
from app.services import send_sms


def test_send_sms_rejects_invalid_destination_before_provider(db, monkeypatch):
    calls = []

    class UnexpectedProvider:
        def send(self, to, body):
            calls.append((to, body))
            return True, None

    monkeypatch.setattr("app.services.get_sms_provider", lambda: UnexpectedProvider())

    ok, error = send_sms(db, to="911", body="must not be sent")

    assert ok is False
    assert error == "Destination phone number is not usable"
    assert calls == []

    db.commit()
    entry = db.query(MessageLog).one()
    assert entry.phone == "+911"
    assert entry.body == "must not be sent"
    assert entry.direction == MessageDirection.outbound
    assert entry.success is False
    assert entry.error == error
    assert entry.hangout_id is None


def test_send_sms_passes_valid_destination_to_provider(db, monkeypatch):
    calls = []

    class Provider:
        def send(self, to, body):
            calls.append((to, body))
            return True, None

    monkeypatch.setattr("app.services.get_sms_provider", lambda: Provider())

    ok, error = send_sms(db, to="555 123 4567", body="allowed")
    db.commit()

    assert (ok, error) == (True, None)
    assert calls == [("+15551234567", "allowed")]
    entry = db.query(MessageLog).one()
    assert entry.phone == "+15551234567"
    assert entry.success is True


def test_send_sms_logs_provider_exception(db, monkeypatch):
    class RaisingProvider:
        def send(self, to, body):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr("app.services.get_sms_provider", lambda: RaisingProvider())

    with pytest.raises(RuntimeError, match="provider exploded"):
        send_sms(db, to="+15551234567", body="failed")
    db.commit()

    entry = db.query(MessageLog).one()
    assert entry.phone == "+15551234567"
    assert entry.body == "failed"
    assert entry.success is False
    assert entry.error == "provider exploded"


def test_webhook_rate_limits_per_phone(client, monkeypatch):
    """Past the per-phone ceiling, the webhook answers 429."""
    from app.config import Settings

    settings = Settings(sms_rate_limit_per_phone_per_minute=3, _env_file=None)
    monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: settings)

    for _ in range(3):
        response = client.post("/webhooks/sms", data={"From": "+15551112222", "Body": "confirm"})
        assert response.status_code == 200

    blocked = client.post("/webhooks/sms", data={"From": "+15551112222", "Body": "confirm"})
    assert blocked.status_code == 429

    # A different source phone is unaffected by that bucket.
    other = client.post("/webhooks/sms", data={"From": "+15553334444", "Body": "confirm"})
    assert other.status_code == 200


def test_webhook_global_rate_limit(client, monkeypatch):
    """The global ceiling applies across all source phones."""
    from app.config import Settings

    settings = Settings(sms_rate_limit_global_per_minute=2, _env_file=None)
    monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: settings)

    assert (
        client.post("/webhooks/sms", data={"From": "+15551110001", "Body": "hi"}).status_code == 200
    )
    assert (
        client.post("/webhooks/sms", data={"From": "+15551110002", "Body": "hi"}).status_code == 200
    )
    blocked = client.post("/webhooks/sms", data={"From": "+15551110003", "Body": "hi"})
    assert blocked.status_code == 429


def test_signature_verification_runs_before_rate_limit(client, monkeypatch):
    """Unsigned floods die at the cheaper signature check, never the limiter."""
    from app.config import Settings

    settings = Settings(
        sms_provider="twilio",
        twilio_account_sid="AC123",
        twilio_auth_token="test-token",
        twilio_from_number="+15005550006",
        public_base_url="https://hangout.example.com",
        sms_rate_limit_per_phone_per_minute=1,
        _env_file=None,
    )
    monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: settings)

    for _ in range(5):
        response = client.post(
            "/webhooks/sms",
            data={"From": "+15551112222", "Body": "confirm"},
        )
        assert response.status_code == 403  # invalid signature, not 429
