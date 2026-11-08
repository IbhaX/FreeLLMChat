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
