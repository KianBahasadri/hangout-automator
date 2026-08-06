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
