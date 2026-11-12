"""Security helpers: admin authorization, session tokens, origin checks."""

from __future__ import annotations

import hmac
import secrets

from fastapi import Header, HTTPException, Request, status

from app.core.config import Settings


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def is_origin_allowed(origin: str | None, settings: Settings) -> bool:
    """Validate the Origin header of a WebSocket handshake.

    Browsers always send Origin for WebSockets. Non-browser clients (tests, CLI tools)
    may omit it; that is permitted outside production.
    """
    if origin is None:
        return settings.environment != "production"
    allowed = settings.cors_origin_list
    return "*" in allowed or origin.rstrip("/") in allowed


def require_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency protecting administrative endpoints."""
    settings: Settings = request.app.state.settings
    if settings.admin_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_API_KEY is not configured on the server.",
        )
    if not tokens_match(settings.admin_api_key.get_secret_value(), x_admin_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key.")
