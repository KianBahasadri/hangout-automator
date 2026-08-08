import json
from pathlib import Path

from app.config import Settings
from app.event_logging import configure_logging


def _json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_http_trace_writes_source_body_and_correlation_id(client, tmp_path):
    log_path = tmp_path / "audit" / "server.log"
    configure_logging(Settings(log_file=str(log_path), _env_file=None))

    response = client.post(
        "/webhooks/sms",
        json={"from": "+15551234567", "body": "confirm me"},
        headers={
            "Authorization": "Bearer must-not-be-written",
            "Cookie": "session=must-not-be-written",
            "CF-Access-Client-Id": "client-id-must-not-be-written",
            "CF-Access-Client-Secret": "client-secret-must-not-be-written",
            "CF-Connecting-IP": "198.51.100.10",
            "CF-Access-Authenticated-User-Email": "operator@example.com",
            "X-Forwarded-For": "198.51.100.10, 10.0.0.5",
            "X-Twilio-Signature": "must-not-be-written",
        },
    )

    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    events = _json_lines(log_path)

    completed = next(
        event
        for event in reversed(events)
        if event["event"] == "http.request.completed" and event["data"]["path"] == "/webhooks/sms"
    )
    assert completed["request_id"] == request_id
    assert completed["data"]["source"]["cf_connecting_ip"] == "198.51.100.10"
    assert completed["data"]["source"]["access_identity"] == "operator@example.com"
    assert "confirm me" in completed["data"]["request_body"]["body"]
    for header_name in (
        "authorization",
        "cookie",
        "cf-access-client-id",
        "cf-access-client-secret",
        "x-twilio-signature",
    ):
        assert header_name in completed["data"]["sensitive_header_names"]
        assert header_name not in completed["data"]["headers"]

    received_sms = next(
        event for event in reversed(events) if event["event"] == "sms.webhook.received"
    )
    assert received_sms["request_id"] == request_id
    assert received_sms["data"]["from_phone"] == "+15551234567"
    assert received_sms["data"]["body"] == "confirm me"

    assert any(
        event["event"] == "database.transaction.committed"
        and any(change["model"] == "MessageLog" for change in event["data"]["changes"])
        for event in events
    )


def test_invalid_json_event_body_is_bounded(client, tmp_path):
    log_path = tmp_path / "audit" / "server.log"
    configure_logging(Settings(log_file=str(log_path), _env_file=None))
    body = "{" + ("x" * (262_144 - 1))

    response = client.post(
        "/webhooks/sms",
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    events = _json_lines(log_path)
    rejected = next(
        event
        for event in reversed(events)
        if event["event"] == "sms.webhook.rejected"
        and event["data"].get("reason") == "invalid_json"
    )
    assert len(rejected["data"]["raw_body"]) == 262_144
    assert rejected["data"]["raw_body_bytes"] == len(body.encode("utf-8"))
    assert rejected["data"]["raw_body_truncated"] is False

    body = "{" + ("x" * 262_144)
    response = client.post(
        "/webhooks/sms",
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    events = _json_lines(log_path)
    rejected = next(
        event
        for event in reversed(events)
        if event["event"] == "sms.webhook.rejected"
        and event["data"].get("reason") == "invalid_json"
        and event["data"]["raw_body_bytes"] == len(body.encode("utf-8"))
    )
    assert len(rejected["data"]["raw_body"]) == 262_144
    assert rejected["data"]["raw_body_truncated"] is True
