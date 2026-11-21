from __future__ import annotations

import httpx
import pytest
import respx

from app.gateway import Gateway
from app.models import Provider
from app.providers.ai_horde import AIHordeAdapter
from app.providers.registry import load_fallback_chains, load_provider_seed
from tests.conftest import make_key


def _provider() -> Provider:
    return Provider(
        name="ai-horde",
        display_name="AI Horde",
        adapter="ai_horde",
        base_url="https://oai.aihorde.net/v1",
        auth_style="none",
        auth_header="Authorization",
        extra_headers={},
        default_models=[],
        limits={},
    )


def test_keyless_placeholder_uses_documented_anonymous_token():
    adapter = AIHordeAdapter(_provider())
    assert adapter.build_headers("ollama-local") == {
        "Authorization": "Bearer 0000000000"
    }


def test_registered_horde_key_is_forwarded_unchanged():
    adapter = AIHordeAdapter(_provider())
    assert adapter.build_headers("user-horde-key") == {
        "Authorization": "Bearer user-horde-key"
    }


@pytest.mark.asyncio
async def test_model_discovery_reads_live_openai_model_ids():
    adapter = AIHordeAdapter(_provider())
    with respx.mock:
        respx.get("https://oai.aihorde.net/v1/models").mock(
            return_value=httpx.Response(200, json={
                "object": "list",
                "data": [{"id": "worker/model-a"}, {"id": "worker/model-b"}],
            })
        )
        async with httpx.AsyncClient() as client:
            models = await adapter.list_models(client, "ollama-local")

    assert models == ["worker/model-a", "worker/model-b"]


def test_horde_is_last_hosted_auto_fallback_and_local_ollama_is_not_auto():
    seed = load_provider_seed()
    horde = next(p for p in seed["providers"] if p["name"] == "ai-horde")
    assert horde["base_url"] == "https://oai.aihorde.net/v1"
    assert horde["auth_style"] == "none"
    assert horde["default_models"] == []  # live catalog only

    chains = load_fallback_chains()
    assert chains["auto"][-1] == "ai-horde"
    assert all("ollama" not in chain for chain in chains.values())


async def test_auto_fails_over_to_horde_with_anonymous_auth(session, monkeypatch):
    import app.routing.fallback as fallback

    first = Provider(
        name="first", base_url="https://first.example/v1",
        default_models=["first-model"], limits={},
    )
    horde = _provider()
    horde.default_models = ["worker/model-a"]
    session.add_all([first, horde])
    await session.commit()
    await make_key(session, first, "first-key")
    await make_key(session, horde, "ollama-local")
    monkeypatch.setattr(
        fallback, "load_fallback_chains",
        lambda: {"auto": ["first", "ai-horde"]},
    )

    success = {
        "id": "chatcmpl-horde",
        "object": "chat.completion",
        "model": "worker/model-a",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "from Horde"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    with respx.mock:
        respx.get("https://first.example/v1/models").mock(
            return_value=httpx.Response(404)
        )
        respx.get("https://oai.aihorde.net/v1/models").mock(
            return_value=httpx.Response(200, json={
                "object": "list", "data": [{"id": "worker/model-a"}],
            })
        )
        respx.post("https://first.example/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "rate limited"})
        )
        horde_request = respx.post(
            "https://oai.aihorde.net/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=success))
        gateway = Gateway()
        try:
            result, provider, _key, model = await gateway.chat(
                session, {
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        finally:
            await gateway.close()

    assert provider.name == "ai-horde"
    assert model == "worker/model-a"
    assert result["choices"][0]["message"]["content"] == "from Horde"
    assert horde_request.calls.last.request.headers["authorization"] == (
        "Bearer 0000000000"
    )
