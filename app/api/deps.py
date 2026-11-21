"""Auth dependencies for /v1 and /admin surfaces."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)


async def require_gateway_key(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """If GATEWAY_API_KEY is set, /v1 requires a matching bearer token."""
    expected = get_settings().gateway_api_key
    if not expected:
        return
    if creds is None or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid gateway API key",
                              "type": "authentication_error"}},
        )


async def require_admin(request: Request) -> None:
    """/admin requires ADMIN_TOKEN via bearer or X-Admin-Token header."""
    expected = get_settings().admin_token
    token = request.headers.get("x-admin-token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token",
        )


async def optional_user(request: Request) -> "User | None":
    """Resolve a user bearer token. Returns None when absent — endpoints
    stay usable in single-tenant local mode. The resolved user is cached
    on ``request.state`` so router-level ``require_user`` and endpoint
    params share one token lookup per request."""
    cached = getattr(request.state, "user", None)
    if cached is not None or getattr(request.state, "user_resolved", False):
        return cached
    request.state.user_resolved = True
    from app.db import get_session_factory
    from app.services.auth import bearer_token, resolve_token

    token = request.headers.get("x-user-token") or \
        bearer_token(request.headers.get("authorization"))
    if not token:
        return None
    factory = get_session_factory()
    async with factory() as session:
        user = await resolve_token(session, token)
    request.state.user = user
    return user


async def require_user(request: Request) -> "User":
    """Like optional_user but 401s when auth is required and no valid
    token is present. The ``authentication_required`` error type lets
    the web UI distinguish a login redirect from a gateway-key failure."""
    from app.services.auth import auth_required
    user = await optional_user(request)
    if user is None and auth_required():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Authentication required",
                              "type": "authentication_required"}},
        )
    return user


def subject_for(user: "User | None") -> str:
    """Metering subject string for a (possibly anonymous) request."""
    return f"u{user.id}" if user else "anon"
