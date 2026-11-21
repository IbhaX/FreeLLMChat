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


def _register(c: TestClient, username: str) -> str:
    r = c.post("/v1/auth/register", json={
        "username": username, "password": "password123"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


async def _set_role(username: str, role: str) -> None:
    from sqlalchemy import update
    from app.db import get_session_factory
    from app.models import User
    async with get_session_factory()() as s:
        await s.execute(update(User).where(
            User.username == username).values(role=role))
        await s.commit()


def test_admin_endpoints_accept_admin_role_user(auth_on):
    """An admin-role user's session satisfies /admin without ADMIN_TOKEN —
    the UI needs no second password."""
    with TestClient(app) as c:
        token = _register(c, "site-admin")
        asyncio.run(_set_role("site-admin", "admin"))
        assert c.get("/admin/providers").status_code == 401
        r = c.get("/admin/providers", headers={"X-User-Token": token})
        assert r.status_code == 200


def test_admin_endpoints_reject_regular_user(auth_on):
    """A signed-in non-admin user may read dashboard data but writes get
    403 admin_required — the UI routes them to a usable page instead of
    looping /login."""
    with TestClient(app) as c:
        token = _register(c, "plain-user")
        headers = {"X-User-Token": token}
        # Dashboard reads are open to any signed-in user.
        for path in ("/admin/providers", "/admin/stats", "/admin/tools"):
            r = c.get(path, headers=headers)
            assert r.status_code == 200, path
        # Mutations still require the admin role.
        for path in ("/admin/providers/x", ):
            r = c.patch(path, json={}, headers=headers)
            assert r.status_code == 403, path
            assert r.json()["detail"]["error"]["type"] == "admin_required"


def test_user_session_is_the_only_credential(auth_on):
    """User sessions unlock /v1; anonymous and arbitrary bearer tokens do
    not — there is no shared gateway key anymore."""
    with TestClient(app) as c:
        token = _register(c, "session-user")
        r = c.get("/v1/models", headers={"X-User-Token": token})
        assert r.status_code == 200
        assert c.get("/v1/models").status_code == 401
        r = c.get("/v1/models", headers={
            "Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401
        # The user token also works as an OpenAI-style bearer for tooling.
        r = c.get("/v1/models", headers={
            "Authorization": f"Bearer {token}"})
        assert r.status_code == 200


def test_admin_endpoints_anonymous_gets_401(auth_on):
    """No session → 401 authentication_required → UI redirects to /login."""
    with TestClient(app) as c:
        r = c.get("/admin/providers")
        assert r.status_code == 401
        assert r.json()["detail"]["error"]["type"] == "authentication_required"


def test_admin_token_still_works(auth_on):
    """External tooling can keep using ADMIN_TOKEN directly."""
    with TestClient(app) as c:
        r = c.get("/admin/providers", headers={"X-Admin-Token": "test-admin"})
        assert r.status_code == 200


def test_admin_emails_env_grants_admin_role(
        auth_on, monkeypatch: pytest.MonkeyPatch):
    """ADMIN_EMAILS is authoritative: matching accounts are admin at
    registration AND promoted on login if created earlier."""
    monkeypatch.setattr(
        get_settings(), "admin_emails", "boss@example.com, other@example.com")
    with TestClient(app) as c:
        r = c.post("/v1/auth/register", json={
            "username": "boss-user", "password": "password123",
            "email": "Boss@Example.com"})
        assert r.status_code == 201, r.text
        assert r.json()["user"]["role"] == "admin"

    # A user registered *before* the env was set gets promoted at login.
    monkeypatch.setattr(get_settings(), "admin_emails", "")
    with TestClient(app) as c:
        r = c.post("/v1/auth/register", json={
            "username": "late-user", "password": "password123",
            "email": "late@example.com"})
        assert r.status_code == 201, r.text
        if r.json()["user"]["role"] != "user":
            pytest.skip("first-account install admin — no plain user to test")
    monkeypatch.setattr(get_settings(), "admin_emails", "late@example.com")
    with TestClient(app) as c:
        r = c.post("/v1/auth/login", json={
            "username": "late@example.com", "password": "password123"})
        assert r.status_code == 200, r.text
        hdrs = {"X-User-Token": r.json()["token"]}
        assert c.get("/v1/auth/me", headers=hdrs
                     ).json()["user"]["role"] == "admin"


def test_register_rejected_without_referral(
        auth_on, monkeypatch: pytest.MonkeyPatch):
    """REFERRAL_CODE configured → registration is invite-only."""
    monkeypatch.setattr(get_settings(), "referral_code", "sekret")
    with TestClient(app) as c:
        assert c.get("/v1/auth/me").json()["referral_required"] is True
        for body in ({}, {"referral_code": "wrong"}):
            r = c.post("/v1/auth/register", json={
                "username": "ref-user", "password": "password123", **body})
            assert r.status_code == 403, body
        r = c.post("/v1/auth/register", json={
            "username": "ref-user", "password": "password123",
            "referral_code": "sekret"})
        assert r.status_code == 201, r.text


def test_register_open_when_referral_unset(auth_on):
    """No REFERRAL_CODE → registration stays open (current behavior)."""
    with TestClient(app) as c:
        assert c.get("/v1/auth/me").json()["referral_required"] is False
        r = c.post("/v1/auth/register", json={
            "username": "open-user", "password": "password123"})
        assert r.status_code == 201, r.text


def _stub_google(monkeypatch: pytest.MonkeyPatch,
                 email: str = "Goog@Example.com") -> None:
    """Bypass the remote tokeninfo call — unit-scope only."""
    import app.api.auth_routes as ar

    async def _claims(token: str, cid: str) -> dict:
        return {"email": email, "name": "Goog User", "sub": "1"}

    monkeypatch.setattr(ar, "_verify_google_id_token", _claims)
    monkeypatch.setattr(get_settings(), "google_client_id", "cid-123")


def test_google_sign_in_creates_and_links(
        auth_on, monkeypatch: pytest.MonkeyPatch):
    _stub_google(monkeypatch)
    with TestClient(app) as c:
        r = c.post("/v1/auth/google", json={"credential": "tok"})
        assert r.status_code == 200, r.text
        uid = r.json()["user"]["id"]
        assert r.json()["user"]["email"] == "goog@example.com"
        # Second sign-in with a different-cased email links the same account.
        monkeypatch.undo()
        _stub_google(monkeypatch, email="GOOG@example.com")
        r2 = c.post("/v1/auth/google", json={"credential": "tok"})
        assert r2.status_code == 200
        assert r2.json()["user"]["id"] == uid


def test_google_sign_in_respects_referral(
        auth_on, monkeypatch: pytest.MonkeyPatch):
    _stub_google(monkeypatch, email="invite-only@x.com")
    monkeypatch.setattr(get_settings(), "referral_code", "sekret")
    with TestClient(app) as c:
        r = c.post("/v1/auth/google", json={"credential": "tok"})
        assert r.status_code == 403
        r = c.post("/v1/auth/google", json={
            "credential": "tok", "referral_code": "sekret"})
        assert r.status_code == 201 or r.status_code == 200


def test_google_sign_in_unconfigured_404(
        auth_on, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    with TestClient(app) as c:
        r = c.post("/v1/auth/google", json={"credential": "tok"})
        assert r.status_code == 404


def test_profile_update_and_github(
        auth_on, monkeypatch: pytest.MonkeyPatch):
    """PATCH /v1/auth/me persists profile fields; /v1/auth/github returns
    the linked account's public profile (lookup stubbed at the boundary)."""
    with TestClient(app) as c:
        token = _register(c, "prof-user")
        hdrs = {"X-User-Token": token}
        assert c.patch("/v1/auth/me", json={"bio": "x"}).status_code == 401
        r = c.patch("/v1/auth/me", headers=hdrs, json={
            "display_name": "Prof User", "bio": "hello",
            "github_username": "@octocat",
            "avatar_url": "https://a.b/c.png"})
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["bio"] == "hello"
        assert u["github_username"] == "octocat"
        assert u["avatar_url"] == "https://a.b/c.png"
        # A full GitHub URL normalizes to the login.
        r = c.patch("/v1/auth/me", headers=hdrs,
                    json={"github_username": "https://github.com/octocat/"})
        assert r.json()["user"]["github_username"] == "octocat"
        assert c.patch("/v1/auth/me", headers=hdrs,
                       json={"avatar_url": "javascript:x"}
                       ).status_code == 400

        async def fake_lookup(username: str):
            assert username == "octocat"
            return {"login": "octocat", "public_repos": 8, "repos": []}

        monkeypatch.setattr(
            "app.services.github.lookup_github", fake_lookup)
        r = c.get("/v1/auth/github", headers=hdrs)
        assert r.status_code == 200
        assert r.json()["github"]["login"] == "octocat"

        token2 = _register(c, "nogit-user")
        assert c.get("/v1/auth/github",
                     headers={"X-User-Token": token2}).status_code == 400
        assert c.get("/v1/auth/github").status_code == 401


def test_login_accepts_email(auth_on):
    """Sign-in identifies by username OR email; only password is required."""
    with TestClient(app) as c:
        assert c.post("/v1/auth/register", json={
            "username": "email-user", "password": "password123",
            "email": "EmailUser@Example.com"}).status_code == 201
        r = c.post("/v1/auth/login", json={
            "username": "emailuser@example.com", "password": "password123"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["username"] == "email-user"
        # Referral code is irrelevant to login.
        r = c.post("/v1/auth/login", json={
            "username": "email-user", "password": "password123"})
        assert r.status_code == 200
