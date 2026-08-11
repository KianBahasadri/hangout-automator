"""Admin Panel cost estimates and access gate (KIAN-535 Phase A)."""

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.costs import sms_period_stats, twilio_cost_card
from app.models import AccessRole, MessageDirection, MessageLog
from tests.support.access import grant_access
from tests.test_access_control import _signed_in_as


def _add_msg(db, *, direction, success=True, hours_ago=0, body="hi"):
    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    row = MessageLog(
        direction=direction,
        phone="+15550001111",
        body=body,
        success=success,
        created_at=created,
    )
    db.add(row)
    db.commit()
    return row


def test_sms_period_stats_counts_and_estimate(db):
    _add_msg(db, direction=MessageDirection.outbound, success=True)
    _add_msg(db, direction=MessageDirection.outbound, success=True)
    _add_msg(db, direction=MessageDirection.outbound, success=False)
    _add_msg(db, direction=MessageDirection.inbound, success=True)
    # Outside window
    _add_msg(db, direction=MessageDirection.outbound, success=True, hours_ago=48 * 24)

    since = datetime.now(timezone.utc) - timedelta(days=7)
    stats = sms_period_stats(
        db,
        since=since,
        price_per_message=0.01,
        key="d7",
        label="Last 7 days",
    )
    assert stats.outbound_ok == 2
    assert stats.outbound_fail == 1
    assert stats.inbound == 1
    assert stats.billable == 3
    assert stats.estimated_usd == 0.03


def test_twilio_card_without_price_shows_counts_only(db):
    _add_msg(db, direction=MessageDirection.outbound, success=True)
    settings = Settings(twilio_sms_price_estimate=None, _env_file=None)
    card = twilio_cost_card(db, settings)
    assert card.source == "estimate"
    assert card.periods[0].estimated_usd is None
    assert "messages" in card.summary.lower()


def test_twilio_card_with_price_shows_usd(db):
    _add_msg(db, direction=MessageDirection.outbound, success=True)
    _add_msg(db, direction=MessageDirection.inbound, success=True)
    settings = Settings(twilio_sms_price_estimate=0.02, _env_file=None)
    card = twilio_cost_card(db, settings)
    assert card.periods[0].estimated_usd == 0.04
    assert "$" in card.summary


def test_member_cannot_open_admin_panel(client, monkeypatch):
    _signed_in_as(monkeypatch, "user_member", "member@example.test")
    grant_access("member@example.test", role=AccessRole.member)

    assert client.get("/admin").status_code == 403
    assert client.get("/admin/access").status_code == 403
    assert "Admin Panel" not in client.get("/").text


def test_admin_sees_admin_panel_nav(client, monkeypatch):
    _signed_in_as(monkeypatch, "user_admin", "admin@example.test")
    grant_access("admin@example.test", role=AccessRole.admin)

    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/admin">Admin Panel</a>' in home.text
    assert client.get("/admin").status_code == 200


def test_settings_access_redirects_to_admin_access(client):
    response = client.get("/settings/access", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/access"
