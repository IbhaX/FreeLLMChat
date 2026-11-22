"""Connectors API — per-user external-service credentials.

Connectors are strictly user-scoped: each signed-in account stores its own
tokens (Fernet-encrypted at rest) and connector tools resolve the calling
user's config at run time. Raw secret values never leave the server — list
responses carry field hints (…1234) only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_user
from app.models import User
from app.services import connectors

router = APIRouter(prefix="/v1/connectors")


def _user_or_401(user: User | None) -> User:
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Sign in to manage connectors — "
                              "credentials are per-account, not shared.",
                              "type": "authentication_required"}})
    return user


@router.get("")
async def list_connectors(user: User | None = Depends(require_user)) -> dict[str, Any]:
    """Available connector specs + which ones this user has configured."""
    u = _user_or_401(user)
    return {
        "connectors": connectors.spec_dicts(),
        "configured": await connectors.configured(u.id),
    }


@router.put("/{service}")
async def save_connector(
    service: str,
    body: dict[str, Any],
    user: User | None = Depends(require_user),
) -> dict[str, Any]:
    """Store (or replace) this user's credentials for ``service``."""
    u = _user_or_401(user)
    try:
        cfg = await connectors.save(u.id, service, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "service": service, "fields": sorted(cfg)}


@router.post("/{service}/test")
async def test_connector(
    service: str,
    user: User | None = Depends(require_user),
) -> dict[str, Any]:
    """Live-check the user's stored credentials with a lightweight call."""
    u = _user_or_401(user)
    cfg = await connectors.config_for_user(u.id, service)
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"connect {service} first — no credentials stored")
    try:
        msg = await connectors.probe(service, cfg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "message": msg}


@router.delete("/{service}")
async def delete_connector(
    service: str,
    user: User | None = Depends(require_user),
) -> dict[str, Any]:
    """Remove this user's credentials for ``service``."""
    u = _user_or_401(user)
    removed = await connectors.remove(u.id, service)
    return {"ok": removed}
