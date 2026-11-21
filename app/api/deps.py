"""Auth dependencies for /v1 and /admin surfaces."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)


async def require_admin(request: Request) -> None:
    """/admin requires ADMIN_TOKEN via bearer/X-Admin-Token, OR a
    signed-in user whose role is ``admin`` — one credential system for
    the UI, token kept for external tooling."""
    expected = get_settings().admin_token
    token = request.headers.get("x-admin-token")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if token and secrets.compare_digest(token, expected):
        return
    user = await optional_user(request)
    if user is not None and user.role == "admin":
        return
    if user is None:
        # No session at all → the UI should send them to /login.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Authentication required",
                              "type": "authentication_required"}},
        )
    # Signed in but not an admin → 403 so the UI can route them to a
    # usable page instead of looping through /login.
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"message": "Admin role required",
                          "type": "admin_required"}},
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


async def require_user(request: Request) -> "User | None":
    """Like optional_user but 401s when auth is required and no valid
    token is present. The ``authentication_required`` error type lets the
    web UI distinguish a login redirect from other 401s."""
    from app.services.auth import auth_required
    user = await optional_user(request)
    if user is None and auth_required():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Authentication required",
                              "type": "authentication_required"}},
        )
    return user


async def require_user_or_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> "User | None":
    """Read-only /admin surface: any signed-in user or ADMIN_TOKEN tooling
    qualifies; anonymous works when auth is off. Writes stay behind
    :func:`require_admin`."""
    from app.services.auth import auth_required
    user = await optional_user(request)
    if user is not None:
        return user
    settings = get_settings()
    token = request.headers.get("x-admin-token") or (
        creds.credentials if creds is not None else None)
    if token:
        if settings.admin_token and secrets.compare_digest(
                token, settings.admin_token):
            return None
    if not auth_required():
        return None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"message": "Authentication required",
                          "type": "authentication_required"}},
    )


def subject_for(user: "User | None") -> str:
    """Metering subject string for a (possibly anonymous) request."""
    return f"u{user.id}" if user else "anon"
