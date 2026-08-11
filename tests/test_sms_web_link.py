"""Outbound SMS website footer (KIAN-533)."""

from types import SimpleNamespace

from app.config import Settings
from app.messages import (
    craft_confirm_reply,
    craft_followup_message,
    craft_invite_message,
    craft_organizer_digest,
    public_site_url,
    web_link_footer,
)
from app.models import MessageDirection, MessageLog
from app.services import load_hangout, setup_hangout
from tests.test_rsvp_flow import _profile


def _patch_public_url(monkeypatch, url: str) -> None:
    settings = Settings(public_base_url=url, _env_file=None)
    monkeypatch.setattr("app.messages.get_settings", lambda: settings)


def test_public_site_url_strips_trailing_slash(monkeypatch):
    _patch_public_url(monkeypatch, "https://hangout.example.com/")
    assert public_site_url() == "https://hangout.example.com"
    assert web_link_footer() == "Web: https://hangout.example.com"


def test_public_site_url_omits_empty_and_invalid(monkeypatch):
    _patch_public_url(monkeypatch, "   ")
    assert public_site_url() is None
    assert web_link_footer() is None

    _patch_public_url(monkeypatch, "http://")
    assert public_site_url() is None

    _patch_public_url(monkeypatch, "not-a-url")
    assert public_site_url() is None


def test_invite_and_followup_include_web_footer(monkeypatch):
    _patch_public_url(monkeypatch, "https://hangout.example.com")
    hangout = SimpleNamespace(
        id=1,
        motive="Dinner",
        day_date="2026-08-15",
        time="19:00",
        duration=None,
        location="Sam's",
        alcohol_involved=None,
        weed_involved=None,
        notes=None,
        invites=[],
    )
    profile = SimpleNamespace(name="Sam")

    invite = craft_invite_message(hangout, profile)  # type: ignore[arg-type]
    followup = craft_followup_message(hangout, profile, 1)  # type: ignore[arg-type]
    confirm = craft_confirm_reply(hangout)  # type: ignore[arg-type]
    digest = craft_organizer_digest(hangout)  # type: ignore[arg-type]

    for body in (invite, followup, confirm, digest):
        assert body.endswith("Web: https://hangout.example.com")
        assert "\n\nWeb: https://hangout.example.com" in body


def test_invite_omits_footer_when_base_url_unset(monkeypatch):
    _patch_public_url(monkeypatch, "")
    hangout = SimpleNamespace(
        id=1,
        motive="Dinner",
        day_date=None,
        time=None,
        duration=None,
        location=None,
        alcohol_involved=None,
        weed_involved=None,
        notes=None,
        invites=[],
    )
    body = craft_invite_message(hangout, SimpleNamespace(name="Sam"))  # type: ignore[arg-type]
    assert "Web:" not in body
    assert "You're invited:" in body


def test_setup_invite_sms_includes_public_url(db, monkeypatch, workspace):
    _patch_public_url(monkeypatch, "https://hangout.bahasadri.com")
    from app.models import Hangout, HangoutStatus

    invitee = _profile(db, "Sam", "+15551112222")
    hangout = Hangout(
        status=HangoutStatus.draft,
        motive="Beach",
        workspace_id=workspace.id,
    )
    db.add(hangout)
    db.commit()

    setup_hangout(db, load_hangout(db, hangout.id, workspace), [invitee.id], workspace)
    body = (
        db.query(MessageLog)
        .filter(MessageLog.direction == MessageDirection.outbound)
        .order_by(MessageLog.id.desc())
        .first()
        .body
    )
    assert "You're invited:" in body
    assert "Web: https://hangout.bahasadri.com" in body


def test_preview_invite_includes_web_link(client_no_raise, monkeypatch):
    _patch_public_url(monkeypatch, "https://preview.example.com")
    response = client_no_raise.post(
        "/api/sms/preview-invite",
        json={"recipient_name": "Sam", "motive": "Dinner"},
    )
    assert response.status_code == 200
    assert "Web: https://preview.example.com" in response.json()["body"]
