import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
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


def _clear_settings_env(monkeypatch) -> None:
    """Drop process env that would override the dotenv files under test.

    conftest forces CLERK_ENABLED=false so app imports stay hermetic; that
    value would otherwise win over CLERK_ENABLED=true written into a temp
    env file. Same for APP_PORT and the other Clerk keys.
    """
    for name in (
        "HANGOUT_ENV",
        "APP_PORT",
        "CLERK_ENABLED",
        "CLERK_PUBLISHABLE_KEY",
        "CLERK_FRONTEND_API_URL",
        "CLERK_SECRET_KEY",
        "CLERK_JWT_KEY",
        "CLERK_AUTHORIZED_PARTIES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_get_settings_selects_the_environment_specific_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env.development").write_text("APP_PORT=8123\n", encoding="utf-8")
    (tmp_path / ".env.production").write_text("APP_PORT=9123\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _clear_settings_env(monkeypatch)
    get_settings.cache_clear()

    try:
        assert get_settings().app_port == 8123

        monkeypatch.setenv("HANGOUT_ENV", "production")
        get_settings.cache_clear()
        assert get_settings().app_port == 9123
    finally:
        get_settings.cache_clear()


def test_get_settings_rejects_live_clerk_keys_in_development(tmp_path, monkeypatch):
    (tmp_path / ".env.development").write_text(
        "\n".join(
            (
                "CLERK_ENABLED=true",
                "CLERK_PUBLISHABLE_KEY=pk_live_ZXhhbXBsZS5jb20k",
                "CLERK_FRONTEND_API_URL=https://example.com",
                "CLERK_SECRET_KEY=sk_live_example",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _clear_settings_env(monkeypatch)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="pk_test_"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_get_settings_rejects_test_clerk_keys_in_production(tmp_path, monkeypatch):
    (tmp_path / ".env.production").write_text(
        "\n".join(
            (
                "CLERK_ENABLED=true",
                "CLERK_PUBLISHABLE_KEY=pk_test_ZXhhbXBsZS5jb20k",
                "CLERK_FRONTEND_API_URL=https://example.com",
                "CLERK_SECRET_KEY=sk_test_example",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("HANGOUT_ENV", "production")
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="pk_live_"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_get_settings_rejects_mismatched_clerk_frontend_api(tmp_path, monkeypatch):
    (tmp_path / ".env.development").write_text(
        "\n".join(
            (
                "CLERK_ENABLED=true",
                "CLERK_PUBLISHABLE_KEY=pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk",
                "CLERK_FRONTEND_API_URL=https://different.clerk.accounts.dev",
                "CLERK_SECRET_KEY=sk_test_example",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _clear_settings_env(monkeypatch)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="different Clerk instances"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_get_settings_rejects_non_absolute_clerk_frontend_api(tmp_path, monkeypatch):
    (tmp_path / ".env.development").write_text(
        "\n".join(
            (
                "CLERK_ENABLED=true",
                "CLERK_PUBLISHABLE_KEY=pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk",
                "CLERK_FRONTEND_API_URL=example.clerk.accounts.dev",
                "CLERK_SECRET_KEY=sk_test_example",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _clear_settings_env(monkeypatch)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="absolute URL"):
            get_settings()
    finally:
        get_settings.cache_clear()
