"""Clerk session authentication for browser and JSON requests."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from clerk_backend_api import AuthenticateRequestOptions
from clerk_backend_api.security import RequestState
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app import access
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_PUBLIC_EXACT_PATHS = frozenset({"/api/health", "/sign-in"})


async def authenticate_clerk_request(request: Request, settings: Settings) -> RequestState:
    """Verify the request's Clerk session without blocking the event loop."""
    options = AuthenticateRequestOptions(
        secret_key=settings.clerk_secret_key.strip() or None,
        jwt_key=settings.clerk_jwt_key.strip() or None,
        authorized_parties=settings.clerk_authorized_party_list,
    )
    client = access.clerk_client(settings.clerk_secret_key.strip())
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


def _service_unavailable(request: Request) -> Response:
    """503 for anything that leaves the app unable to make an auth decision."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication service unavailable"}, status_code=503)
    return Response(
        "Authentication service unavailable",
        status_code=503,
        media_type="text/plain",
    )


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
            return _service_unavailable(request)

        if not request_state.is_authenticated:
            return _authentication_failure(request)

        request.state.clerk_auth = request_state
        payload: dict[str, Any] = request_state.payload or {}
        clerk_user_id = payload.get("sub")
        request.state.clerk_user_id = clerk_user_id
        request.state.clerk_session_id = payload.get("sid")
        request.state.clerk_org_id = payload.get("org_id")

        if not clerk_user_id:
            # Verified, but carrying no subject claim, so the request cannot be
            # attributed to anyone. Should be unreachable; refuse it as an
            # identity failure rather than guessing at an access decision.
            logger.error(
                "Clerk-authenticated request has no subject claim; refusing",
                extra={"path": request.url.path},
            )
            return _authentication_failure(request)

        # Clerk vouches for who they are; the access list decides whether this
        # deployment admits them. Every protected route runs behind this,
        # including the ones that never resolve a workspace.
        try:
            email = await access.email_for_clerk_user(clerk_user_id, settings)
        except access.IdentityLookupFailed:
            # Unreachable Clerk is an outage, not a revoked grant. Telling a
            # legitimate user they are "not on the access list" would send them
            # to an admin over a problem no admin can fix.
            return _service_unavailable(request)

        role = await asyncio.to_thread(access.role_for_email, email) if email else None
        if role is None:
            logger.warning(
                "Refusing signed-in user with no access grant",
                extra={"path": request.url.path, "has_email": bool(email)},
            )
            return access.access_denied_response(request, email)

        request.state.clerk_email = email
        request.state.access_role = role
        return await call_next(request)
