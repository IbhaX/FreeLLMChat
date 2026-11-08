from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

ADMIN = {"X-Admin-Token": "test-admin"}


def test_healthz():
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"status": "ok"}


def test_models_lists_auto_aliases():
    with TestClient(app) as c:
        r = c.get("/v1/models")
        ids = {m["id"] for m in r.json()["data"]}
    assert {"auto", "auto:coding", "auto:fast"} <= ids
    assert any(i.startswith("groq/") for i in ids)


def test_admin_requires_token():
    with TestClient(app) as c:
        assert c.get("/admin/providers").status_code == 401
        assert c.get("/admin/providers", headers=ADMIN).status_code == 200


def test_add_and_list_key():
    with TestClient(app) as c:
        r = c.post("/admin/keys", headers=ADMIN, json={
            "provider": "groq", "keys": ["gsk_testkey_abcd1234"],
            "label_prefix": "t"})
        assert r.status_code == 201, r.text
        assert r.json()["created"] == 1
        keys = c.get("/admin/keys?provider=groq", headers=ADMIN).json()
        assert len(keys) == 1
        assert keys[0]["key_hint"] == "1234"
        assert "encrypted_key" not in keys[0]


def test_duplicate_key_skipped():
    with TestClient(app) as c:
        c.post("/admin/keys", headers=ADMIN,
               json={"provider": "groq", "keys": ["gsk_dup_9999"]})
        r = c.post("/admin/keys", headers=ADMIN,
                   json={"provider": "groq", "keys": ["gsk_dup_9999"]})
        assert r.json()["created"] == 0
        assert r.json()["skipped"] == 1


def test_gateway_key_guard(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("GATEWAY_API_KEY", "secret")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            assert c.get("/v1/models").status_code == 401
            assert c.get(
                "/v1/models", headers={"Authorization": "Bearer secret"}
            ).status_code == 200
    finally:
        monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
        get_settings.cache_clear()
