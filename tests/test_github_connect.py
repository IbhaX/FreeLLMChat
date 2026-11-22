"""GitHub connection contract tests.

The account must only read as *connected* after a real connect action:
``POST /v1/auth/github/connect`` (verified lookup) or the OAuth
callback. Typing a username into the profile form is a claim, never a
connection.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    get_settings().auth_required = True
    monkeypatch.setattr(get_settings(), "referral_code", "")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        get_settings().auth_required = False


def _register(c: TestClient, name: str) -> dict[str, str]:
    r = c.post("/v1/auth/register",
               json={"username": name, "password": "password123"})
    assert r.status_code == 201, r.text
    return {"X-User-Token": r.json()["token"]}


FAKE_GH = {
    "login": "octocat", "name": "The Octocat", "avatar_url": "https://a",
    "bio": "", "company": "", "location": "", "blog": "",
    "twitter_username": "", "followers": 5, "following": 1,
    "public_repos": 8, "public_gists": 1,
    "html_url": "https://github.com/octocat", "created_at": "2011-01-01",
    "repos": [],
}


def _fake_lookup(found: bool):
    async def _lookup(username: str):
        return dict(FAKE_GH) if found else None
    return _lookup


def test_typed_username_is_not_connected(client: TestClient) -> None:
    hdrs = _register(client, "gh-claim")
    r = client.patch("/v1/auth/me", headers=hdrs,
                     json={"github_username": "octocat"})
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["github_username"] == "octocat"
    assert u["github_connected"] is False


def test_verified_connect_and_disconnect(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hdrs = _register(client, "gh-connect")
    monkeypatch.setattr("app.services.github.lookup_github",
                        _fake_lookup(True))
    r = client.post("/v1/auth/github/connect", headers=hdrs,
                    json={"username": "octocat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["github_connected"] is True
    assert body["user"]["github_username"] == "octocat"
    assert body["github"]["login"] == "octocat"
    # /me reflects the connected flag.
    me = client.get("/v1/auth/me", headers=hdrs).json()["user"]
    assert me["github_connected"] is True

    r = client.post("/v1/auth/github/disconnect", headers=hdrs)
    u = r.json()["user"]
    assert u["github_connected"] is False
    assert u["github_username"] == ""


def test_connect_rejects_unknown_github_user(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hdrs = _register(client, "gh-miss")
    monkeypatch.setattr("app.services.github.lookup_github",
                        _fake_lookup(False))
    r = client.post("/v1/auth/github/connect", headers=hdrs,
                    json={"username": "not-a-real-gh-user"})
    assert r.status_code == 404
    me = client.get("/v1/auth/me", headers=hdrs).json()["user"]
    assert me["github_connected"] is False


def test_connect_rejects_malformed_username(client: TestClient) -> None:
    hdrs = _register(client, "gh-bad")
    r = client.post("/v1/auth/github/connect", headers=hdrs,
                    json={"username": "not a user!!"})
    assert r.status_code == 400


def test_connect_requires_auth(client: TestClient) -> None:
    assert client.post(
        "/v1/auth/github/connect",
        json={"username": "octocat"}).status_code == 401
    assert client.post("/v1/auth/github/disconnect").status_code == 401


def test_oauth_start_501_without_client_id(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hdrs = _register(client, "gh-oauth")
    monkeypatch.setattr(get_settings(), "github_client_id", None)
    assert client.get("/v1/auth/github/oauth/start",
                      headers=hdrs).status_code == 501


def test_oauth_start_returns_authorize_url(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    hdrs = _register(client, "gh-oauth2")
    monkeypatch.setattr(get_settings(), "github_client_id", "cid_test")
    monkeypatch.setattr(get_settings(), "github_oauth_redirect",
                        "http://test/v1/auth/github/oauth/callback")
    r = client.get("/v1/auth/github/oauth/start", headers=hdrs)
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=cid_test" in url and "state=" in url


def test_oauth_callback_rejects_bad_state(client: TestClient) -> None:
    assert client.get(
        "/v1/auth/github/oauth/callback",
        params={"code": "x", "state": "bogus"}).status_code == 400
