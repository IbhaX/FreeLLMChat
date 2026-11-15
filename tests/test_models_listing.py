"""/v1/models usable-filter + pricing exposure tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

ADMIN = {"X-Admin-Token": "test-admin"}


def _ids(c: TestClient, query: str = "") -> dict[str, dict]:
    return {m["id"]: m for m in c.get(f"/v1/models{query}").json()["data"]}


def _mk_provider(c: TestClient, name: str, **kw) -> dict:
    body = {"name": name, "base_url": "http://x.test/v1",
            "default_models": ["m-a", "m-b"]}
    body.update(kw)
    r = c.post("/admin/providers", headers=ADMIN, json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestUsableFilter:
    def test_unkeyed_provider_hidden_by_usable_param(self):
        with TestClient(app) as c:
            _mk_provider(c, "nokey-prov")
            all_ids = _ids(c)
            assert "nokey-prov/m-a" in all_ids  # full catalog unaffected
            usable = _ids(c, "?usable=1")
            assert "nokey-prov/m-a" not in usable
            assert "nokey-prov/m-b" not in usable
            assert {"auto", "auto:coding", "auto:fast"} <= set(usable)

    def test_keyless_provider_usable_without_key(self):
        """auth_style='none' providers (e.g. Ollama) need no API key — they
        must show as usable/keyed without one."""
        with TestClient(app) as c:
            _mk_provider(c, "keyless-prov", auth_style="none")
            usable = _ids(c, "?usable=1")
            assert "keyless-prov/m-a" in usable
            assert usable["keyless-prov/m-a"]["usable"] is True
            assert usable["keyless-prov/m-a"]["keyed"] is True

    def test_provider_appears_after_key_added(self):
        with TestClient(app) as c:
            _mk_provider(c, "keyed-later")
            assert "keyed-later/m-a" not in _ids(c, "?usable=1")
            r = c.post("/admin/keys", headers=ADMIN,
                       json={"provider": "keyed-later", "keys": ["sk-t-9999"]})
            assert r.status_code == 201
            usable = _ids(c, "?usable=1")
            assert "keyed-later/m-a" in usable
            assert usable["keyed-later/m-a"]["usable"] is True
            assert usable["keyed-later/m-a"]["keyed"] is True

    def test_disabled_key_makes_provider_unusable(self):
        with TestClient(app) as c:
            _mk_provider(c, "diskey-prov")
            c.post("/admin/keys", headers=ADMIN,
                   json={"provider": "diskey-prov", "keys": ["sk-t-8888"]})
            assert "diskey-prov/m-a" in _ids(c, "?usable=1")
            key = c.get("/admin/keys?provider=diskey-prov",
                        headers=ADMIN).json()[0]
            c.patch(f"/admin/keys/{key['id']}", headers=ADMIN,
                    json={"enabled": False})
            assert "diskey-prov/m-a" not in _ids(c, "?usable=1")
            # Unfiltered catalog still lists it with usable=False.
            entry = _ids(c)["diskey-prov/m-a"]
            assert entry["usable"] is False


class TestPricing:
    def test_pricing_fields_present(self):
        with TestClient(app) as c:
            _mk_provider(c, "priced-prov", pricing={
                "input_per_mtok": 0.5, "output_per_mtok": 1.5,
                "currency": "USD"})
            entry = _ids(c)["priced-prov/m-a"]
            assert entry["pricing"]["input_per_mtok"] == 0.5
            assert entry["pricing"]["output_per_mtok"] == 1.5
            assert entry["pricing"]["currency"] == "USD"

    def test_model_overrides_win(self):
        with TestClient(app) as c:
            _mk_provider(c, "ovr-prov", pricing={
                "input_per_mtok": 1.0, "output_per_mtok": 2.0,
                "model_overrides": {
                    "m-b": {"input_per_mtok": 0.0, "output_per_mtok": 0.0}}})
            data = _ids(c)
            assert data["ovr-prov/m-a"]["pricing"]["input_per_mtok"] == 1.0
            assert data["ovr-prov/m-b"]["pricing"]["input_per_mtok"] == 0.0
            assert "model_overrides" not in data["ovr-prov/m-b"]["pricing"]

    def test_no_pricing_is_null(self):
        with TestClient(app) as c:
            _mk_provider(c, "noprice-prov")
            assert _ids(c)["noprice-prov/m-a"]["pricing"] is None

    def test_model_detail_includes_pricing(self):
        with TestClient(app) as c:
            _mk_provider(c, "detail-prov", pricing={
                "input_per_mtok": 0.1, "output_per_mtok": 0.2})
            r = c.get("/v1/models/detail-prov/m-a")
            assert r.status_code == 200
            assert r.json()["pricing"]["output_per_mtok"] == 0.2

    def test_ollama_seeded_free(self):
        with TestClient(app) as c:
            data = _ids(c, "?usable=1")
            ollama = [m for m in data.values()
                      if m["id"].startswith("ollama/")]
            for m in ollama:
                assert m["usable"] is True
                assert m["pricing"]["input_per_mtok"] == 0.0


def test_ensure_collections_runs_once(monkeypatch):
    """ensure_collections must not hit the remote API on every request —
    list_collection_names runs once per process."""
    from app.services import astra_memory

    calls = []

    class FakeDB:
        def list_collection_names(self):
            calls.append(1)
            return ["mem_short", "mem_long"]

    monkeypatch.setattr(astra_memory, "_ensured", False)
    monkeypatch.setattr(astra_memory, "_db", lambda: FakeDB())
    astra_memory.ensure_collections()
    astra_memory.ensure_collections()
    assert len(calls) == 1
    monkeypatch.setattr(astra_memory, "_ensured", False)
