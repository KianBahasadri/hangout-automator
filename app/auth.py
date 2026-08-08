"""Clerk session authentication for browser and JSON requests."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from clerk_backend_api import AuthenticateRequestOptions, Clerk
from clerk_backend_api.security import RequestState
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_PUBLIC_EXACT_PATHS = frozenset({"/api/health", "/sign-in"})


@lru_cache(maxsize=8)
def _clerk_client(secret_key: str) -> Clerk:
    """Reuse Clerk's verifier/client while allowing credential rotation in tests."""
    return Clerk(bearer_auth=secret_key or None)


async def authenticate_clerk_request(request: Request, settings: Settings) -> RequestState:
    """Verify the request's Clerk session without blocking the event loop."""
    options = AuthenticateRequestOptions(
        secret_key=settings.clerk_secret_key.strip() or None,
        jwt_key=settings.clerk_jwt_key.strip() or None,
        authorized_parties=settings.clerk_authorized_party_list,
    )
    client = _clerk_client(settings.clerk_secret_key.strip())
    # The SDK's public method is synchronous. Running it off-loop matters when
    # a secret key is used and Clerk's JWKS has to be fetched or refreshed.
    return await asyncio.to_thread(client.authenticate_request, request, options)


def _is_public_path(path: str, settings: Settings) -> bool:
    provider = (settings.sms_provider or "mock").strip().lower()
    twilio_webhook = provider == "twilio" and bool(settings.twilio_auth_token.strip())
    return (
        path in _PUBLIC_EXACT_PATHS
        or path.startswith("/sign-in/")
        or path == "/static"
        or path.startswith("/static/")
        or (path == "/webhooks/sms" and twilio_webhook)
    )


def _safe_destination(request: Request) -> str:
    destination = request.url.path
    if request.url.query:
        destination += "?" + request.url.query
    return destination


def _authentication_failure(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    destination = quote(_safe_destination(request), safe="")
    return RedirectResponse(f"/sign-in?redirect_url={destination}", status_code=303)


class ClerkAuthMiddleware(BaseHTTPMiddleware):
    """Require Clerk for the app while leaving integrations public as needed."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.clerk_enabled or _is_public_path(request.url.path, settings):
            return await call_next(request)

        try:
            request_state = await authenticate_clerk_request(request, settings)
        except Exception:
            # Do not expose verifier details or token material to clients. A
            # misconfigured/unavailable verifier is a server error, but it is
            # still fail-closed for the protected application routes.
            logger.exception(
                "Clerk request verification failed",
                extra={"path": request.url.path},
            )
            return (
                JSONResponse(
                    {"detail": "Authentication service unavailable"},
                    status_code=503,
                )
                if request.url.path.startswith("/api/")
                else Response(
                    "Authentication service unavailable",
                    status_code=503,
                    media_type="text/plain",
                )
            )

        if not request_state.is_authenticated:
            return _authentication_failure(request)

        request.state.clerk_auth = request_state
        payload: dict[str, Any] = request_state.payload or {}
        request.state.clerk_user_id = payload.get("sub")
        request.state.clerk_session_id = payload.get("sid")
        request.state.clerk_org_id = payload.get("org_id")
        return await call_next(request)
