import pytest
from pydantic import ValidationError

from app.config import Settings
from app.sms import MockSmsProvider, TwilioSmsProvider, get_sms_provider


def test_mock_default_ok():
    settings = Settings(sms_provider="mock", _env_file=None)
    assert settings.sms_provider == "mock"


def test_app_port_loads_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_PORT=9123\n", encoding="utf-8")
    monkeypatch.delenv("APP_PORT", raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.app_port == 9123


def test_google_maps_api_key_loads_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_MAPS_API_KEY=places-test-key\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.google_maps_api_key == "places-test-key"


def test_twilio_incomplete_raises():
    with pytest.raises(ValidationError, match="TWILIO_AUTH_TOKEN"):
        Settings(
            sms_provider="twilio",
            twilio_account_sid="AC123",
            twilio_from_number="+15005550006",
            _env_file=None,
        )


def test_twilio_partial_raises_from_sms_module():
    with pytest.raises(ValidationError, match="TWILIO_FROM_NUMBER"):
        Settings(
            sms_provider="twilio",
            twilio_account_sid="AC123",
            twilio_auth_token="tok",
            _env_file=None,
        )


def test_get_sms_provider_selects_expected_provider():
    mock_settings = Settings(sms_provider="mock", _env_file=None)
    assert isinstance(get_sms_provider(mock_settings), MockSmsProvider)
    twilio_settings = Settings(
        sms_provider="twilio",
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_from_number="+15005550006",
        _env_file=None,
    )
    assert isinstance(get_sms_provider(twilio_settings), TwilioSmsProvider)


def test_twilio_complete_ok():
    settings = Settings(
        sms_provider="twilio",
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_from_number="+15005550006",
        _env_file=None,
    )
    assert settings.twilio_auth_token == "tok"


def test_invalid_provider_raises():
    with pytest.raises(ValidationError, match="Invalid SMS_PROVIDER"):
        Settings(sms_provider="carrier_pigeon", _env_file=None)


def test_clerk_is_disabled_by_default():
    settings = Settings(
        sms_provider="mock",
        public_base_url="http://localhost:9000",
        _env_file=None,
    )

    assert settings.clerk_enabled is False
    assert settings.clerk_authorized_party_list == ["http://localhost:9000"]


def test_clerk_enabled_requires_verification_configuration():
    with pytest.raises(ValidationError, match="CLERK_PUBLISHABLE_KEY"):
        Settings(clerk_enabled=True, _env_file=None)


def test_clerk_enabled_accepts_secret_or_jwt_key_and_normalizes_origins():
    settings = Settings(
        clerk_enabled=True,
        clerk_publishable_key="pk_test_example",
        clerk_frontend_api_url="https://example.clerk.accounts.dev/",
        clerk_secret_key="sk_test_example",
        clerk_authorized_parties=" http://localhost:9000/ , https://app.example.com/ ",
        _env_file=None,
    )

    assert settings.clerk_authorized_party_list == [
        "http://localhost:9000",
        "https://app.example.com",
    ]
