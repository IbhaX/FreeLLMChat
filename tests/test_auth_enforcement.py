"""Auth enforcement: when auth_required is on, /v1 app endpoints reject
unauthenticated requests and accept a valid user session token."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "auth_required", True)
    yield


def test_protected_endpoints_reject_anonymous(auth_on):
    with TestClient(app) as c:
        for path in ("/v1/models", "/v1/roleplay/rooms", "/v1/agents",
                     "/v1/skills/manage", "/v1/characters", "/v1/person/lookup",
                     "/v1/research/runs"):
            r = c.get(path)
            assert r.status_code == 401, f"{path} should require auth"
            err = r.json()["detail"]["error"]
            assert err["type"] == "authentication_required"


def test_auth_endpoints_stay_reachable(auth_on):
    """Login/register must remain open — they are the way in."""
    with TestClient(app) as c:
        r = c.get("/v1/auth/me")
        assert r.status_code == 200
        assert r.json()["auth_required"] is True
        r = c.post("/v1/auth/login",
                   json={"username": "ghost", "password": "nope12345"})
        assert r.status_code == 401


def test_register_then_access_protected(auth_on):
    """A registered user's token unlocks the app surface."""
    with TestClient(app) as c:
        r = c.post("/v1/auth/register", json={
            "username": "guard-user", "password": "password123"})
        assert r.status_code == 201
        token = r.json()["token"]
        assert r.json()["user"]["role"] in ("admin", "user")
        hdrs = {"X-User-Token": token}
        assert c.get("/v1/models", headers=hdrs).status_code == 200
        me = c.get("/v1/auth/me", headers=hdrs).json()
        assert me["user"]["username"] == "guard-user"
        # Revoked token is rejected again.
        asyncio.run(_revoke(token))
        assert c.get("/v1/models", headers=hdrs).status_code == 401


async def _revoke(token: str) -> None:
    from app.db import get_session_factory
    from app.services.auth import revoke_token
    async with get_session_factory()() as s:
        await revoke_token(s, token)
        await s.commit()


def test_public_share_route_stays_open(auth_on):
    """Share links are capabilities — deliberately unauthenticated."""
    with TestClient(app) as c:
        r = c.get("/v1/share/does-not-exist")
        assert r.status_code == 404  # not 401


def test_healthz_open(auth_on):
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
