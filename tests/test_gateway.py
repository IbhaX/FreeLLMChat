from __future__ import annotations

import httpx
import pytest
import respx

from app.gateway import Gateway, GatewayExhausted
from app.models import Provider
from tests.conftest import make_key


async def _provider(session, name: str, url: str, models: list[str]) -> Provider:
    p = Provider(name=name, base_url=url, default_models=models, limits={})
    session.add(p)
    await session.commit()
    return p


def _ok_payload(model: str) -> dict:
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


async def test_429_rotates_to_next_key(session):
    p = await _provider(session, "p1", "https://p1.example/v1", ["m1"])
    k1 = await make_key(session, p, "sk-aaa-1111", label="a")
    k2 = await make_key(session, p, "sk-bbb-2222", label="b")
    calls = {"n": 0}

    def side(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_ok_payload("m1"))

    with respx.mock:
        respx.post("https://p1.example/v1/chat/completions").mock(side_effect=side)
        gw = Gateway()
        result, provider, key, model = await gw.chat(
            session, {"model": "p1/m1", "messages": [{"role": "user", "content": "hi"}]}
        )
        await gw.close()

    assert result["model"] == "m1"
    assert key.id == k2.id
    await session.refresh(k1)
    assert k1.consecutive_failures == 1
    assert k1.cooldown_until is not None


async def test_provider_failover_when_keys_exhausted(session):
    p1 = await _provider(session, "p1", "https://p1.example/v1", ["m1"])
    p2 = await _provider(session, "p2", "https://p2.example/v1", ["m1"])
    await make_key(session, p1, "sk-aaa-1111", label="a")
    k2 = await make_key(session, p2, "sk-bbb-2222", label="b")

    with respx.mock:
        respx.post("https://p1.example/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "limit"})
        )
        respx.post("https://p2.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_payload("m1"))
        )
        gw = Gateway()
        result, provider, key, model = await gw.chat(
            session, {"model": "m1", "messages": [{"role": "user", "content": "hi"}]}
        )
        await gw.close()

    assert provider.name == "p2"
    assert key.id == k2.id


async def test_all_exhausted_raises(session):
    p = await _provider(session, "p1", "https://p1.example/v1", ["m1"])
    await make_key(session, p, "sk-aaa-1111")

    with respx.mock:
        respx.post("https://p1.example/v1/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        gw = Gateway()
        with pytest.raises(GatewayExhausted):
            await gw.chat(
                session, {"model": "p1/m1", "messages": [{"role": "user", "content": "hi"}]}
            )
        await gw.close()


async def test_stream_passthrough(session):
    p = await _provider(session, "p1", "https://p1.example/v1", ["m1"])
    k = await make_key(session, p, "sk-aaa-1111", label="a")
    chunks = [
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    with respx.mock:
        respx.post("https://p1.example/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, content=b"".join(chunks),
                headers={"content-type": "text/event-stream"},
            )
        )
        gw = Gateway()
        stream, provider, key, model = await gw.chat_stream(
            session, {"model": "p1/m1", "messages": [{"role": "user", "content": "hi"}],
                      "stream": True}
        )
        body = b"".join([c async for c in stream])
        await gw.close()

    assert b'"content":"he"' in body and b'"content":"llo"' in body
    assert provider.name == "p1"
    await session.refresh(k)
    assert k.daily_request_count == 1


async def test_stream_retries_before_first_token(session):
    p = await _provider(session, "p1", "https://p1.example/v1", ["m1"])
    await make_key(session, p, "sk-aaa-1111", label="a")
    k2 = await make_key(session, p, "sk-bbb-2222", label="b")
    calls = {"n": 0}

    def side(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200, content=b"data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with respx.mock:
        respx.post("https://p1.example/v1/chat/completions").mock(side_effect=side)
        gw = Gateway()
        stream, provider, key, model = await gw.chat_stream(
            session, {"model": "p1/m1", "messages": [{"role": "user", "content": "hi"}],
                      "stream": True}
        )
        body = b"".join([c async for c in stream])
        await gw.close()

    assert key.id == k2.id
    assert b"[DONE]" in body


async def test_auto_failover_to_next_provider(session, monkeypatch):
    """auto must try every provider in the chain, not just the first."""
    import app.routing.fallback as fb

    p1 = await _provider(session, "p1", "https://p1.example/v1", ["m1"])
    p2 = await _provider(session, "p2", "https://p2.example/v1", ["m1"])
    await make_key(session, p1, "sk-aaa-1111", label="a")
    k2 = await make_key(session, p2, "sk-bbb-2222", label="b")
    monkeypatch.setattr(fb, "load_fallback_chains",
                        lambda: {"auto": ["p1", "p2"]})

    with respx.mock:
        respx.post("https://p1.example/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "limit"})
        )
        respx.post("https://p2.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_payload("m1"))
        )
        gw = Gateway()
        result, provider, key, model = await gw.chat(
            session, {"model": "auto",
                      "messages": [{"role": "user", "content": "hi"}]}
        )
        await gw.close()

    assert provider.name == "p2"
    assert key.id == k2.id
