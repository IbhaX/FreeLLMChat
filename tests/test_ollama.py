"""Ollama adapter + auto-scan integration tests."""

from __future__ import annotations

import httpx
import pytest

from app.models import ApiKey, ModelCache, Provider
from app.providers.ollama import (
    OllamaAdapter,
    candidate_hosts,
    normalize_host,
)
from app.services import ollama as svc


def _provider(base_url: str = "http://localhost:11434/v1") -> Provider:
    p = Provider(name="ollama", adapter="ollama", base_url=base_url,
                 auth_style="none")
    return p


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestNormalizeHost:
    def test_plain_host(self):
        assert normalize_host("myserver:11434") == "http://myserver:11434"

    def test_full_url(self):
        assert normalize_host("http://x:11434/") == "http://x:11434"

    def test_https(self):
        assert normalize_host("https://ollama.example.com") == \
            "https://ollama.example.com"

    def test_candidate_hosts_env_first(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "gpu-box:11434")
        hosts = candidate_hosts()
        assert hosts[0] == "http://gpu-box:11434"
        assert "http://localhost:11434" in hosts
        assert len(hosts) == len(set(hosts))


_TAGS = {
    "models": [
        {
            "name": "llama3.2:latest",
            "model": "llama3.2:latest",
            "size": 2019393189,
            "modified_at": "2026-11-10T12:00:00Z",
            "digest": "a80c4f17acd5",
            "details": {"parameter_size": "3.2B",
                        "quantization_level": "Q4_K_M"},
        },
        {
            "name": "qwen2.5-coder:7b",
            "size": 4683073694,
            "modified_at": "2026-11-11T08:00:00Z",
            "details": {"parameter_size": "7.6B",
                        "quantization_level": "Q4_K_M"},
        },
    ]
}


class TestAdapter:
    @pytest.mark.asyncio
    async def test_list_models_reads_api_tags(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/tags"
            return httpx.Response(200, json=_TAGS)

        adapter = OllamaAdapter(_provider())
        models = await adapter.list_models(_mock_client(handler), "ignored")
        assert models == ["llama3.2:latest", "qwen2.5-coder:7b"]

    @pytest.mark.asyncio
    async def test_list_models_down_returns_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = OllamaAdapter(_provider())
        assert await adapter.list_models(_mock_client(handler), "x") == []

    @pytest.mark.asyncio
    async def test_chat_uses_openai_v1(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={
                "id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": "hi"},
                    "finish_reason": "stop"}],
            })

        adapter = OllamaAdapter(_provider())
        out = await adapter.chat_completion(
            _mock_client(handler), "placeholder", {"model": "llama3.2"}
        )
        assert seen["path"] == "/v1/chat/completions"
        assert seen["auth"] is None  # keyless: no auth header sent
        assert out["choices"][0]["message"]["content"] == "hi"

    def test_native_base_strips_v1(self):
        adapter = OllamaAdapter(_provider("http://gpu:11434/v1"))
        assert adapter.native_base_url() == "http://gpu:11434"


