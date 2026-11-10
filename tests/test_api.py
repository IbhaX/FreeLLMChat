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


def test_undecryptable_key_flagged():
    with TestClient(app) as c:
        c.post("/admin/keys", headers=ADMIN,
               json={"provider": "groq", "keys": ["gsk_ok_1234"]})
        # Corrupt the stored ciphertext to simulate an ENCRYPTION_KEY change.
        from sqlalchemy import select
        from app.db import get_session_factory
        from app.models import ApiKey
        import asyncio

        async def corrupt():
            async with get_session_factory()() as s:
                k = (await s.execute(select(ApiKey))).scalars().first()
                k.encrypted_key = "gAAAAAB" + "x" * 100
                await s.commit()

        asyncio.get_event_loop().run_until_complete(corrupt())
        keys = c.get("/admin/keys?provider=groq", headers=ADMIN).json()
        assert keys[0]["decryptable"] is False
        assert keys[0]["healthy"] is False


def test_tool_policy_prepends_system_message():
    from app.services.tools import _with_tool_policy
    out = _with_tool_policy([{"role": "user", "content": "hi"}])
    assert out[0]["role"] == "system"
    assert "Tool use policy" in out[0]["content"]
    assert out[1]["content"] == "hi"


def test_tool_policy_appends_to_existing_system():
    from app.services.tools import _with_tool_policy
    out = _with_tool_policy(
        [{"role": "system", "content": "Persona."}, {"role": "user", "content": "hi"}]
    )
    assert out[0]["content"].startswith("Persona.")
    assert "Tool use policy" in out[0]["content"]
    assert len(out) == 2


def test_keys_test_all(monkeypatch):
    import app.api.admin_routes as ar

    class FakeAdapter:
        def __init__(self, provider):
            self.provider = provider

        async def chat_completion(self, client, api_key, payload):
            if "bad" in api_key:
                raise RuntimeError("401 invalid api key")
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ar, "build_adapter", lambda provider: FakeAdapter(provider))
    with TestClient(app) as c:
        c.post("/admin/keys", headers=ADMIN, json={
            "provider": "groq", "keys": ["gsk_good_key_0001"], "label_prefix": "t"})
        c.post("/admin/keys", headers=ADMIN, json={
            "provider": "groq", "keys": ["gsk_bad_key_0002"], "label_prefix": "t"})
        r = c.post("/admin/keys/test-all", headers=ADMIN)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["tested"] >= 2 and data["ok"] >= 1 and data["failed"] >= 1
        by_hint = {k["key_hint"]: k for k in data["results"]}
        assert by_hint["0001"]["ok"] is True
        assert by_hint["0001"]["model_used"]
        assert by_hint["0002"]["ok"] is False
        assert "401" in by_hint["0002"]["error"]
        assert "provider" in by_hint["0001"] and "latency_ms" in by_hint["0001"]


def test_keys_test_all_prunes_dead_model(monkeypatch):
    import app.api.admin_routes as ar

    class DeadModel(Exception):
        status_code = 404

    class FakeAdapter:
        def __init__(self, provider):
            self.provider = provider

        async def chat_completion(self, client, api_key, payload):
            raise DeadModel("404 This model is no longer available")

    monkeypatch.setattr(ar, "build_adapter", lambda provider: FakeAdapter(provider))
    with TestClient(app) as c:
        c.post("/admin/keys", headers=ADMIN, json={
            "provider": "gemini", "keys": ["AIza_dead_model_key"], "label_prefix": "t"})
        r = c.post("/admin/keys/test-all", headers=ADMIN)
        assert r.status_code == 200, r.text
        assert r.json()["failed"] >= 1
        models = c.get("/v1/models").json()["data"]
        gemini_models = [m["id"] for m in models if m["id"].startswith("gemini/")]
        # The dead default model was pruned from the catalog.
        assert "gemini/gemini-3.6-flash" not in gemini_models
