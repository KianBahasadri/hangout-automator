from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from clerk_backend_api.security import AuthStatus, RequestState

from app.config import Settings
from tests.support.access import allow_clerk_user


def _clerk_settings() -> Settings:
    return Settings(
        clerk_enabled=True,
        clerk_publishable_key="pk_test_example",
        clerk_frontend_api_url="https://example.clerk.accounts.dev",
        clerk_secret_key="sk_test_example",
        public_base_url="http://localhost:9000",
        _env_file=None,
    )


def test_clerk_redirects_browser_requests_to_sign_in(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    response = client.get("/profiles", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in?redirect_url=%2Fprofiles"


def test_clerk_returns_json_401_for_api_requests(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    response = client.get("/api/tags")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_clerk_leaves_health_and_static_routes_public(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    health = client.get("/api/health")
    static = client.get("/static/style.css")

    assert health.status_code == 200
    assert static.status_code == 200


def test_clerk_protects_mock_webhook(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    response = client.post(
        "/webhooks/sms",
        data={"From": "+15551110001", "Body": "confirm"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sign-in?redirect_url=%2Fwebhooks%2Fsms"


def test_clerk_leaves_twilio_webhook_public(client, monkeypatch):
    settings = Settings(
        **{
            **_clerk_settings().model_dump(),
            "sms_provider": "twilio",
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "twilio-test-token",
            "twilio_from_number": "+15005550006",
        }
    )
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: settings)

    response = client.post(
        "/webhooks/sms",
        data={"From": "+15551110001", "Body": "confirm"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.text == "Invalid Twilio signature"


def test_authenticated_clerk_request_reaches_app(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.web.get_settings", lambda: settings)

    async def fake_authenticate(request, settings):
        return RequestState(
            status=AuthStatus.SIGNED_IN,
            payload={"sub": "user_test", "sid": "sess_test"},
            token="test-token",
        )

    monkeypatch.setattr("app.auth.authenticate_clerk_request", fake_authenticate)
    allow_clerk_user(monkeypatch, "user_test")

    response = client.get("/")

    assert response.status_code == 200
    assert 'data-clerk-publishable-key="pk_test_example"' in response.text
    assert 'id="clerk-user-button"' in response.text
    assert "sk_test_example" not in response.text
    assert "user_test" not in response.text


def test_clerk_jwt_key_authentication_reaches_app(client, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    settings = Settings(
        clerk_enabled=True,
        clerk_publishable_key="pk_test_example",
        clerk_frontend_api_url="https://example.clerk.accounts.dev",
        clerk_jwt_key=public_key,
        public_base_url="http://localhost:9000",
        _env_file=None,
    )
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.web.get_settings", lambda: settings)
    allow_clerk_user(monkeypatch, "user_jwt")
    token = jwt.encode(
        {
            "sub": "user_jwt",
            "sid": "sess_jwt",
            "azp": "http://localhost:9000",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )

    response = client.get("/", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert 'id="clerk-user-button"' in response.text


def test_sign_in_rejects_external_redirect_destination(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.routers.web.get_settings", lambda: settings)

    response = client.get("/sign-in?redirect_url=https://evil.example/steal")

    assert response.status_code == 200
    assert 'data-redirect-url="/"' in response.text


def test_sign_in_rejects_backslash_redirect_destination(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.routers.web.get_settings", lambda: settings)

    response = client.get(r"/sign-in?redirect_url=/%5C%5Cevil.example")

    assert response.status_code == 200
    assert 'data-redirect-url="/"' in response.text


def test_clerk_verifier_exception_fails_closed(client, monkeypatch):
    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    async def broken_authenticate(request, settings):
        raise RuntimeError("test verifier failure")

    monkeypatch.setattr("app.auth.authenticate_clerk_request", broken_authenticate)

    response = client.get("/profiles")

    assert response.status_code == 503


def test_deep_health_requires_auth_but_shallow_stays_public(client, monkeypatch):
    """The Cloudflare probe hits /api/health unauthenticated; the deep variant
    is protected like every other /api route."""
    from tests.test_tenant_isolation import _clerk_settings

    settings = _clerk_settings()
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)

    shallow = client.get("/api/health")
    assert shallow.status_code == 200

    deep = client.get("/api/health/deep")
    assert deep.status_code == 401