class TestProbe:
    @pytest.mark.asyncio
    async def test_reachable_host(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json=_TAGS)
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.5.4"})
            return httpx.Response(404)

        scan = await svc.probe_host("localhost:11434",
                                    client=_mock_client(handler))
        assert scan.reachable is True
        assert scan.host == "http://localhost:11434"
        assert scan.version == "0.5.4"
        assert [m.name for m in scan.models] == [
            "llama3.2:latest", "qwen2.5-coder:7b"]
        assert scan.models[0].parameter_size == "3.2B"
        assert scan.models[0].size_bytes == 2019393189

    @pytest.mark.asyncio
    async def test_unreachable_host(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        scan = await svc.probe_host("http://localhost:11434",
                                    client=_mock_client(handler))
        assert scan.reachable is False
        assert scan.models == ()

    @pytest.mark.asyncio
    async def test_scan_orders_reachable_first(self, monkeypatch):
        async def fake_probe(host, client=None):
            reachable = "good" in host
            return svc.OllamaHostScan(host=host, reachable=reachable)

        monkeypatch.setattr(svc, "probe_host", fake_probe)
        monkeypatch.setattr(svc, "candidate_hosts",
                            lambda: ["http://bad:11434", "http://good:11434"])
        scans = await svc.scan_ollama_hosts()
        assert scans[0].host == "http://good:11434"
        assert scans[0].reachable is True


class TestSync:
    @pytest.mark.asyncio
    async def test_sync_creates_provider_cache_and_key(self, session):
        scan = svc.OllamaHostScan(
            host="http://localhost:11434", reachable=True,
            version="0.5.4",
            models=(svc.OllamaModel(name="llama3.2:latest",
                                    size_bytes=100),),
        )
        provider = await svc.sync_ollama_provider(session, scan)
        assert provider.enabled is True
        assert provider.base_url == "http://localhost:11434/v1"
        assert provider.default_models == ["llama3.2:latest"]

        from sqlalchemy import select
        keys = (await session.execute(
            select(ApiKey).where(ApiKey.provider_id == provider.id)
        )).scalars().all()
        assert len(keys) == 1
        cache = (await session.execute(
            select(ModelCache).where(ModelCache.provider_id == provider.id)
        )).scalar_one()
        assert cache.models == ["llama3.2:latest"]

    @pytest.mark.asyncio
    async def test_sync_is_idempotent(self, session):
        scan = svc.OllamaHostScan(
            host="http://localhost:11434", reachable=True,
            models=(svc.OllamaModel(name="m1"),),
        )
        await svc.sync_ollama_provider(session, scan)
        await svc.sync_ollama_provider(session, scan)
        from sqlalchemy import func, select
        n = (await session.execute(
            select(func.count()).select_from(ApiKey)
        )).scalar_one()
        assert n == 1

    @pytest.mark.asyncio
    async def test_auto_scan_disables_when_unreachable(
            self, session, monkeypatch):
        session.add(Provider(name="ollama", adapter="ollama", enabled=True,
                             base_url="http://localhost:11434/v1",
                             auth_style="none"))
        await session.commit()

        async def no_hosts():
            return [svc.OllamaHostScan(host="http://localhost:11434",
                                       reachable=False)]

        monkeypatch.setattr(svc, "scan_ollama_hosts", no_hosts)
        result = await svc.auto_scan_ollama(session)
        assert result is None
        await session.refresh(
            await svc._get_provider(session))
        provider = await svc._get_provider(session)
        assert provider.enabled is False


class TestScanEndpoint:
    def test_admin_scan_reachable(self, monkeypatch):
        from fastapi.testclient import TestClient

        from app.main import app

        scan = svc.OllamaHostScan(
            host="http://localhost:11434", reachable=True, version="0.5.4",
            models=(svc.OllamaModel(name="llama3.2:latest",
                                    size_bytes=10,
                                    parameter_size="3B"),),
        )

        async def fake_scan():
            return [scan]

        monkeypatch.setattr(svc, "scan_ollama_hosts", fake_scan)
        with TestClient(app) as c:
            r = c.post("/admin/ollama/scan",
                       headers={"X-Admin-Token": "test-admin"})
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] == "http://localhost:11434"
        assert body["models"][0]["name"] == "llama3.2:latest"
        assert body["synced"]["provider"] == "ollama"
        assert body["synced"]["enabled"] is True

    def test_admin_scan_unreachable_disables(self, monkeypatch):
        from fastapi.testclient import TestClient

        from app.main import app

        async def fake_scan():
            return [svc.OllamaHostScan(host="http://localhost:11434",
                                       reachable=False)]

        monkeypatch.setattr(svc, "scan_ollama_hosts", fake_scan)
        with TestClient(app) as c:
            r = c.post("/admin/ollama/scan",
                       headers={"X-Admin-Token": "test-admin"})
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is None
        assert body["models"] == []

    def test_admin_scan_requires_token(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as c:
            assert c.post("/admin/ollama/scan").status_code == 401
